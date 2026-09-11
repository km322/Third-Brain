import { expect, test } from "@playwright/test";

import { GITHUB_URL } from "@/components/marketing/links";

/**
 * Public documentation page: the open-source getting-started surface. Third Brain is
 * free and self-hosted, so the quick start must open with the bring-up a stranger runs
 * on their own machine and link the public repository - never a waitlist, an access
 * request, or a hosted plan.
 */
test.describe("public docs", () => {
  /**
   * The hero headline - "Everything you need to get started." - is rendered as the page
   * h1. The quick start below it leads with the self-host bring-up, then wires in a
   * client with the MCP CLI; both blocks are verbatim commands, so they are asserted
   * verbatim, and the repository is public and reachable straight from there.
   *
   * Nothing on the page gates access behind a waitlist or a managed plan, and the
   * snippets point at the reader's own instance, not a hosted API host.
   */
  test("renders a self-host quick start that links the public repo", async ({ page }) => {
    await page.goto("/docs");

    const heading = page.getByRole("heading", { level: 1 });
    await expect(heading).toBeVisible();
    await expect(heading).toContainText(/everything you need to get started/i);

    const quickStart = page.locator("#quick-start");
    await expect(quickStart).toContainText(`git clone ${GITHUB_URL}.git`);
    await expect(quickStart).toContainText("cp .env.example .env");
    await expect(quickStart).toContainText("make up-d");
    await expect(quickStart).toContainText("make migrate && make seed");
    await expect(quickStart).toContainText("npx third-brain-mcp connect");
    await expect(quickStart).toContainText("npx third-brain-mcp install claude");

    await expect(quickStart.locator(`a[href="${GITHUB_URL}"]`)).toBeVisible();

    await expect(page.getByText(/waitlist|early access/i)).toHaveCount(0);
    expect(await page.content()).not.toContain("api.third-brain.ai");
  });
});
