import { expect, type Page } from "@playwright/test";

/**
 * Shared helpers for the Third Brain E2E specs. These drive the real UI against
 * a running stack - no API stubbing - so every helper waits on the same visible
 * signals a user would (URL changes, toasts, table rows, status badges).
 */

/** A freshly-created workspace account. */
export interface Account {
  name: string;
  email: string;
  password: string;
  orgName: string;
}

let seq = 0;

/** A process-unique token, safe to embed in emails / org names across parallel runs. */
export function unique(prefix = "tb"): string {
  seq += 1;
  const rand = Math.random().toString(36).slice(2, 8);
  return `${prefix}-${Date.now().toString(36)}-${seq}-${rand}`;
}

/**
 * Register a brand-new user + organization through the signup form and land on
 * the dashboard. The caller becomes the OWNER of the new org.
 *
 * The form mints a session and redirects into the app; the shell only renders children
 * once identity resolves, which is what the account menu waits on.
 */
export async function signUp(
  page: Page,
  overrides: Partial<Account> = {},
): Promise<Account> {
  const token = unique();
  const account: Account = {
    name: overrides.name ?? `E2E User ${token}`,
    email: overrides.email ?? `${token}@example.com`,
    password: overrides.password ?? "correct-horse-battery-staple",
    orgName: overrides.orgName ?? `Org ${token}`,
  };

  await page.goto("/signup");

  await page.getByLabel("Full name").fill(account.name);
  await page.getByLabel("Organization name").fill(account.orgName);
  await page.getByLabel("Work email").fill(account.email);
  await page.getByLabel("Password", { exact: true }).fill(account.password);

  await page.getByRole("button", { name: /create workspace/i }).click();

  await page.waitForURL("**/dashboard", { timeout: 30_000 });
  await expect(page.getByRole("button", { name: /account menu/i })).toBeVisible();

  return account;
}

/**
 * Create a knowledge base via the "New knowledge base" dialog and wait for the
 * redirect onto its detail page. On success we route to
 * /dashboard/collections/<uuid>, which is the knowledge base's detail URL and what
 * this returns.
 */
export async function createCollection(page: Page, name: string): Promise<string> {
  await page.goto("/dashboard/collections");

  await page
    .getByRole("button", { name: /new knowledge base/i })
    .first()
    .click();

  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  await dialog.getByLabel("Name").fill(name);
  await dialog.getByRole("button", { name: /create knowledge base/i }).click();

  await page.waitForURL(/\/dashboard\/collections\/[0-9a-fA-F-]{16,}/, {
    timeout: 30_000,
  });
  await expect(page.getByRole("heading", { name })).toBeVisible();

  return page.url();
}

/**
 * Add an inline-text document to the currently-open collection detail page.
 * Waits for the add dialog to close (its success toast fires on completion).
 *
 * The trigger button and the dialog's submit button share the label "Add document", so
 * the submit click is scoped to the dialog. The Text tab is the default, so its fields
 * are filled directly.
 */
export async function addTextDocument(
  page: Page,
  doc: { title: string; content: string },
): Promise<void> {
  await page
    .getByRole("button", { name: /add document/i })
    .first()
    .click();

  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();

  await dialog.getByLabel("Title").fill(doc.title);
  await dialog.getByLabel("Content").fill(doc.content);

  await dialog.getByRole("button", { name: /add document/i }).click();
  await expect(dialog).toBeHidden({ timeout: 30_000 });
}

/**
 * Locate the table row for a document by its title. Works on both the
 * collection-detail and the global documents tables.
 */
export function documentRow(page: Page, title: string) {
  return page.getByRole("row").filter({ hasText: title });
}

/**
 * Wait for a document to finish ingesting (status badge reads "Indexed"). The
 * list auto-polls while a document is processing, so we just assert on the row.
 */
export async function waitForIndexed(
  page: Page,
  title: string,
  timeout = 75_000,
): Promise<void> {
  const row = documentRow(page, title);
  await expect(row).toBeVisible({ timeout: 30_000 });
  await expect(row.getByText("Indexed")).toBeVisible({ timeout });
}
