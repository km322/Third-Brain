import { expect, test } from "@playwright/test";

import { DEMO_MEDIA } from "@/components/marketing/demo-video";

/**
 * Marketing landing page: the unauthenticated front door. In the waitlist-first launch the
 * primary call-to-action captures an email rather than routing to self-serve signup. The
 * live demo workspace stays private (unlisted and credential-gated) and is never linked from
 * the marketing UI - the only demo on the page is the recorded walkthrough embedded below.
 *
 * Note on coverage: CI runs these against the compose `dev` target (`next dev`), which serves
 * public/ straight from the source tree. So the asset assertions below pin the URLs, the range
 * behaviour and the cache policy, but they cannot catch a production image that fails to copy
 * public/ into the standalone bundle - that needs a smoke test against the `runner` target.
 */
test.describe("marketing landing", () => {
  test("renders the hero and a waitlist-first primary CTA", async ({ page }) => {
    await page.goto("/");

    // Hero headline - "The documentation writes itself." - rendered as the page h1.
    const heading = page.getByRole("heading", { level: 1 });
    await expect(heading).toBeVisible();
    await expect(heading).toContainText(/documentation writes itself/i);

    // Primary CTA: "Join the waitlist" anchoring to the waitlist section.
    const cta = page.getByRole("link", { name: /^join the waitlist$/i }).first();
    await expect(cta).toBeVisible();
    await expect(cta).toHaveAttribute("href", /#waitlist$/);

    // The live demo is private now: no "Live demo" link is exposed in the marketing UI.
    await expect(page.getByRole("link", { name: /^live demo$/i })).toHaveCount(0);

    // A "Sign in" affordance remains for returning users.
    await expect(
      page.getByRole("link", { name: /^sign in$/i }).first(),
    ).toBeVisible();

    // The source repo is private: nothing on the page (footer included) may link to
    // github.com/km322 - that URL 404s for every visitor.
    await expect(page.locator('a[href*="github.com/km322"]')).toHaveCount(0);
    expect(await page.content()).not.toContain("github.com/km322");

    // Following the primary CTA reveals the waitlist form (email capture).
    await cta.click();
    await expect(page).toHaveURL(/#waitlist$/);
    const emailField = page.getByPlaceholder("you@company.com");
    await expect(emailField).toBeVisible();
    await expect(
      page.getByRole("button", { name: /join the waitlist/i }),
    ).toBeVisible();
  });

  test("embeds the recorded demo behind a poster and serves the file from the site root", async ({
    page,
  }) => {
    await page.goto("/");

    // The "Watch the demo" CTA scrolls to the embedded section, not to a live workspace.
    const watch = page.getByRole("link", { name: /^watch the demo$/i }).first();
    await expect(watch).toHaveAttribute("href", /#demo$/);

    const video = page.locator("#demo video");
    await expect(video).toBeVisible();
    // Lazy by construction: a visitor who never presses play must not pay for the file.
    await expect(video).toHaveAttribute("preload", "none");
    // The recording has no audio track at all; `muted` keeps a backgrounded tab on the
    // browser's cheap throttling path instead of holding an audio-focus slot.
    await expect(video).toHaveAttribute("muted", "");
    await expect(video).toHaveAttribute(
      "poster",
      DEMO_MEDIA.poster,
    );

    // Two renditions: a phone renders this frame in roughly 1020 device px, so it must
    // not be handed 1080p to decode. The wide rung is media-gated; the narrow one is the
    // fallback, so it carries no media attribute and every client can reach it.
    const sources = video.locator("source");
    await expect(sources).toHaveCount(2);
    await expect(sources.nth(0)).toHaveAttribute("src", DEMO_MEDIA.hd);
    await expect(sources.nth(0)).toHaveAttribute(
      "media",
      `(min-width: ${DEMO_MEDIA.hdMinWidth})`,
    );
    await expect(sources.nth(1)).toHaveAttribute("src", DEMO_MEDIA.sd);
    await expect(sources.nth(1)).not.toHaveAttribute("media", /./);

    // Every asset must actually be served, and must be range-servable: a full-body 200
    // on a 12 MB file breaks seeking and mobile Safari playback.
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
      // Frozen at the edge, so a play does not re-stream from the origin every time.
      expect(res.headers()["cache-control"]).toContain("immutable");
    }
  });

  test("the demo gateway is reachable directly but credential-gated, never handing out the password", async ({
    page,
  }) => {
    await page.goto("/demo/dashboard");

    // The demo is login-locked: it renders a sign-in gateway, not the dashboard.
    await expect(page.getByText(/explore the live demo/i).first()).toBeVisible();
    await expect(
      page.getByRole("button", { name: /^sign in$/i }),
    ).toBeVisible();

    // Credentials are never advertised or pre-filled: no seeded email appears anywhere,
    // and both fields start empty (only someone who holds the workspace credentials can
    // sign in - there is no well-known demo password to leak).
    await expect(page.getByText(/@third-brain\.ai|@example\.com/)).toHaveCount(0);
    await expect(page.getByLabel("Work email")).toHaveValue("");
    await expect(page.getByLabel("Password", { exact: true })).toHaveValue("");
  });
});
