import type { MetadataRoute } from "next";

import { IS_STATIC_SITE } from "@/components/marketing/links";

// See the note in robots.ts: `output: "export"` requires metadata routes to opt into
// being prerendered.
export const dynamic = "force-static";

/**
 * `sitemap.xml` for the published project site: the two pages the site build is allowed
 * to emit (see PUBLISHED_PAGES in scripts/build-site.mjs). Keep the two lists in step -
 * a page added there needs an entry here to be discoverable.
 *
 * Empty in the app build, which is a self-hosted private instance: `robots.ts` answers
 * `Disallow: /` there, so publishing a list of its URLs would contradict it - and the
 * absolute base would be whatever `NEXT_PUBLIC_SITE_URL` happened to be, defaulting to
 * localhost. Only the static site build emits entries, and it always sets
 * `trailingSlash`, so the URLs carry one to match the canonicals exactly.
 */
export default function sitemap(): MetadataRoute.Sitemap {
  if (!IS_STATIC_SITE) return [];

  const siteUrl = process.env.NEXT_PUBLIC_SITE_URL || "http://localhost:3000";
  const lastModified = new Date();

  return [
    {
      url: `${siteUrl}/`,
      lastModified,
      changeFrequency: "weekly",
      priority: 1,
    },
    {
      url: `${siteUrl}/docs/`,
      lastModified,
      changeFrequency: "weekly",
      priority: 0.8,
    },
  ];
}
