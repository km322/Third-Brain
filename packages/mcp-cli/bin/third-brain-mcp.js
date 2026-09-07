#!/usr/bin/env node
/**
 * third-brain-mcp: connect any MCP client to a Third Brain server.
 *
 * With no arguments this runs "serve" (the stdio bridge), which is how MCP
 * clients invoke it. Everything human-facing prints to stderr in serve mode;
 * stdout is reserved for the MCP protocol.
 */

import { readFileSync } from "node:fs";

import { runConnect } from "../lib/connect.js";
import { DeviceAuthError } from "../lib/device.js";
import { runInstall } from "../lib/install.js";
import { runServe } from "../lib/serve.js";
import { runStatus } from "../lib/status.js";

const pkg = JSON.parse(readFileSync(new URL("../package.json", import.meta.url), "utf8"));

const HELP = `third-brain-mcp ${pkg.version} - connect MCP clients to a Third Brain server

Usage:
  npx third-brain-mcp connect [--url <server>] [--api-key <tb_...>]
      Sign in to a Third Brain server (browser device flow, or a pasted API
      key) and save credentials to ~/.third-brain/config.json.

  npx third-brain-mcp install <claude|claude-code|cursor>
      Register the bridge with an MCP client (safe read-modify-write merge).

  npx third-brain-mcp serve
      Run the stdio-to-HTTP MCP bridge (what MCP clients invoke; the default
      when no command is given). THIRD_BRAIN_URL / THIRD_BRAIN_API_KEY
      environment variables override the saved config.

  npx third-brain-mcp status
      Show the configured server and whether the saved API key still works.

  npx third-brain-mcp --help | --version`;

async function main() {
  const [command, ...rest] = process.argv.slice(2);
  switch (command) {
    case undefined:
    case "serve": {
      const ok = await runServe();
      if (!ok) {
        process.exitCode = 1;
      }
      return;
    }
    case "connect":
      await runConnect(rest);
      return;
    case "install":
      runInstall(rest[0]);
      return;
    case "status": {
      const ok = await runStatus();
      if (!ok) {
        process.exitCode = 1;
      }
      return;
    }
    case "--version":
    case "-v":
    case "version":
      console.log(pkg.version);
      return;
    case "--help":
    case "-h":
    case "help":
      console.log(HELP);
      return;
    default:
      process.stderr.write(`Unknown command: ${command}\n\n${HELP}\n`);
      process.exitCode = 1;
  }
}

main().catch((err) => {
  const message =
    err instanceof DeviceAuthError ? err.message : err.message || String(err);
  process.stderr.write(`Error: ${message}\n`);
  process.exitCode = 1;
});
