import assert from "node:assert/strict";
import { test } from "node:test";
import { PassThrough } from "node:stream";

import { classifyMessage, runBridge, TRANSPORT_ERROR } from "../lib/bridge.js";
import { API_KEY, startFakeServer } from "./helpers.js";

async function bridgeSession({ url, apiKey = API_KEY, lines }) {
  const input = new PassThrough();
  const output = new PassThrough();
  const chunks = [];
  output.on("data", (chunk) => chunks.push(chunk));
  const logs = [];
  const done = runBridge({
    url,
    apiKey,
    input,
    output,
    logger: (message) => logs.push(message),
  });
  for (const line of lines) {
    input.write(line + "\n");
  }
  input.end();
  await done;
  const raw = Buffer.concat(chunks).toString("utf8");
  const responses = raw
    .split("\n")
    .filter((line) => line !== "")
    .map((line) => JSON.parse(line));
  return { raw, responses, logs };
}

test("round-trips requests and writes one line per response", async () => {
  const server = await startFakeServer();
  try {
    const { raw, responses } = await bridgeSession({
      url: server.url,
      lines: [
        JSON.stringify({ jsonrpc: "2.0", id: 1, method: "initialize", params: {} }),
        JSON.stringify({ jsonrpc: "2.0", id: 2, method: "tools/list" }),
      ],
    });
    assert.equal(responses.length, 2);
    assert.equal(responses[0].id, 1);
    assert.equal(responses[0].result.serverInfo.name, "third-brain");
    assert.equal(responses[1].id, 2);
    const names = responses[1].result.tools.map((tool) => tool.name);
    assert.ok(names.includes("search_knowledge"));
    // Exactly one newline-terminated line per response: the protocol framing.
    assert.equal(raw.split("\n").length - 1, 2);
  } finally {
    await server.close();
  }
});

test("notifications are forwarded but produce no stdout", async () => {
  const server = await startFakeServer();
  try {
    const { responses } = await bridgeSession({
      url: server.url,
      lines: [
        JSON.stringify({ jsonrpc: "2.0", method: "notifications/initialized" }),
        JSON.stringify({ jsonrpc: "2.0", id: 7, method: "ping" }),
      ],
    });
    assert.equal(responses.length, 1);
    assert.equal(responses[0].id, 7);
    const forwarded = server.requests.filter(
      (req) => req.method === "POST" && req.url === "/mcp",
    );
    assert.equal(forwarded.length, 2); // the notification did reach the server
  } finally {
    await server.close();
  }
});

test("server-side JSON-RPC errors are relayed verbatim", async () => {
  const server = await startFakeServer();
  try {
    const { responses } = await bridgeSession({
      url: server.url,
      apiKey: "tb_wrong_key",
      lines: [
        JSON.stringify({
          jsonrpc: "2.0",
          id: 3,
          method: "tools/call",
          params: { name: "search_knowledge", arguments: { query: "hi" } },
        }),
      ],
    });
    assert.equal(responses.length, 1);
    assert.equal(responses[0].id, 3);
    assert.equal(responses[0].error.code, -32001);
  } finally {
    await server.close();
  }
});

test("connection failures map to a JSON-RPC error with the request id", async () => {
  const server = await startFakeServer();
  await server.close(); // the port is now dead
  const { responses, logs } = await bridgeSession({
    url: server.url,
    lines: [
      JSON.stringify({ jsonrpc: "2.0", id: 42, method: "tools/list" }),
      JSON.stringify({ jsonrpc: "2.0", method: "notifications/progress" }),
    ],
  });
  assert.equal(responses.length, 1); // the failed notification stays silent
  assert.equal(responses[0].id, 42);
  assert.equal(responses[0].error.code, TRANSPORT_ERROR);
  assert.match(responses[0].error.message, /transport error/i);
  assert.equal(logs.length, 2); // both failures logged to stderr, never stdout
});

