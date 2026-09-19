import { expect, test } from "@playwright/test";

import {
  addTextDocument,
  createCollection,
  signUp,
  unique,
  waitForIndexed,
} from "./helpers";

/**
 * The Usage page: every metered operation a workspace performs has to reach the charts.
 * This journey seeds real usage - an ingest, the embeddings behind it and a completion
 * from one Ask - then asserts the three chart surfaces actually render it.
 *
 * Worth a journey of its own because charts are where a charting-library upgrade breaks
 * quietly: the build compiles, the page loads, and the series is simply missing. The two
 * contracts asserted below - the bar's formatted value labels and the custom tooltip
 * receiving the hovered row's payload - are exactly the ones recharts changed in v3.
 *
 * The cost donut is asserted in its empty state deliberately. The deterministic offline
 * provider that runs when no API keys are set is not billable
 * (`app.services.llm.pricing.OFFLINE_PROVIDERS`), so every `cost_usd` on this stack is
 * genuinely zero. The donut's hover readout needs a billable provider and so is not
 * covered here.
 */
test.describe("usage analytics", () => {
  test("charts the usage a real journey produces", async ({ page }) => {
    await signUp(page);

    await createCollection(page, `Usage KB ${unique()}`);

    const docTitle = `Support policy ${unique()}`;
    await addTextDocument(page, {
      title: docTitle,
      content:
        "Support requests are acknowledged within one business hour and resolved " +
        "within two business days. Weekend coverage is on-call only.",
    });
    await waitForIndexed(page, docTitle);

    const box = page.getByPlaceholder(/ask a question about your knowledge/i);
    await page.goto("/dashboard/search");
    await box.fill("How quickly is a support request acknowledged?");
    await box.press("Enter");
    await expect(page.getByTestId("answer-panel")).toBeVisible();
    await expect(page.getByText(/%\s*match/i).first()).toBeVisible({ timeout: 45_000 });

    await page.goto("/dashboard/analytics");
    await expect(page.getByRole("heading", { name: "Usage", level: 1 })).toBeVisible();

    /** The trend is drawn, not the "No usage yet" empty state. */
    await expect(page.locator(".recharts-area-curve").first()).toBeVisible({
      timeout: 30_000,
    });
    await expect(page.getByText("No usage yet")).toHaveCount(0);

    /** One bar per metered kind, each carrying its formatted value label. */
    const barChart = page
      .locator(".recharts-wrapper")
      .filter({ has: page.locator(".recharts-bar") });
    const bars = barChart.locator(".recharts-rectangle");
    await expect(bars.first()).toBeVisible();
    await expect(barChart.locator(".recharts-label-list text").first()).toHaveText(/^\d/);

    /** The custom tooltip still receives the hovered row's full payload. */
    const firstBar = bars.first();
    await firstBar.scrollIntoViewIfNeeded();
    const barBox = (await firstBar.boundingBox())!;
    await page.mouse.move(barBox.x - 40, barBox.y + barBox.height / 2);
    await page.mouse.move(barBox.x + barBox.width / 2, barBox.y + barBox.height / 2, {
      steps: 8,
    });
    const tooltip = barChart.locator(".recharts-tooltip-wrapper");
    await expect(tooltip).toContainText("Requests");
    await expect(tooltip).toContainText("Tokens");
    await expect(tooltip).toContainText("Cost");

    /** Nothing billable ran, so the donut says so instead of drawing an empty ring. */
    await expect(page.getByText("No spend for this period.")).toBeVisible();

    /** The table behind the charts agrees with them. */
    const row = page.getByRole("row").filter({ hasText: "Completions" });
    await expect(row).toBeVisible();
    await expect(row).toContainText("$0.00");

    /** Switching the plotted metric re-renders the series rather than blanking it. */
    await page.getByRole("tab", { name: "Tokens" }).click();
    await expect(page.locator(".recharts-area-curve").first()).toBeVisible();
  });
});
