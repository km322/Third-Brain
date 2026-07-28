import { readFileSync } from "node:fs";

/** @type {import('next').NextConfig} */
const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

const pkg = JSON.parse(
  readFileSync(new URL("./package.json", import.meta.url), "utf8"),
);

const nextConfig = {
  reactStrictMode: true,
  // Inlined into the client bundle at build time: the app version comes from
  // package.json, the commit from the GIT_COMMIT build arg (empty in dev).
  env: {
    NEXT_PUBLIC_APP_VERSION: pkg.version,
    NEXT_PUBLIC_GIT_COMMIT: process.env.GIT_COMMIT || "",
  },
  // Emit a self-contained server bundle (.next/standalone/server.js) so the
  // production Docker image can run `node server.js` without node_modules.
  output: "standalone",
  // Pin file tracing to this app so stray lockfiles outside the repo cannot
  // change the standalone output layout.
  outputFileTracingRoot: import.meta.dirname,
  eslint: { ignoreDuringBuilds: true },
  async rewrites() {
    // Optional dev convenience: proxy /backend/* to the API to avoid CORS locally.
    return [{ source: "/backend/:path*", destination: `${API_URL}/:path*` }];
  },
  // Baseline hardening headers on every response. Deliberately NOT a full script-src CSP
  // (Next relies on inline/hashed scripts; a strict policy would need per-build nonces and
  // risks breaking pages) - these cover clickjacking, MIME sniffing and referrer leakage,
  // which are safe to apply globally. TLS/HSTS is terminated at the reverse proxy.
  async headers() {
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
      // Static media under public/media is content-addressed by name (the
      // rendition is in the filename), so it can be frozen at the edge. Without
      // this Next serves public/ as `max-age=0`, which makes the CDN in front of
      // production revalidate - and re-stream the multi-megabyte demo from the
      // origin - on every play. Rename the file when the content changes.
      {
        source: "/media/:path*",
        headers: [
          { key: "Cache-Control", value: "public, max-age=31536000, immutable" },
        ],
      },
    ];
  },
};

export default nextConfig;
