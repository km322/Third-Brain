import { expect, test } from "@playwright/test";

import { signUp, unique } from "./helpers";

/**
 * Signup flow: registering a new user + organization mints a session and drops
 * the user onto the dashboard as the owner of their workspace.
 */
test.describe("signup", () => {
  /**
   * Landing inside the authenticated shell means the new org is the active org - shown in
   * the topbar switcher - and the knowledge nav is present.
   */
  test("creating a workspace lands on the dashboard", async ({ page }) => {
    const account = await signUp(page);

    await expect(page).toHaveURL(/\/dashboard\/?$/);

    await expect(page.getByText(account.orgName).first()).toBeVisible();
    await expect(
      page.getByRole("link", { name: /knowledge bases/i }).first(),
    ).toBeVisible();
  });

  /**
   * A too-short password must be blocked by client-side zod validation: the error shows
   * and no navigation occurs, so we are still on the signup page.
   */
  test("rejects a too-short password with a validation error", async ({ page }) => {
    await page.goto("/signup");

    const token = unique();
    await page.getByLabel("Full name").fill("Validation Probe");
    await page.getByLabel("Organization name").fill(`Org ${token}`);
    await page.getByLabel("Work email").fill(`${token}@example.com`);
    await page.getByLabel("Password", { exact: true }).fill("short");

    await page.getByRole("button", { name: /create workspace/i }).click();

    await expect(page.getByText(/at least 8 characters/i)).toBeVisible();
    await expect(page).toHaveURL(/\/signup/);
  });
});
