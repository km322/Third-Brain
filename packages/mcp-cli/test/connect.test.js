import assert from "node:assert/strict";
import { test } from "node:test";

import { isHttpUrl, openBrowser } from "../lib/connect.js";

test("isHttpUrl accepts http(s) and rejects other schemes", () => {
  assert.equal(isHttpUrl("https://brain.acme.com/activate"), true);
  assert.equal(isHttpUrl("http://127.0.0.1:3000/activate?code=WXYZ-1234"), true);
  assert.equal(isHttpUrl("file:///Applications/Calculator.app"), false);
  assert.equal(isHttpUrl("x-apple.systempreferences:com.apple.preference"), false);
  assert.equal(isHttpUrl("not a url"), false);
  assert.equal(isHttpUrl(""), false);
});

test("openBrowser launches only http(s) URLs via the OS opener", () => {
  const calls = [];
  const spawnImpl = (cmd, args) => {
    calls.push({ cmd, args });
    return { on() {}, unref() {} };
  };

  openBrowser("https://brain.acme.com/activate", { platform: "darwin", spawnImpl });
  assert.equal(calls.length, 1);
  assert.deepEqual(calls[0], { cmd: "open", args: ["https://brain.acme.com/activate"] });

  // A non-http(s) URI (custom scheme / local file) is never handed to the opener.
  openBrowser("file:///etc/passwd", { platform: "darwin", spawnImpl });
  openBrowser("x-apple.systempreferences:evil", { platform: "linux", spawnImpl });
  assert.equal(calls.length, 1);
});
