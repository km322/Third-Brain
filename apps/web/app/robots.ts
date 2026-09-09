import type { MetadataRoute } from "next";

import { IS_STATIC_SITE } from "@/components/marketing/links";

/**
 * `robots.txt`, generated at build time for both builds - and deliberately opposite in
 * each.
 *
 * The published project site (`npm run build:site`) is meant to be found, so it invites
 * crawlers and points them at the sitemap. A self-hosted *instance* is the opposite: it
 * is a company's private knowledge base sitting behind login, and nothing about it should
 * end up in a search index if an operator ever exposes it to the internet. Emitting no
 * `robots.txt` at all - the previous behaviour - reads to a crawler as "no rules", which
 * is the wrong default for the app.
 *
 * Absolute base matches `metadataBase` in app/layout.tsx; the fallback keeps a local
 * build valid.
 */
// Next compiles metadata routes to Route Handlers, and `output: "export"` refuses to
// build one that has not opted into being prerendered. Both builds want the same thing:
// a file computed once at build time.
export const dynamic = "force-static";

export default function robots(): MetadataRoute.Robots {
  const siteUrl = process.env.NEXT_PUBLIC_SITE_URL || "http://localhost:3000";

  if (!IS_STATIC_SITE) {
    return { rules: [{ userAgent: "*", disallow: "/" }] };
  }

  return {
    rules: [{ userAgent: "*", allow: "/" }],
    sitemap: `${siteUrl}/sitemap.xml`,
  };
}
