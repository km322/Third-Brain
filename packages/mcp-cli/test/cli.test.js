import assert from "node:assert/strict";
import { test } from "node:test";
import { spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { API_KEY, startFakeServer } from "./helpers.js";

const ROOT = path.join(path.dirname(fileURLToPath(import.meta.url)), "..");
const BIN = path.join(ROOT, "bin", "third-brain-mcp.js");
const PKG_VERSION = JSON.parse(
  fs.readFileSync(path.join(ROOT, "package.json"), "utf8"),
).version;

/** Point HOME at an empty temp dir so this machine's ~/.third-brain cannot leak in. */
function isolatedHome() {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "tb-mcp-home-"));
  return { HOME: home, USERPROFILE: home };
}

function runCli(args, { env = {}, input = "" } = {}) {
  // Scrub the developer's own THIRD_BRAIN_* vars so a machine that happens to have them
  // set cannot leak into the "no config" tests; the caller's `env` still wins.
  const base = { ...process.env };
  delete base.THIRD_BRAIN_URL;
  delete base.THIRD_BRAIN_API_KEY;
  return new Promise((resolve) => {
    const child = spawn(process.execPath, [BIN, ...args], {
      env: { ...base, ...isolatedHome(), ...env },
      stdio: ["pipe", "pipe", "pipe"],
    });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (chunk) => {
      stdout += chunk;
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk;
    });
    child.on("close", (code) => resolve({ code, stdout, stderr }));
    if (input) {
      child.stdin.write(input);
    }
    child.stdin.end();
  });
}

test("--version prints the package.json version", async () => {
  const { code, stdout } = await runCli(["--version"]);
  assert.equal(code, 0);
  assert.equal(stdout.trim(), PKG_VERSION);
});

test("published metadata points at the open-source repository", () => {
  const pkg = JSON.parse(fs.readFileSync(path.join(ROOT, "package.json"), "utf8"));
  assert.equal(pkg.license, "Apache-2.0");
  assert.equal(pkg.homepage, "https://github.com/km322/Third-Brain#readme");
  assert.equal(pkg.repository.type, "git");
  assert.equal(pkg.repository.url, "git+https://github.com/km322/Third-Brain.git");
  assert.equal(pkg.repository.directory, "packages/mcp-cli");
  assert.equal(pkg.bugs.url, "https://github.com/km322/Third-Brain/issues");
  const readme = fs.readFileSync(path.join(ROOT, "README.md"), "utf8");
  assert.match(readme, /github\.com\/km322\/Third-Brain/);
  // The CLI talks to whatever server the operator runs; no hosted host is baked in.
  assert.doesNotMatch(readme, /third-brain\.ai/);
});

test("--help prints usage", async () => {
  const { code, stdout } = await runCli(["--help"]);
  assert.equal(code, 0);
  assert.match(stdout, /third-brain-mcp connect/);
  assert.match(stdout, /install <claude\|claude-code\|cursor>/);
});

test("unknown commands fail with help on stderr", async () => {
  const { code, stdout, stderr } = await runCli(["frobnicate"]);
  assert.equal(code, 1);
  assert.equal(stdout, "");
  assert.match(stderr, /Unknown command: frobnicate/);
});

test("serve bridges stdio to HTTP using THIRD_BRAIN_* env overrides", async () => {
  const server = await startFakeServer();
  try {
    const input =
      [
        JSON.stringify({ jsonrpc: "2.0", id: 1, method: "initialize", params: {} }),
        JSON.stringify({ jsonrpc: "2.0", method: "notifications/initialized" }),
        JSON.stringify({ jsonrpc: "2.0", id: 2, method: "ping" }),
      ].join("\n") + "\n";
    const { code, stdout } = await runCli(["serve"], {
      env: { THIRD_BRAIN_URL: server.url, THIRD_BRAIN_API_KEY: API_KEY },
      input,
    });
    assert.equal(code, 0);
    const responses = stdout
      .split("\n")
      .filter((line) => line !== "")
      .map((line) => JSON.parse(line));
    assert.equal(responses.length, 2); // the notification stayed silent
    assert.equal(responses[0].id, 1);
    assert.equal(responses[0].result.serverInfo.name, "third-brain");
    assert.equal(responses[1].id, 2);
  } finally {
    await server.close();
  }
});

test("serve without config or env fails on stderr, stdout stays clean", async () => {
  const { code, stdout, stderr } = await runCli(["serve"]);
  assert.equal(code, 1);
  assert.equal(stdout, "");
  assert.match(stderr, /not connected/i);
});

test("connect --api-key preflights, verifies and saves the config", async () => {
  const server = await startFakeServer();
  const home = isolatedHome();
  try {
    const { code, stdout } = await runCli(
      ["connect", "--url", server.url, "--api-key", API_KEY],
      { env: home },
    );
    assert.equal(code, 0);
    assert.match(stdout, /Found third-brain v1\.0\.0/);
    assert.match(stdout, /API key verified: 5 tools available\./);
    const configPath = path.join(home.HOME, ".third-brain", "config.json");
    assert.deepEqual(JSON.parse(fs.readFileSync(configPath, "utf8")), {
      url: server.url,
      apiKey: API_KEY,
    });
    if (process.platform !== "win32") {
      assert.equal(fs.statSync(configPath).mode & 0o777, 0o600);
    }
  } finally {
    await server.close();
  }
});

test("status reports a reachable server and a valid key", async () => {
  const server = await startFakeServer();
  try {
    const { code, stdout } = await runCli(["status"], {
      env: { THIRD_BRAIN_URL: server.url, THIRD_BRAIN_API_KEY: API_KEY },
    });
    assert.equal(code, 0);
    assert.match(stdout, /Reachable: yes \(third-brain v1\.0\.0, 5 tools\)/);
    assert.match(stdout, /API key: valid/);
  } finally {
    await server.close();
  }
});

test("status flags a rejected key with a non-zero exit", async () => {
  const server = await startFakeServer();
  try {
    const { code, stdout } = await runCli(["status"], {
      env: { THIRD_BRAIN_URL: server.url, THIRD_BRAIN_API_KEY: "tb_wrong" },
    });
    assert.equal(code, 1);
    assert.match(stdout, /API key: rejected/);
  } finally {
    await server.close();
  }
});
