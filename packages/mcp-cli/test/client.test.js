import assert from "node:assert/strict";
import { test } from "node:test";

import { fetchDescriptor, normalizeUrl, verifyKey } from "../lib/client.js";
import { API_KEY, startFakeServer } from "./helpers.js";

test("normalizeUrl accepts bare hosts and strips trailing slashes and /mcp", () => {
  assert.equal(normalizeUrl("brain.acme.com"), "https://brain.acme.com");
  assert.equal(normalizeUrl("https://brain.acme.com/"), "https://brain.acme.com");
  assert.equal(normalizeUrl("https://brain.acme.com/mcp"), "https://brain.acme.com");
  assert.equal(normalizeUrl("https://brain.acme.com/mcp/"), "https://brain.acme.com");
  assert.equal(normalizeUrl("http://localhost:8000"), "http://localhost:8000");
  assert.throws(() => normalizeUrl(""), /required/);
  assert.throws(() => normalizeUrl("http://"), /Invalid URL/);
});

test("normalizeUrl refuses cleartext http to a remote host, but exempts loopback", () => {
  // A remote http:// host would leak the API key in cleartext -> refuse.
  assert.throws(() => normalizeUrl("http://brain.acme.com"), /cleartext/);
  assert.throws(() => normalizeUrl("http://10.0.0.5:8000/mcp"), /cleartext/);
  // A bare host is auto-upgraded to https, never refused.
  assert.equal(normalizeUrl("brain.acme.com"), "https://brain.acme.com");
  // Loopback is exempt (local dev), including IPv6 loopback (URL.hostname yields "[::1]").
  assert.equal(normalizeUrl("http://127.0.0.1:8000"), "http://127.0.0.1:8000");
  assert.equal(normalizeUrl("http://localhost:8000/mcp"), "http://localhost:8000");
  assert.equal(normalizeUrl("http://[::1]:8000"), "http://[::1]:8000");
  // The expanded IPv6-loopback form is recognized too (exempt, not refused).
  assert.equal(
    normalizeUrl("http://[0:0:0:0:0:0:0:1]:8000"),
    "http://[0:0:0:0:0:0:0:1]:8000",
  );
  // Explicit opt-out for a trusted network.
  assert.equal(
    normalizeUrl("http://brain.internal", {
      env: { THIRD_BRAIN_ALLOW_INSECURE_HTTP: "1" },
    }),
    "http://brain.internal",
  );
});

test("fetchDescriptor returns the unauthenticated GET /mcp descriptor", async () => {
  const server = await startFakeServer();
  try {
    const descriptor = await fetchDescriptor(server.url);
    assert.equal(descriptor.server.name, "third-brain");
    assert.equal(descriptor.server.version, "1.0.0");
    assert.ok(descriptor.tools.includes("search_knowledge"));
  } finally {
    await server.close();
  }
});

test("fetchDescriptor rejects non-descriptor responses", async () => {
  const fakeFetch = async () => ({ ok: true, status: 200, json: async () => ({}) });
  await assert.rejects(
    fetchDescriptor("https://example.invalid", { fetchImpl: fakeFetch }),
    /descriptor/,
  );
});

test("verifyKey accepts a valid key (authenticated probe) and rejects a bad one", async () => {
  const server = await startFakeServer();
  try {
    // tools/list is unauthenticated on the real server, so verifyKey must probe with an
    // authenticated tools/call: a good key passes, a bad key gets -32001 -> rejected.
    const result = await verifyKey(server.url, API_KEY);
    assert.equal(result.scopeLimited, false);
    await assert.rejects(verifyKey(server.url, "tb_wrong"), /rejected/);
  } finally {
    await server.close();
  }
});

test("verifyKey flags a scope-limited key (tool result isError)", async () => {
  // A key that authenticates but hits a scope limit returns a tool result with
  // isError:true; that still proves authentication, so verifyKey resolves scopeLimited.
  const fakeFetch = async () => ({
    ok: true,
    status: 200,
    json: async () => ({
      jsonrpc: "2.0",
      id: 1,
      result: {
        content: [{ type: "text", text: "Error: missing scope" }],
        isError: true,
      },
    }),
  });
  const result = await verifyKey("https://brain.example.com", "tb_x", {
    fetchImpl: fakeFetch,
  });
  assert.equal(result.scopeLimited, true);
});
