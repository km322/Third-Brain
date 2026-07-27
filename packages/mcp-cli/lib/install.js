/** "install <client>": wire the bridge into Claude Desktop / Cursor / Claude Code. */

import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { printAgentWriteNotice } from "./notice.js";

export const SERVER_KEY = "third-brain";
export const CLIENTS = ["claude", "claude-code", "cursor"];

export function serverEntry() {
  return { command: "npx", args: ["-y", "third-brain-mcp", "serve"] };
}

export function clientConfigPath(
  client,
  { platform = process.platform, env = process.env, home = os.homedir() } = {},
) {
  if (client === "claude") {
    if (platform === "darwin") {
      return path.join(
        home,
        "Library",
        "Application Support",
        "Claude",
        "claude_desktop_config.json",
      );
    }
    if (platform === "win32") {
      return path.join(
        env.APPDATA || path.join(home, "AppData", "Roaming"),
        "Claude",
        "claude_desktop_config.json",
      );
    }
    return path.join(
      env.XDG_CONFIG_HOME || path.join(home, ".config"),
      "Claude",
      "claude_desktop_config.json",
    );
  }
  if (client === "cursor") {
    return path.join(home, ".cursor", "mcp.json");
  }
  throw new Error(`No config path for client: ${client}`);
}

/** Pure merge: set mcpServers["third-brain"], preserving every other entry and key. */
export function mergeServerEntry(config) {
  const base =
    config && typeof config === "object" && !Array.isArray(config) ? config : {};
  const servers =
    base.mcpServers &&
    typeof base.mcpServers === "object" &&
    !Array.isArray(base.mcpServers)
      ? base.mcpServers
      : {};
  return {
    ...base,
    mcpServers: { ...servers, [SERVER_KEY]: serverEntry() },
  };
}

/**
 * Read-modify-write the client config at `configPath`: back up the existing file
 * to <path>.bak, merge in the third-brain entry, never clobber other entries.
 * Returns {configPath, backupPath} (backupPath null when the file did not exist).
 */
export function installIntoConfigFile(configPath) {
  let existing = {};
  let backupPath = null;
  if (fs.existsSync(configPath)) {
    const raw = fs.readFileSync(configPath, "utf8");
    if (raw.trim()) {
      try {
        existing = JSON.parse(raw);
      } catch {
        throw new Error(
          `${configPath} is not valid JSON. Fix or remove it, then re-run install.`,
        );
      }
    }
    backupPath = `${configPath}.bak`;
    fs.copyFileSync(configPath, backupPath);
  } else {
    fs.mkdirSync(path.dirname(configPath), { recursive: true });
  }
  fs.writeFileSync(
    configPath,
    JSON.stringify(mergeServerEntry(existing), null, 2) + "\n",
  );
  return { configPath, backupPath };
}

const CLIENT_LABELS = {
  claude: "Claude Desktop",
  cursor: "Cursor",
};

export function runInstall(client, { log = console.log } = {}) {
  if (!client || !CLIENTS.includes(client)) {
    throw new Error(
      `Usage: third-brain-mcp install <client> where <client> is one of: ${CLIENTS.join(", ")}.`,
    );
  }
  if (client === "claude-code") {
    log("Claude Code manages its own MCP registry. Run this in your project:");
    log("");
    log("  claude mcp add third-brain -- npx -y third-brain-mcp serve");
    log("");
    printAgentWriteNotice(log);
    return;
  }
  const { configPath, backupPath } = installIntoConfigFile(clientConfigPath(client));
  log(`Added the "${SERVER_KEY}" MCP server to ${CLIENT_LABELS[client]}.`);
  log(`  Wrote: ${configPath}`);
  if (backupPath) {
    log(`  Backup of the previous config: ${backupPath}`);
  }
  log(`Restart ${CLIENT_LABELS[client]} to pick up the new server.`);
  log("");
  printAgentWriteNotice(log);
}
