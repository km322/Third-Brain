/**
 * Outbound destinations shared by the marketing surface. Third Brain is an
 * open-source project, so the repository is the on-ramp: it is linked from the
 * navbar, the footer, the closing CTA and the docs page, and the URL is kept
 * here so those never drift apart.
 */
export const GITHUB_URL = "https://github.com/km322/Third-Brain";

export const GITHUB_LICENSE_URL = `${GITHUB_URL}/blob/main/LICENSE`;

/**
 * True in the static project-site build (`npm run build:site`), which publishes only
 * the marketing pages - there is no API and no `/login` route behind them. The
 * marketing surface uses this to hide sign-in affordances that would dead-end there;
 * in the app build the flag is empty and every link behaves as it always has.
 *
 * Inlined at build time from STATIC_EXPORT (see next.config.mjs).
 */
export const IS_STATIC_SITE = process.env.NEXT_PUBLIC_STATIC_SITE === "1";
