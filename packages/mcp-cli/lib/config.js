/** Credential storage: ~/.third-brain/config.json, chmod 600, env vars win. */

import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { normalizeUrl } from "./client.js";

export const CONFIG_DIR = path.join(os.homedir(), ".third-brain");
export const CONFIG_PATH = path.join(CONFIG_DIR, "config.json");

export function loadConfig(configPath = CONFIG_PATH) {
  try {
    const parsed = JSON.parse(fs.readFileSync(configPath, "utf8"));
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {};
  } catch {
    return {};
  }
}

/**
 * Write the config, owner-only.
 *
 * ``writeFileSync`` only applies the mode on creation, so the mode is re-asserted
 * afterwards for a file that already existed.
 */
export function saveConfig(config, configPath = CONFIG_PATH) {
  fs.mkdirSync(path.dirname(configPath), { recursive: true, mode: 0o700 });
  fs.writeFileSync(configPath, JSON.stringify(config, null, 2) + "\n", { mode: 0o600 });
  fs.chmodSync(configPath, 0o600);
}

/**
 * Effective settings: THIRD_BRAIN_URL / THIRD_BRAIN_API_KEY env vars take
 * precedence over the stored config so CI and containers can run stateless.
 *
 * The URL is normalized here so every consumer (serve, status) gets the same canonical
 * URL - a bare host in THIRD_BRAIN_URL works everywhere, not just where the caller
 * happened to normalize. A plain parse error keeps the raw value so a caller
 * (status/serve) can still report "could not reach ...", but a refused insecure-http URL
 * is re-thrown: never silently downgrade to cleartext by falling through to a raw value
 * the key would then be sent over.
 */
export function resolveConfig(env = process.env, configPath = CONFIG_PATH) {
  const stored = loadConfig(configPath);
  const rawUrl = env.THIRD_BRAIN_URL || stored.url || "";
  let url = "";
  if (rawUrl) {
    try {
      url = normalizeUrl(rawUrl, { env });
    } catch (err) {
      if (err && err.code === "INSECURE_URL") {
        throw err;
      }
      url = rawUrl;
    }
  }
  return {
    url,
    apiKey: env.THIRD_BRAIN_API_KEY || stored.apiKey || "",
  };
}
