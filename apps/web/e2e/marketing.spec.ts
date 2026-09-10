import { expect, test } from "@playwright/test";

import { DEMO_MEDIA } from "@/components/marketing/demo-video";
import { GITHUB_URL } from "@/components/marketing/links";

/**
 * Marketing landing page: the unauthenticated front door. Third Brain is a free,
 * open-source, self-hosted project, so nothing here gates the product: the primary
 * call-to-action routes a stranger to the self-host quick start and the page links the
 * public repository. The only demo on the page is the recorded walkthrough embedded
 * below - there is no hosted workspace to sign in to.
 *
 * Note on coverage: CI runs these against the compose `dev` target (`next dev`), which serves
 * public/ straight from the source tree. So the asset assertions below pin the URLs, the range
 * behaviour and the cache policy, but they cannot catch a production image that fails to copy
 * public/ into the standalone bundle - that needs a smoke test against the `runner` target.
 */
test.describe("marketing landing", () => {
  /**
   * The hero headline - "The documentation writes itself." - is rendered as the page h1,
   * above the primary CTA: the free self-host on-ramp, pointing at the docs quick start.
   * The closing CTA repeats that on-ramp plus the one violet text link to the source.
   *
   * Nothing is gated: no waitlist section, no email capture, no "early access". The live
   * demo is private, so no "Live demo" link is exposed in the marketing UI, though a
   * "Sign in" affordance remains for returning users. The repo is public now and is the
   * on-ramp: the navbar, the closing CTA and the footer all point at it.
   *
   * Following the primary CTA lands on the quick start, which is a real section.
   */
  test("renders the hero, the self-host CTA and the closing CTA", async ({ page }) => {
    await page.goto("/");

    const heading = page.getByRole("heading", { level: 1 });
    await expect(heading).toBeVisible();
    await expect(heading).toContainText(/documentation writes itself/i);

    const cta = page.getByRole("link", { name: /^self-host it free$/i }).first();
    await expect(cta).toBeVisible();
    await expect(cta).toHaveAttribute("href", "/docs#quick-start");

    await expect(
      page.getByRole("heading", {
        name: /give your company a brain that writes itself/i,
      }),
    ).toBeVisible();
    const source = page.getByRole("link", { name: /^view the source$/i });
    await expect(source).toBeVisible();
    await expect(source).toHaveAttribute("href", GITHUB_URL);

    await expect(page.getByText(/waitlist|early access/i)).toHaveCount(0);
    await expect(page.getByPlaceholder("you@company.com")).toHaveCount(0);

    await expect(page.getByRole("link", { name: /^live demo$/i })).toHaveCount(0);

    await expect(page.getByRole("link", { name: /^sign in$/i }).first()).toBeVisible();

    expect(await page.locator(`a[href="${GITHUB_URL}"]`).count()).toBeGreaterThan(0);

    await cta.click();
    await expect(page).toHaveURL(/\/docs#quick-start$/);
    await expect(page.locator("#quick-start")).toBeVisible();
  });

  /**
   * The "Watch the demo" CTA scrolls to the embedded section, not to a live workspace.
   * The player is lazy by construction - a visitor who never presses play must not pay
   * for the file - and the recording has no audio track at all, so `muted` keeps a
   * backgrounded tab on the browser's cheap throttling path instead of holding an
   * audio-focus slot.
   *
   * Two renditions: a phone renders this frame in roughly 1020 device px, so it must not
   * be handed 1080p to decode. The wide rung is media-gated; the narrow one is the
   * fallback, so it carries no media attribute and every client can reach it.
   *
   * Every asset must actually be served, and must be range-servable: a full-body 200 on a
   * 12 MB file breaks seeking and mobile Safari playback. Each is also frozen at the edge,
   * so a play does not re-stream from the origin every time.
   */
  test("embeds the recorded demo behind a poster and serves the file from the site root", async ({
    page,
  }) => {
    await page.goto("/");

    const watch = page.getByRole("link", { name: /^watch the demo$/i }).first();
    await expect(watch).toHaveAttribute("href", /#demo$/);

    const video = page.locator("#demo video");
    await expect(video).toBeVisible();
    await expect(video).toHaveAttribute("preload", "none");
    await expect(video).toHaveAttribute("muted", "");
    await expect(video).toHaveAttribute("poster", DEMO_MEDIA.poster);

    const sources = video.locator("source");
    await expect(sources).toHaveCount(2);
    await expect(sources.nth(0)).toHaveAttribute("src", DEMO_MEDIA.hd);
    await expect(sources.nth(0)).toHaveAttribute(
      "media",
      `(min-width: ${DEMO_MEDIA.hdMinWidth})`,
    );
    await expect(sources.nth(1)).toHaveAttribute("src", DEMO_MEDIA.sd);
    await expect(sources.nth(1)).not.toHaveAttribute("media", /./);

    for (const [path, type] of [
      [DEMO_MEDIA.hd, "video/mp4"],
      [DEMO_MEDIA.sd, "video/mp4"],
      [DEMO_MEDIA.poster, "image/webp"],
    ]) {
      const res = await page.request.get(path, {
        headers: { Range: "bytes=0-1023" },
      });
      expect(res.status()).toBe(206);
      expect(res.headers()["content-type"]).toContain(type);
      expect(res.headers()["cache-control"]).toContain("immutable");
    }
  });
});
