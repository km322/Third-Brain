import { expect, test } from "@playwright/test";

import { signUp, unique } from "./helpers";

/**
 * Signup flow: registering a new user + organization mints a session and drops
 * the user onto the dashboard as the owner of their workspace.
 */
test.describe("signup", () => {
  test("creating a workspace lands on the dashboard", async ({ page }) => {
    const account = await signUp(page);

    // We are inside the authenticated shell.
    await expect(page).toHaveURL(/\/dashboard\/?$/);

    // The new org is the active org (shown in the topbar switcher), and the
    // knowledge nav is present.
    await expect(page.getByText(account.orgName).first()).toBeVisible();
    await expect(
      page.getByRole("link", { name: /knowledge bases/i }).first(),
    ).toBeVisible();
  });

  test("rejects a too-short password with a validation error", async ({
    page,
  }) => {
    await page.goto("/signup");

    const token = unique();
    await page.getByLabel("Full name").fill("Validation Probe");
    await page.getByLabel("Organization name").fill(`Org ${token}`);
    await page.getByLabel("Work email").fill(`${token}@example.com`);
    // Too short - client-side zod validation must block submission.
    await page.getByLabel("Password", { exact: true }).fill("short");

    await page.getByRole("button", { name: /create workspace/i }).click();

    await expect(
      page.getByText(/at least 8 characters/i),
    ).toBeVisible();
    // Still on the signup page; no navigation occurred.
    await expect(page).toHaveURL(/\/signup/);
  });
});
