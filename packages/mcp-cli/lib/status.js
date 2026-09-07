/** "status": show the configured server, its descriptor, and whether the key works. */

import { fetchDescriptor, verifyKey } from "./client.js";
import { resolveConfig } from "./config.js";

export async function runStatus({ log = console.log, env = process.env } = {}) {
  const { url, apiKey } = resolveConfig(env);
  if (!url) {
    log("Not configured. Run: npx third-brain-mcp connect");
    return false;
  }
  log(`Server: ${url}`);
  let descriptor;
  try {
    descriptor = await fetchDescriptor(url);
  } catch (err) {
    log(`Reachable: no (${err.message})`);
    return false;
  }
  const tools = descriptor.tools || [];
  log(
    `Reachable: yes (${descriptor.server.name} v${descriptor.server.version}, ` +
      `${tools.length} tools)`,
  );
  if (!apiKey) {
    log("API key: none saved. Run: npx third-brain-mcp connect");
    return false;
  }
  try {
    const { scopeLimited } = await verifyKey(url, apiKey);
    log(`API key: valid${scopeLimited ? " (scope-limited)" : ""}`);
    return true;
  } catch (err) {
    log(`API key: rejected (${err.message})`);
    return false;
  }
}
