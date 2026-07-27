import { expect, test } from "@playwright/test";

/**
 * Marketing landing page: the unauthenticated front door. In the waitlist-first launch the
 * primary call-to-action captures an email rather than routing to self-serve signup. The
 * live demo is private (unlisted and credential-gated) and is no longer linked from the
 * marketing UI.
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
