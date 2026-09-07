import { expect, test } from "@playwright/test";

import {
  addTextDocument,
  createCollection,
  documentRow,
  signUp,
  unique,
} from "./helpers";

/**
 * Knowledge base lifecycle: an authenticated owner creates a collection, adds a
 * text document, and sees the ingested document surface in the collection's
 * table.
 */
test.describe("knowledge bases", () => {
  test("create a collection and add a text document that appears", async ({ page }) => {
    await signUp(page);

    const collectionName = `Handbook ${unique()}`;
    await createCollection(page, collectionName);

    // A brand-new collection starts empty.
    await expect(page.getByText(/no documents yet/i)).toBeVisible();

    const docTitle = `Remote Work Policy ${unique()}`;
    await addTextDocument(page, {
      title: docTitle,
      content:
        "Third Brain supports fully remote work. Employees may work from any " +
        "location and are reimbursed for home office equipment up to a set annual limit.",
    });

    // The new document appears as a row in the collection's document table.
    const row = documentRow(page, docTitle);
    await expect(row).toBeVisible({ timeout: 30_000 });

    // The document-count stat reflects the addition - assert the actual value, not just
    // that a "Documents" label exists somewhere on the page.
    await expect
      .poll(
        async () =>
          Number((await page.getByTestId("documents-count").innerText()).trim()),
        { timeout: 30_000 },
      )
      .toBeGreaterThanOrEqual(1);
  });
});
