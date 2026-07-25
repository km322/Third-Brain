import { expect, test } from "@playwright/test";

import {
  addTextDocument,
  createCollection,
  documentRow,
  signUp,
  unique,
  waitForIndexed,
} from "./helpers";

// A well-known fake AWS access key id (from AWS's own docs) that the secret
// scanner is guaranteed to flag. The raw value must never be rendered by the
// review UI - only its redacted form.
const FAKE_AWS_KEY = "AKIAIOSFODNN7EXAMPLE";
const REDACTED_AWS_KEY = "AKIA…LE";

/**
 * Secret quarantine lifecycle: a document whose content contains a credential
 * is held before indexing, the review dialog explains what was detected and
 * who would see it (without ever exposing the raw secret), and approving it
 * sends it through ingestion normally.
 */
test.describe("secret quarantine", () => {
  test("a document with a secret is quarantined, reviewable and approvable", async ({
    page,
  }) => {
    await signUp(page);

    const collectionName = `Infra Notes ${unique()}`;
    await createCollection(page, collectionName);

    const docTitle = `Deploy Runbook ${unique()}`;
    await addTextDocument(page, {
      title: docTitle,
      content:
        "Steps to deploy the staging environment.\n" +
        `Use access key ${FAKE_AWS_KEY} to authenticate with the registry.\n` +
        "Then run the release pipeline as usual.",
    });

    // The worker scans the content during ingestion and holds the document.
    const row = documentRow(page, docTitle);
    await expect(row).toBeVisible({ timeout: 30_000 });
    await expect(row.getByText("Quarantined")).toBeVisible({ timeout: 75_000 });

    // Open the review dialog from the row's actions menu.
    await row.getByRole("button", { name: /document actions/i }).click();
    await page.getByRole("menuitem", { name: /^review$/i }).click();

    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    await expect(
      dialog.getByRole("heading", { name: /review quarantined document/i }),
    ).toBeVisible();

    // Where it is going: the destination collection is named.
    await expect(dialog.getByText(collectionName)).toBeVisible();

    // Who will be able to see it: the audience section renders.
    await expect(dialog.getByText(/who will be able to see it/i)).toBeVisible();

    // What was detected: the redacted sample is shown...
    await expect(dialog.getByText(REDACTED_AWS_KEY)).toBeVisible();
    // ...and the full secret appears nowhere on the page.
    await expect(page.getByText(FAKE_AWS_KEY)).toHaveCount(0);

    // Approve: "Index anyway" re-enqueues ingestion and the document indexes.
    await dialog.getByRole("button", { name: /index anyway/i }).click();
    await expect(dialog).toBeHidden({ timeout: 30_000 });

    await waitForIndexed(page, docTitle);
  });
});
