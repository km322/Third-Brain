import { readFileSync } from "node:fs";

const pkg = JSON.parse(readFileSync(new URL("./package.json", import.meta.url), "utf8"));

/**
 * Static-site mode: `scripts/build-site.mjs` sets STATIC_EXPORT=1 to build the public
 * project site (the marketing landing page and the docs page) as plain HTML with no
 * server behind it. Everything else - `npm run build`, the Docker image, CI - leaves it
 * unset and gets the unchanged standalone app build.
 */
const STATIC_SITE = process.env.STATIC_EXPORT === "1";

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  /**
   * Inlined into the client bundle at build time: the app version comes from
   * package.json, the commit from the GIT_COMMIT build arg (empty in dev).
   */
  env: {
    NEXT_PUBLIC_APP_VERSION: pkg.version,
    NEXT_PUBLIC_GIT_COMMIT: process.env.GIT_COMMIT || "",
    /**
     * Lets the marketing components drop the affordances that need a running app
     * behind them (see components/marketing/links.ts).
     */
    NEXT_PUBLIC_STATIC_SITE: STATIC_SITE ? "1" : "",
  },
  /**
   * Emit a self-contained server bundle (.next/standalone/server.js) so the
   * production Docker image can run `node server.js` without node_modules. The
   * static site build emits plain files into out/ instead.
   */
  output: STATIC_SITE ? "export" : "standalone",
  /**
   * Pin file tracing to this app so stray lockfiles outside the repo cannot
   * change the standalone output layout.
   */
  outputFileTracingRoot: import.meta.dirname,
  eslint: { ignoreDuringBuilds: true },
  /**
   * Export every route as `<route>/index.html` so any plain file server resolves
   * `/docs` without needing clean-URL rules. No effect on the app build.
   */
  trailingSlash: STATIC_SITE,
};

if (!STATIC_SITE) {
  /**
   * Response headers, registered for the app build only.
   *
   * `headers` is served by the Next server, which the static site does not have -
   * `output: "export"` rejects it. A static host applies its own headers instead
   * (see docs/WEBSITE.md), so they are only registered for the app build.
   *
   * `/:path*` gets baseline hardening headers on every response. Deliberately NOT a
   * full script-src CSP (Next relies on inline/hashed scripts; a strict policy would
   * need per-build nonces and risks breaking pages) - these cover clickjacking, MIME
   * sniffing and referrer leakage, which are safe to apply globally. TLS/HSTS is
   * terminated at the reverse proxy.
   *
   * `/media/:path*` is frozen at the edge: static media under public/media is
   * content-addressed by name (the rendition is in the filename), so it can be
   * cached immutably. Without this Next serves public/ as `max-age=0`, which makes
   * the CDN in front of production revalidate - and re-stream the multi-megabyte
   * demo from the origin - on every play. Rename the file when the content changes.
   */
  nextConfig.headers = async () => {
    const securityHeaders = [
      { key: "X-Frame-Options", value: "DENY" },
      { key: "X-Content-Type-Options", value: "nosniff" },
      { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
      { key: "Content-Security-Policy", value: "frame-ancestors 'none'" },
      { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
      { key: "X-DNS-Prefetch-Control", value: "off" },
    ];
    return [
      { source: "/:path*", headers: securityHeaders },
      {
        source: "/media/:path*",
        headers: [{ key: "Cache-Control", value: "public, max-age=31536000, immutable" }],
      },
    ];
  };
}

export default nextConfig;
