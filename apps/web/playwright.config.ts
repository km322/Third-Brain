import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright E2E configuration for the Third Brain dashboard.
 *
 * These specs drive a REAL, fully-running stack (Next.js web + FastAPI API +
 * Postgres/pgvector + Redis + the arq ingestion worker). We intentionally do
 * NOT start a `webServer` here: the authoritative run is CI, which brings the
 * whole stack up via docker-compose and then points Playwright at it through
 * `PLAYWRIGHT_BASE_URL`. Locally you can run the same specs against `npm run dev`
 * + the API stack on the default ports.
 *
 * The suite is designed to be enumerable without any infra (`playwright test
 * --list`); it only needs the running stack to actually execute.
 */

const BASE_URL = process.env.PLAYWRIGHT_BASE_URL || "http://localhost:3000";
const IS_CI = !!process.env.CI;

export default defineConfig({
  testDir: "./e2e",
  // Collect only spec files; shared helpers live alongside them but must not be
  // treated as tests.
  testMatch: /.*\.spec\.ts$/,

  // Ingestion + retrieval are asynchronous (worker + embeddings), so give each
  // test room while still failing fast on a genuinely stuck flow.
  timeout: 90_000,
  expect: { timeout: 15_000 },

  fullyParallel: true,
  // Never let a committed `test.only` silently narrow the CI run.
  forbidOnly: IS_CI,
  retries: IS_CI ? 2 : 0,
  // Keep runs deterministic in CI (shared backend state); parallelize locally.
  workers: IS_CI ? 1 : undefined,

  reporter: IS_CI
    ? [["github"], ["list"], ["html", { open: "never" }]]
    : [["list"], ["html", { open: "never" }]],

  use: {
    baseURL: BASE_URL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
    actionTimeout: 15_000,
    navigationTimeout: 30_000,
  },

  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
});