test("non-2xx HTTP responses map to a JSON-RPC error with the request id", async () => {
  const server = await startFakeServer();
  try {
    const { responses } = await bridgeSession({
      url: server.url,
      lines: [JSON.stringify({ jsonrpc: "2.0", id: 5, method: "explode" })],
    });
    assert.equal(responses.length, 1);
    assert.equal(responses[0].id, 5);
    assert.equal(responses[0].error.code, TRANSPORT_ERROR);
    assert.match(responses[0].error.message, /500/);
  } finally {
    await server.close();
  }
});

test("blank lines are ignored and unparseable lines still get a reply", async () => {
  const server = await startFakeServer();
  try {
    const { responses } = await bridgeSession({
      url: server.url,
      lines: ["", "   ", "{not json"],
    });
    assert.equal(responses.length, 1); // the server's parse-error reply, relayed
    assert.equal(responses[0].id, null);
    assert.equal(responses[0].error.code, -32700);
  } finally {
    await server.close();
  }
});

test("classifyMessage distinguishes requests, notifications and garbage", () => {
  assert.deepEqual(classifyMessage('{"jsonrpc":"2.0","id":9,"method":"ping"}'), {
    id: 9,
    isNotification: false,
  });
  assert.deepEqual(classifyMessage('{"jsonrpc":"2.0","method":"x"}'), {
    id: null,
    isNotification: true,
  });
  assert.deepEqual(classifyMessage("{oops"), { id: null, isNotification: false });
  assert.deepEqual(classifyMessage("[1,2]"), { id: null, isNotification: false });
  // A batch of only notifications stays silent on failure; a batch with a request replies.
  assert.deepEqual(classifyMessage('[{"method":"a"},{"method":"b"}]'), {
    id: null,
    isNotification: true,
  });
  assert.deepEqual(classifyMessage('[{"id":1,"method":"a"},{"method":"b"}]'), {
    id: null,
    isNotification: false,
  });
});

test("a slow request does not head-of-line block a fast one (out-of-order ok)", async () => {
  // The bridge dispatches concurrently, so a slow first request must not delay a fast
  // second one; JSON-RPC clients match by id, so out-of-order arrival is fine.
  let firstResolve;
  const gate = new Promise((resolve) => {
    firstResolve = resolve;
  });
  let call = 0;
  const fetchImpl = async (_endpoint, init) => {
    const msg = JSON.parse(init.body);
    call += 1;
    if (call === 1) {
      await gate; // hold the first request until the second has been answered
    }
    return {
      status: 200,
      ok: true,
      text: async () => JSON.stringify({ jsonrpc: "2.0", id: msg.id, result: {} }),
    };
  };

  const input = new PassThrough();
  const output = new PassThrough();
  const order = [];
  output.on("data", (chunk) => {
    for (const line of chunk.toString("utf8").split("\n")) {
      if (line) order.push(JSON.parse(line).id);
    }
  });
  const done = runBridge({ url: "http://x", apiKey: "k", input, output, fetchImpl });
  input.write(JSON.stringify({ jsonrpc: "2.0", id: 1, method: "slow" }) + "\n");
  input.write(JSON.stringify({ jsonrpc: "2.0", id: 2, method: "fast" }) + "\n");
  input.end();

  // Give the fast (second) request time to complete before releasing the slow one.
  await new Promise((resolve) => setTimeout(resolve, 20));
  firstResolve();
  await done;

  assert.deepEqual(order, [2, 1]); // fast replied first despite being sent second
});

test("large numeric ids survive the bridge without precision loss", async () => {
  const bigId = "12345678901234567890"; // beyond 2^53; a JSON round-trip would corrupt it
  const fetchImpl = async () => ({
    status: 200,
    ok: true,
    // Single-line body: the bridge must pass it through verbatim, not re-serialize.
    text: async () => `{"jsonrpc":"2.0","id":${bigId},"result":{}}`,
  });
  const input = new PassThrough();
  const output = new PassThrough();
  const chunks = [];
  output.on("data", (chunk) => chunks.push(chunk));
  const done = runBridge({ url: "http://x", apiKey: "k", input, output, fetchImpl });
  input.write(JSON.stringify({ jsonrpc: "2.0", id: 1, method: "x" }) + "\n");
  input.end();
  await done;
  assert.match(Buffer.concat(chunks).toString("utf8"), new RegExp(`"id":${bigId}`));
});
