/** "serve": run the stdio bridge against the configured (or env-supplied) server. */

import { normalizeUrl } from "./client.js";
import { resolveConfig } from "./config.js";
import { runBridge } from "./bridge.js";

export async function runServe({ env = process.env } = {}) {
  const { url, apiKey } = resolveConfig(env);
  if (!url || !apiKey) {
    process.stderr.write(
      "third-brain-mcp is not connected. Run `npx third-brain-mcp connect` first, " +
        "or set THIRD_BRAIN_URL and THIRD_BRAIN_API_KEY.\n",
    );
    return false;
  }
  await runBridge({
    url: normalizeUrl(url, { env }),
    apiKey,
    input: process.stdin,
    output: process.stdout,
    logger: (message) => process.stderr.write(`[third-brain-mcp] ${message}\n`),
  });
  return true;
}
