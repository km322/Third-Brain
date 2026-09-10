import assert from "node:assert/strict";
import { test } from "node:test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import {
  clientConfigPath,
  installIntoConfigFile,
  mergeServerEntry,
  runInstall,
  SERVER_KEY,
} from "../lib/install.js";

const EXPECTED_ENTRY = { command: "npx", args: ["-y", "third-brain-mcp", "serve"] };

/**
 * Pinned literally (not via the shared constant) so a wording change or deletion in
 * lib/notice.js fails this suite instead of silently passing through.
 */
const NOTICE_LINES = [
  "Heads-up: agents connected through this MCP server can write to your organization's",
  "knowledge base - as they work they may capture decisions and answers into shared",
  "collections. Review captures anytime: Dashboard -> Documents -> Written by agents.",
];

function tmpdir() {
  return fs.mkdtempSync(path.join(os.tmpdir(), "tb-mcp-cli-"));
}

/** The merge is pure: the input object is not mutated. */
test("mergeServerEntry preserves unrelated servers and top-level keys", () => {
  const existing = {
    theme: "dark",
    mcpServers: {
      "other-server": { command: "other", args: ["--flag"] },
    },
  };
  const merged = mergeServerEntry(existing);
  assert.deepEqual(merged.mcpServers["other-server"], {
    command: "other",
    args: ["--flag"],
  });
  assert.equal(merged.theme, "dark");
  assert.deepEqual(merged.mcpServers[SERVER_KEY], EXPECTED_ENTRY);
  assert.equal(SERVER_KEY in existing.mcpServers, false);
});

test("mergeServerEntry creates mcpServers when missing or malformed", () => {
  assert.deepEqual(mergeServerEntry({}).mcpServers[SERVER_KEY], EXPECTED_ENTRY);
  assert.deepEqual(mergeServerEntry(undefined).mcpServers[SERVER_KEY], EXPECTED_ENTRY);
  assert.deepEqual(
    mergeServerEntry({ mcpServers: "bogus" }).mcpServers[SERVER_KEY],
    EXPECTED_ENTRY,
  );
});

test("mergeServerEntry replaces a stale third-brain entry", () => {
  const merged = mergeServerEntry({
    mcpServers: { [SERVER_KEY]: { command: "old-binary", args: [] } },
  });
  assert.deepEqual(merged.mcpServers[SERVER_KEY], EXPECTED_ENTRY);
});

test("installIntoConfigFile backs up and merges an existing config", () => {
  const configPath = path.join(tmpdir(), "claude_desktop_config.json");
  const original = {
    globalShortcut: "Cmd+Space",
    mcpServers: { filesystem: { command: "fs-server", args: ["/tmp"] } },
  };
  fs.writeFileSync(configPath, JSON.stringify(original, null, 2));

  const { backupPath } = installIntoConfigFile(configPath);

  assert.equal(backupPath, `${configPath}.bak`);
  assert.deepEqual(JSON.parse(fs.readFileSync(backupPath, "utf8")), original);
  const merged = JSON.parse(fs.readFileSync(configPath, "utf8"));
  assert.equal(merged.globalShortcut, "Cmd+Space");
  assert.deepEqual(merged.mcpServers.filesystem, original.mcpServers.filesystem);
  assert.deepEqual(merged.mcpServers[SERVER_KEY], EXPECTED_ENTRY);
});

test("installIntoConfigFile creates missing directories and files", () => {
  const configPath = path.join(tmpdir(), "Claude", "claude_desktop_config.json");
  const { backupPath } = installIntoConfigFile(configPath);
  assert.equal(backupPath, null);
  const written = JSON.parse(fs.readFileSync(configPath, "utf8"));
  assert.deepEqual(written, { mcpServers: { [SERVER_KEY]: EXPECTED_ENTRY } });
});

/** The unparseable file is left untouched on disk, not rewritten or backed up. */
test("installIntoConfigFile refuses to clobber an unparseable config", () => {
  const configPath = path.join(tmpdir(), "mcp.json");
  fs.writeFileSync(configPath, "{broken json");
  assert.throws(() => installIntoConfigFile(configPath), /not valid JSON/);
  assert.equal(fs.readFileSync(configPath, "utf8"), "{broken json");
});

test("clientConfigPath resolves per client and platform", () => {
  const opts = { home: "/home/u", env: {} };
  assert.equal(
    clientConfigPath("claude", { ...opts, platform: "darwin" }),
    path.join(
      "/home/u",
      "Library",
      "Application Support",
      "Claude",
      "claude_desktop_config.json",
    ),
  );
  assert.equal(
    clientConfigPath("claude", {
      platform: "win32",
      home: "C:\\Users\\u",
      env: { APPDATA: "C:\\Users\\u\\AppData\\Roaming" },
    }),
    path.join("C:\\Users\\u\\AppData\\Roaming", "Claude", "claude_desktop_config.json"),
  );
  assert.equal(
    clientConfigPath("claude", { ...opts, platform: "linux" }),
    path.join("/home/u", ".config", "Claude", "claude_desktop_config.json"),
  );
  assert.equal(
    clientConfigPath("cursor", { ...opts, platform: "linux" }),
    path.join("/home/u", ".cursor", "mcp.json"),
  );
});

test("runInstall for claude-code prints the exact one-liner", () => {
  const lines = [];
  runInstall("claude-code", { log: (line) => lines.push(line) });
  assert.ok(
    lines.some(
      (line) =>
        line.trim() === "claude mcp add third-brain -- npx -y third-brain-mcp serve",
    ),
  );
});

test("runInstall for claude-code prints the agent-write notice after the one-liner", () => {
  const lines = [];
  runInstall("claude-code", { log: (line) => lines.push(line) });
  const oneLiner = lines.findIndex(
    (line) =>
      line.trim() === "claude mcp add third-brain -- npx -y third-brain-mcp serve",
  );
  const notice = lines.indexOf(NOTICE_LINES[0]);
  assert.deepEqual(lines.slice(notice, notice + NOTICE_LINES.length), NOTICE_LINES);
  assert.ok(notice > oneLiner, "notice must follow the one-liner");
});

/**
 * HOME is redirected so runInstall writes ~/.cursor/mcp.json under a temp dir, not this
 * machine's real Cursor config.
 */
test("runInstall prints the agent-write notice after a successful config write", () => {
  const saved = { HOME: process.env.HOME, USERPROFILE: process.env.USERPROFILE };
  const home = tmpdir();
  process.env.HOME = home;
  process.env.USERPROFILE = home;
  const lines = [];
  try {
    runInstall("cursor", { log: (line) => lines.push(line) });
  } finally {
    for (const key of ["HOME", "USERPROFILE"]) {
      if (saved[key] === undefined) {
        delete process.env[key];
      } else {
        process.env[key] = saved[key];
      }
    }
  }
  const restart = lines.indexOf("Restart Cursor to pick up the new server.");
  const notice = lines.indexOf(NOTICE_LINES[0]);
  assert.deepEqual(lines.slice(notice, notice + NOTICE_LINES.length), NOTICE_LINES);
  assert.ok(notice > restart, "notice must follow the restart instruction");
});

test("runInstall rejects unknown clients", () => {
  assert.throws(() => runInstall("emacs"), /install <client>/);
  assert.throws(() => runInstall(undefined), /install <client>/);
});
