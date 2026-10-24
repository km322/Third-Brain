import { defineConfig, globalIgnores } from "eslint/config";
import nextCoreWebVitals from "eslint-config-next/core-web-vitals";

export default defineConfig([
  ...nextCoreWebVitals,
  /**
   * Generated output - linting build artifacts (including minified bundles under out/)
   * would fail CI on code nobody wrote. Mirrors .prettierignore.
   */
  globalIgnores([
    ".next/",
    "out/",
    "playwright-report/",
    "blob-report/",
    "test-results/",
    "next-env.d.ts",
  ]),
]);
