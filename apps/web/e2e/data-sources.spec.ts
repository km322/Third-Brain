import { chmodSync, mkdtempSync, writeFileSync } from "fs";
import { tmpdir } from "os";
import { join } from "path";

import { expect, test } from "@playwright/test";

import { createCollection, signUp, unique, waitForIndexed } from "./helpers";

/**
 * The flagship connector journey: an admin configures a local-folder data source,
 * runs a sync, and the folder's files are ingested + indexed into the target
 * collection - exercising the connector framework, source-ACL sync and ingestion
 * end-to-end through the real UI.
 */
test.describe("data sources", () => {
  test("syncs a local folder and indexes its documents", async ({ page }) => {
    await signUp(page);

    const collectionName = `Synced Wiki ${unique()}`;
    await createCollection(page, collectionName);

    // A folder on disk the API server can read. When the stack runs in containers
    // (CI), E2E_DATA_DIR is a host path bind-mounted into api + worker at the same
    // absolute path; otherwise the API shares the host filesystem and tmp is fine.
    // mkdtemp creates the dir 0700 - widen it so the container user (uid 10001) can
    // traverse and read the tree.
    const baseDir = process.env.E2E_DATA_DIR ?? tmpdir();
    const dir = mkdtempSync(join(baseDir, "tb-ds-"));
    chmodSync(dir, 0o755);
    const fileName = `handbook-${unique()}.md`;
    writeFileSync(
      join(dir, fileName),
      "The company handbook covers PTO, expenses and travel policy for the team.",
    );
    // Empty default ACL: only admins (the owner) can see it - which is who we are.
    writeFileSync(join(dir, ".acl.json"), JSON.stringify({ default: [] }));

    await page.goto("/dashboard/data-sources");
    await expect(
      page.getByRole("heading", { name: /data sources/i, level: 1 }),
    ).toBeVisible();

    await page
      .getByRole("button", { name: /add data source/i })
      .first()
      .click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();

    await dialog.getByLabel("Name", { exact: true }).fill(`Team folder ${unique()}`);
    // Kind defaults to "Local folder". Pick the target collection.
    await dialog.locator("#ds-collection").click();
    await page.getByRole("option", { name: collectionName }).click();
    await dialog.getByLabel("Config (JSON)").fill(JSON.stringify({ root: dir }));
    await dialog.getByRole("button", { name: /create data source/i }).click();

    await expect(page.getByText(/data source created/i)).toBeVisible({ timeout: 15_000 });

    // Run a sync; the folder has exactly one file.
    await page.getByRole("button", { name: /sync now/i }).click();
    await expect(page.getByText(/synced:\s*1 added/i)).toBeVisible({ timeout: 30_000 });

    // The synced file is ingested through the normal pipeline and lands Indexed.
    await page.goto("/dashboard/documents");
    await waitForIndexed(page, fileName);
  });
});
