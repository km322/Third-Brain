import assert from "node:assert/strict";
import { test } from "node:test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { loadConfig, resolveConfig, saveConfig } from "../lib/config.js";

function tmpConfigPath() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "tb-mcp-config-"));
  return path.join(dir, "nested", "config.json");
}

test("saveConfig round-trips and locks the file down to 600", () => {
  const configPath = tmpConfigPath();
  saveConfig({ url: "https://brain.acme.com", apiKey: "tb_secret" }, configPath);
  assert.deepEqual(loadConfig(configPath), {
    url: "https://brain.acme.com",
    apiKey: "tb_secret",
  });
  if (process.platform !== "win32") {
    const mode = fs.statSync(configPath).mode & 0o777;
    assert.equal(mode, 0o600);
  }
});

test("loadConfig returns {} for missing or corrupt files", () => {
  assert.deepEqual(loadConfig("/nonexistent/config.json"), {});
  const configPath = tmpConfigPath();
  fs.mkdirSync(path.dirname(configPath), { recursive: true });
  fs.writeFileSync(configPath, "not json");
  assert.deepEqual(loadConfig(configPath), {});
});

test("resolveConfig lets THIRD_BRAIN_* env vars override the stored config", () => {
  const configPath = tmpConfigPath();
  saveConfig({ url: "https://stored.example", apiKey: "tb_stored" }, configPath);

  const fromStore = resolveConfig({}, configPath);
  assert.deepEqual(fromStore, { url: "https://stored.example", apiKey: "tb_stored" });

  const fromEnv = resolveConfig(
    { THIRD_BRAIN_URL: "https://env.example", THIRD_BRAIN_API_KEY: "tb_env" },
    configPath,
  );
  assert.deepEqual(fromEnv, { url: "https://env.example", apiKey: "tb_env" });

  const unconfigured = resolveConfig({}, "/nonexistent/config.json");
  assert.deepEqual(unconfigured, { url: "", apiKey: "" });
});

test("resolveConfig refuses an insecure http URL rather than downgrading to cleartext", () => {
  // A remote http:// override must surface an error, not fall through as a raw value that the
  // API key would then be sent over.
  assert.throws(
    () =>
      resolveConfig(
        { THIRD_BRAIN_URL: "http://brain.acme.com", THIRD_BRAIN_API_KEY: "tb_x" },
        "/nonexistent/config.json",
      ),
    /cleartext/,
  );
  // Loopback is still allowed for local dev.
  const loop = resolveConfig(
    { THIRD_BRAIN_URL: "http://127.0.0.1:8000", THIRD_BRAIN_API_KEY: "tb_x" },
    "/nonexistent/config.json",
  );
  assert.equal(loop.url, "http://127.0.0.1:8000");
});
