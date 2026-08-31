import { expect, test } from "@playwright/test";

import {
  addTextDocument,
  createCollection,
  signUp,
  unique,
  waitForIndexed,
} from "./helpers";

/**
 * The Ask playground: after ingesting a document, a grounded question returns a
 * streamed answer accompanied by clickable source citations.
 *
 * This journey depends on the ingestion worker + retrieval, so it seeds its own
 * indexed document before asking.
 */
test.describe("ask playground", () => {
  test("returns a grounded answer with citations", async ({ page }) => {
    await signUp(page);

    const collectionName = `SLA KB ${unique()}`;
    await createCollection(page, collectionName);

    const docTitle = `Incident Response SLA ${unique()}`;
    await addTextDocument(page, {
      title: docTitle,
      content:
        "Enterprise customers receive a fifteen minute first-response SLA for " +
        "Sev-1 incidents, with a dedicated on-call bridge. Root-cause reports " +
        "are delivered within five business days of resolution.",
    });

    // Retrieval only returns hits once the document is indexed.
    await waitForIndexed(page, docTitle);

    // Head to the Ask page (default mode is "Ask").
    await page.goto("/dashboard/search");
    await expect(page.getByRole("heading", { name: "Ask", level: 1 })).toBeVisible();

    const question = "What is the incident response SLA for enterprise?";
    const box = page.getByPlaceholder(/ask a question about your knowledge/i);
    await expect(box).toBeVisible();
    await box.fill(question);
    // Enter (without Shift) submits the query.
    await box.press("Enter");

    // The answer panel (scoped to its own card, not the whole page) streams grounded text.
    const answerPanel = page.getByTestId("answer-panel");
    await expect(answerPanel).toBeVisible();

    // Sources panel populates with at least one citation card (each shows a
    // "% match" score and links to the cited document).
    await expect(page.getByText("Sources").first()).toBeVisible();
    const firstCitation = page.getByText(/%\s*match/i).first();
    await expect(firstCitation).toBeVisible({ timeout: 45_000 });

    // A citation links back to the originating document on the Documents page.
    await expect(
      page.getByRole("link", { name: new RegExp(docTitle) }).first(),
    ).toBeVisible();

    // The stream must produce a real grounded answer - NOT the "No answer was produced"
    // fallback - so a broken SSE path fails the test instead of passing vacuously.
    await expect(answerPanel.getByText(/no answer was produced/i)).toHaveCount(0);
    await expect
      .poll(
        async () =>
          (await answerPanel.innerText()).replace(/^\s*Answer\s*/i, "").trim().length,
        { timeout: 45_000 },
      )
      .toBeGreaterThan(20);
  });
});
