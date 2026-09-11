/**
 * Tests for the "connect" command.
 *
 * HOME is pointed at an empty temp dir BEFORE connect.js is loaded: config.js resolves
 * ~/.third-brain at import time, and runConnect below must never overwrite this
 * machine's real credentials. Hence the dynamic import.
 */

import assert from "node:assert/strict";
import { test } from "node:test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { API_KEY, startFakeServer } from "./helpers.js";

const home = fs.mkdtempSync(path.join(os.tmpdir(), "tb-mcp-home-"));
process.env.HOME = home;
process.env.USERPROFILE = home;

const { isHttpUrl, openBrowser, runConnect } = await import("../lib/connect.js");

/**
 * Pinned literally (not via the shared constant) so a wording change or deletion in
 * lib/notice.js fails this suite instead of silently passing through.
 */
const NOTICE_LINES = [
  "Heads-up: agents connected through this MCP server can write to your organization's",
  "knowledge base - as they work they may capture decisions and answers into shared",
  "collections. Review captures anytime: Dashboard -> Documents -> Written by agents.",
];

test("isHttpUrl accepts http(s) and rejects other schemes", () => {
  assert.equal(isHttpUrl("https://brain.acme.com/activate"), true);
  assert.equal(isHttpUrl("http://127.0.0.1:3000/activate?code=WXYZ-1234"), true);
  assert.equal(isHttpUrl("file:///Applications/Calculator.app"), false);
  assert.equal(isHttpUrl("x-apple.systempreferences:com.apple.preference"), false);
  assert.equal(isHttpUrl("not a url"), false);
  assert.equal(isHttpUrl(""), false);
});

/** A non-http(s) URI (custom scheme / local file) is never handed to the opener. */
test("openBrowser launches only http(s) URLs via the OS opener", () => {
  const calls = [];
  const spawnImpl = (cmd, args) => {
    calls.push({ cmd, args });
    return { on() {}, unref() {} };
  };

  openBrowser("https://brain.acme.com/activate", { platform: "darwin", spawnImpl });
  assert.equal(calls.length, 1);
  assert.deepEqual(calls[0], { cmd: "open", args: ["https://brain.acme.com/activate"] });

  openBrowser("file:///etc/passwd", { platform: "darwin", spawnImpl });
  openBrowser("x-apple.systempreferences:evil", { platform: "linux", spawnImpl });
  assert.equal(calls.length, 1);
});

test("runConnect prints the agent-write notice between save and next steps", async () => {
  const server = await startFakeServer();
  const lines = [];
  try {
    await runConnect(["--url", server.url, "--api-key", API_KEY], {
      log: (line) => lines.push(line),
    });
  } finally {
    await server.close();
  }

  const saved = lines.findIndex((line) => line.startsWith("Saved credentials to "));
  const next = lines.indexOf("Next, wire it into your MCP client:");
  const notice = lines.indexOf(NOTICE_LINES[0]);
  assert.notEqual(saved, -1);
  assert.notEqual(next, -1);
  assert.deepEqual(lines.slice(notice, notice + NOTICE_LINES.length), NOTICE_LINES);
  assert.ok(saved < notice && notice < next, "notice must sit between save and next steps");
});
