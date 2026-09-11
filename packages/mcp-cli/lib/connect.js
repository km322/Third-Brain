/** "connect": preflight the server, sign in (device flow or --api-key), save creds. */

import { spawn } from "node:child_process";
import os from "node:os";
import readline from "node:readline/promises";

import { fetchDescriptor, normalizeUrl, verifyKey } from "./client.js";
import { CONFIG_PATH, saveConfig } from "./config.js";
import { pollDeviceToken, pollForApproval, startDeviceAuth } from "./device.js";
import { printAgentWriteNotice } from "./notice.js";

export function parseConnectArgs(args) {
  const opts = { url: "", apiKey: "" };
  for (let i = 0; i < args.length; i += 1) {
    const arg = args[i];
    if (arg === "--url") {
      opts.url = args[++i] || "";
    } else if (arg.startsWith("--url=")) {
      opts.url = arg.slice("--url=".length);
    } else if (arg === "--api-key") {
      opts.apiKey = args[++i] || "";
    } else if (arg.startsWith("--api-key=")) {
      opts.apiKey = arg.slice("--api-key=".length);
    } else {
      throw new Error(`Unknown option for connect: ${arg}`);
    }
  }
  return opts;
}

/** Whether `value` is an http(s) URL - the only kind safe to hand to the OS opener. */
export function isHttpUrl(value) {
  try {
    const p = new URL(String(value));
    return p.protocol === "http:" || p.protocol === "https:";
  } catch {
    return false;
  }
}

/**
 * Best-effort: open the approval page in the default browser. Never throws.
 *
 * Only ever hands an http(s) URL to the OS opener: a server-supplied verification URI
 * could otherwise be a file:// path or a custom scheme that launches a local app or
 * handler. A failed launch is swallowed because the URL is always printed as well.
 */
export function openBrowser(
  url,
  { platform = process.platform, spawnImpl = spawn } = {},
) {
  if (!isHttpUrl(url)) {
    return;
  }
  const [command, args] =
    platform === "darwin"
      ? ["open", [url]]
      : platform === "win32"
        ? ["cmd", ["/c", "start", "", url]]
        : ["xdg-open", [url]];
  try {
    const child = spawnImpl(command, args, { stdio: "ignore", detached: true });
    child.on("error", () => {});
    child.unref();
  } catch {}
}

/**
 * Ask the operator for the server URL on stdin.
 *
 * Prompting only makes sense with a human at the terminal. In a non-interactive context
 * (CI, a pipe) reading stdin would block forever, so that fails with guidance instead.
 */
async function promptForUrl() {
  if (!process.stdin.isTTY) {
    throw new Error(
      "No server URL provided. Pass --url <server> (stdin is not a terminal).",
    );
  }
  const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
  try {
    return await rl.question("Third Brain server URL (e.g. https://brain.acme.com): ");
  } finally {
    rl.close();
  }
}

/**
 * Run the device-code flow: start a grant, show the user code and approval page, and poll
 * until it is approved. The server caps ``client_name`` at 255 chars, so the
 * hostname-derived name is truncated well under it for very long hostnames.
 */
async function deviceFlow(url, log) {
  const clientName = `third-brain-mcp on ${os.hostname()}`.slice(0, 200);
  const grant = await startDeviceAuth(url, clientName);
  const approvalUrl = grant.verification_uri_complete || grant.verification_uri;
  log("");
  log("To approve this device, enter the code:");
  log("");
  log(`    ${grant.user_code}`);
  log("");
  log(`Approval page: ${approvalUrl}`);
  if (isHttpUrl(approvalUrl)) {
    openBrowser(approvalUrl);
  } else {
    log("(Not opening automatically: the server returned a non-web approval URL.)");
  }
  log("Waiting for approval (Ctrl-C to cancel)...");
  return await pollForApproval({
    poll: () => pollDeviceToken(url, grant.device_code),
    intervalSeconds: grant.interval,
    expiresInSeconds: grant.expires_in,
  });
}

/**
 * The ``connect`` command: preflight the server, obtain a key, verify it and save it.
 *
 * When the device flow ran, the org (and acting member) the key bound to is shown
 * prominently, so a wrong-org approval - e.g. an admin from another org approving a
 * leaked user code - is hard to miss. That line is printed even if the org name is
 * missing, so the binding is never confirmed silently. The saved-credentials line only
 * claims permissions 600 off Windows, where chmod is a no-op.
 */
export async function runConnect(args, { log = console.log } = {}) {
  const opts = parseConnectArgs(args);
  const url = normalizeUrl(opts.url || (await promptForUrl()));

  const descriptor = await fetchDescriptor(url);
  log(`Found ${descriptor.server.name} v${descriptor.server.version} at ${url}.`);

  let apiKey = opts.apiKey;
  let approval = null;
  if (!apiKey) {
    approval = await deviceFlow(url, log);
    apiKey = approval.api_key;
  }

  const { scopeLimited } = await verifyKey(url, apiKey);
  saveConfig({ url, apiKey });

  const toolCount = (descriptor.tools || []).length;
  log(`API key verified: ${toolCount} tools available.`);
  if (approval) {
    const org = approval.org_name || "(unknown - verify in the dashboard)";
    const who = approval.acts_as_email ? ` as ${approval.acts_as_email}` : "";
    log("");
    log(`Connected to organization: ${org}${who}`);
    log(
      "If this is not the organization you expected, do not use this connection - " +
        "re-run connect.",
    );
  }
  if (scopeLimited) {
    log("Note: this key is scope-limited; some actions may be unavailable.");
  }
  const perms = process.platform === "win32" ? "" : " (permissions 600)";
  log(`Saved credentials to ${CONFIG_PATH}${perms}.`);
  log("");
  printAgentWriteNotice(log);
  log("");
  log("Next, wire it into your MCP client:");
  log("  npx third-brain-mcp install claude       # Claude Desktop");
  log("  npx third-brain-mcp install claude-code  # Claude Code");
  log("  npx third-brain-mcp install cursor       # Cursor");
}
