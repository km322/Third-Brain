import { expect, test } from "@playwright/test";

/**
 * Public documentation page: the managed-first getting-started surface. The source
 * repo is private, so the page must never link to github.com/km322 - that URL 404s
 * for every visitor - and the quick start must describe the hosted flow (waitlist,
 * sign-in, MCP CLI), not a self-host bring-up.
 */
test.describe("public docs", () => {
  test("renders a managed-first quick start with no private-repo links", async ({
    page,
  }) => {
    await page.goto("/docs");

    // Hero headline - "Everything you need to get started." - rendered as the page h1.
    const heading = page.getByRole("heading", { level: 1 });
    await expect(heading).toBeVisible();
    await expect(heading).toContainText(/everything you need to get started/i);

    // Quick start leads with the managed flow: join the waitlist (or sign in),
    // then wire in a client with the MCP CLI.
    const quickStart = page.locator("#quick-start");
    await expect(
      quickStart.getByRole("link", { name: /join the waitlist/i }),
    ).toBeVisible();
    await expect(quickStart).toContainText("npx third-brain-mcp connect");
    await expect(quickStart).toContainText("npx third-brain-mcp install claude");

    // No self-host bring-up instructions on the public page.
    const content = await page.content();
    expect(content).not.toContain("cp .env.example");

    // The private repo is never linked or mentioned anywhere in the rendered page.
    await expect(page.locator('a[href*="github.com/km322"]')).toHaveCount(0);
    expect(content).not.toContain("github.com/km322");
  });
});
