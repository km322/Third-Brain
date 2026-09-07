#!/usr/bin/env node
/**
 * Builds the public Third Brain project site - the marketing landing page and the docs
 * page - as plain static files under `apps/web/out`. No server, no runtime: the output
 * can be served by any file server (see docs/WEBSITE.md).
 *
 * Why it stages a copy first. `output: "export"` exports every route it finds, and this
 * app also contains the dashboard and the auth pages. Those must not be published: they
 * are dead shells without an API, and `app/dashboard/collections/[id]` cannot be
 * exported at all without a `generateStaticParams`. So the build copies apps/web into a
 * temp directory, drops those two route trees from the copy, and runs `next build`
 * there with STATIC_EXPORT=1. The checkout is never modified, nothing is restored
 * afterwards, and two builds can run at once without racing.
 *
 * Usage:
 *   npm run build:site
 *   NEXT_PUBLIC_SITE_URL=https://example.org npm run build:site
 */
import { spawnSync } from "node:child_process";
import fs from "node:fs/promises";
import { createRequire } from "node:module";
import os from "node:os";
import path from "node:path";

const WEB_DIR = path.resolve(import.meta.dirname, "..");
const OUT_DIR = path.join(WEB_DIR, "out");

/** Absolute base for the canonical and Open Graph URLs. Override per deployment. */
const DEFAULT_SITE_URL = "https://third-brain.ai";

/** Paths, relative to apps/web, that are left out of the staged copy. */
const EXCLUDED = new Set([
  // Routes the public site must not publish: both need a running API behind them.
  "app/(auth)",
  "app/dashboard",
  // Build output, test artifacts, and the dependency tree (symlinked in instead).
  ".next",
  "out",
  "node_modules",
  "blob-report",
  "playwright-report",
  "test-results",
  "tsconfig.tsbuildinfo",
]);

/**
 * The complete set of HTML files the site is allowed to publish. Checked after every
 * build so a new route can never quietly ship a page that needs an API behind it; add
 * an entry here deliberately when the site gains a page. Next emits the not-found page
 * twice under `trailingSlash`, once at each spelling.
 */
const PUBLISHED_PAGES = new Set([
  "index.html",
  "docs/index.html",
  "404.html",
  "404/index.html",
]);

/**
 * Per-path response headers, in the `_headers` format Cloudflare Pages and Netlify read
 * from the root of the published directory. Any other file server ignores the file (see
 * docs/WEBSITE.md for the nginx equivalent), so the site is correct without it.
 *
 * Three jobs: reinstate the baseline hardening headers that next.config.mjs applies in
 * the app build but `output: "export"` cannot; freeze the demo renditions at the edge so
 * the CDN does not re-stream megabytes on every play; and label the Open Graph card,
 * which Next exports as an extensionless file that a static host would otherwise serve
 * as application/octet-stream, so link previews break.
 */
const HEADERS_FILE = `/*
  X-Frame-Options: DENY
  X-Content-Type-Options: nosniff
  Referrer-Policy: strict-origin-when-cross-origin
  Content-Security-Policy: frame-ancestors 'none'
  Permissions-Policy: camera=(), microphone=(), geolocation=()
  X-DNS-Prefetch-Control: off

/media/*
  Cache-Control: public, max-age=31536000, immutable

/opengraph-image
  Content-Type: image/png
`;

/** Normalize a path to forward slashes so the sets above read the same on any OS. */
function toPosix(relativePath) {
  return relativePath.split(path.sep).join("/");
}

async function stageSource(staging) {
  await fs.cp(WEB_DIR, staging, {
    recursive: true,
    filter: (src) => {
      const rel = toPosix(path.relative(WEB_DIR, src));
      if (rel === "") return true;
      // Local env files carry per-machine settings and must not reach a published build.
      if (path.basename(rel).startsWith(".env")) return false;
      return !EXCLUDED.has(rel);
    },
  });
  await fs.symlink(
    path.join(WEB_DIR, "node_modules"),
    path.join(staging, "node_modules"),
    "dir",
  );
}

function runNextBuild(staging) {
  const nextBin = createRequire(import.meta.url).resolve("next/dist/bin/next");
  const { status } = spawnSync(process.execPath, [nextBin, "build"], {
    cwd: staging,
    stdio: "inherit",
    env: {
      ...process.env,
      STATIC_EXPORT: "1",
      NEXT_TELEMETRY_DISABLED: "1",
      NEXT_PUBLIC_SITE_URL: process.env.NEXT_PUBLIC_SITE_URL || DEFAULT_SITE_URL,
    },
  });
  if (status !== 0) {
    throw new Error(`next build exited with code ${status}`);
  }
}

async function assertPublishablePages(exportDir) {
  const entries = await fs.readdir(exportDir, { recursive: true });
  const pages = entries.filter((e) => e.endsWith(".html")).map(toPosix).sort();

  const unexpected = pages.filter((p) => !PUBLISHED_PAGES.has(p));
  if (unexpected.length > 0) {
    throw new Error(
      `static export emitted unpublishable pages: ${unexpected.join(", ")}`,
    );
  }
  for (const required of ["index.html", "docs/index.html"]) {
    if (!pages.includes(required)) {
      throw new Error(`static export is missing ${required}`);
    }
  }
  return pages;
}

const staging = await fs.mkdtemp(path.join(os.tmpdir(), "third-brain-site-"));
try {
  await stageSource(staging);
  runNextBuild(staging);

  const exportDir = path.join(staging, "out");
  await fs.writeFile(path.join(exportDir, "_headers"), HEADERS_FILE);
  const pages = await assertPublishablePages(exportDir);

  await fs.rm(OUT_DIR, { recursive: true, force: true });
  await fs.cp(exportDir, OUT_DIR, { recursive: true });
  await fs.rm(staging, { recursive: true, force: true });

  console.log(`\nStatic site written to ${OUT_DIR}`);
  console.log(`Pages: ${pages.join(", ")}`);
} catch (error) {
  console.error(`\nSite build failed. Staged copy left at ${staging} for inspection.`);
  console.error(error.message);
  process.exit(1);
}
