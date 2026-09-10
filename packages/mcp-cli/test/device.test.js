import assert from "node:assert/strict";
import { test } from "node:test";

import {
  DeviceAuthError,
  pollDeviceToken,
  pollForApproval,
  startDeviceAuth,
} from "../lib/device.js";
import { verifyKey } from "../lib/client.js";
import { API_KEY, startFakeServer } from "./helpers.js";

function scriptedPoll(responses) {
  const queue = [...responses];
  return () => Promise.resolve(queue.shift());
}

/** Resolves with the full approved payload so the CLI can show the org it bound to. */
test("polls until approved, sleeping the server-given interval", async () => {
  const sleeps = [];
  let pendings = 0;
  const approved = await pollForApproval({
    poll: scriptedPoll([
      { status: "authorization_pending" },
      { status: "authorization_pending" },
      { status: "approved", api_key: "tb_new_key", org_name: "Acme" },
    ]),
    intervalSeconds: 5,
    expiresInSeconds: 900,
    sleep: async (ms) => sleeps.push(ms),
    onPending: () => {
      pendings += 1;
    },
  });
  assert.equal(approved.api_key, "tb_new_key");
  assert.equal(approved.org_name, "Acme");
  assert.deepEqual(sleeps, [5000, 5000]);
  assert.equal(pendings, 2);
});

test("denied stops polling with a denied error", async () => {
  await assert.rejects(
    pollForApproval({
      poll: scriptedPoll([{ status: "denied" }]),
      intervalSeconds: 5,
      expiresInSeconds: 900,
      sleep: async () => {},
    }),
    (err) => err instanceof DeviceAuthError && err.reason === "denied",
  );
});

test("an expired status from the server surfaces as expired", async () => {
  await assert.rejects(
    pollForApproval({
      poll: scriptedPoll([{ status: "expired" }]),
      intervalSeconds: 5,
      expiresInSeconds: 900,
      sleep: async () => {},
    }),
    (err) => err instanceof DeviceAuthError && err.reason === "expired",
  );
});

/** Two polls happen, at t=0 and t=5000; the deadline at t=10000 halts the loop. */
test("polling stops with expired once expires_in has elapsed", async () => {
  let clock = 0;
  let polls = 0;
  await assert.rejects(
    pollForApproval({
      poll: () => {
        polls += 1;
        return Promise.resolve({ status: "authorization_pending" });
      },
      intervalSeconds: 5,
      expiresInSeconds: 10,
      now: () => clock,
      sleep: async (ms) => {
        clock += ms;
      },
    }),
    (err) => err instanceof DeviceAuthError && err.reason === "expired",
  );
  assert.equal(polls, 2);
});

test("an unexpected status is a protocol error", async () => {
  await assert.rejects(
    pollForApproval({
      poll: scriptedPoll([{ status: "bogus" }]),
      intervalSeconds: 5,
      expiresInSeconds: 900,
      sleep: async () => {},
    }),
    (err) => err instanceof DeviceAuthError && err.reason === "protocol",
  );
});

/** With no Retry-After the interval doubles; with one it is interval + retry_after. */
test("slow_down stretches the wait and keeps polling", async () => {
  const sleeps = [];
  const key = await pollForApproval({
    poll: scriptedPoll([
      { status: "slow_down", retry_after_seconds: null },
      { status: "slow_down", retry_after_seconds: 30 },
      { status: "approved", api_key: "tb_new_key" },
    ]),
    intervalSeconds: 5,
    expiresInSeconds: 900,
    sleep: async (ms) => sleeps.push(ms),
  });
  assert.equal(key.api_key, "tb_new_key");
  assert.deepEqual(sleeps, [10000, 35000]);
});

test("pollDeviceToken maps HTTP 429 to slow_down with Retry-After", async () => {
  const fetchImpl = async () => ({
    status: 429,
    ok: false,
    headers: { get: (name) => (name === "retry-after" ? "17" : null) },
    json: async () => ({}),
  });
  const result = await pollDeviceToken("https://brain.example.com", "tbd_x", {
    fetchImpl,
  });
  assert.deepEqual(result, { status: "slow_down", retry_after_seconds: 17 });
});

test("approved without an api_key is a protocol error", async () => {
  await assert.rejects(
    pollForApproval({
      poll: scriptedPoll([{ status: "approved" }]),
      intervalSeconds: 5,
      expiresInSeconds: 900,
      sleep: async () => {},
    }),
    (err) => err instanceof DeviceAuthError && err.reason === "protocol",
  );
});

test("full device flow over HTTP: start, poll to approval, verify the key", async () => {
  const server = await startFakeServer({
    tokenResponses: [
      { status: "authorization_pending" },
      { status: "approved", api_key: API_KEY },
    ],
  });
  try {
    const grant = await startDeviceAuth(server.url, "third-brain-mcp on testhost");
    assert.equal(grant.user_code, server.deviceGrant.user_code);
    assert.ok(grant.verification_uri_complete.includes(grant.user_code));

    const approved = await pollForApproval({
      poll: () => pollDeviceToken(server.url, grant.device_code),
      intervalSeconds: grant.interval,
      expiresInSeconds: grant.expires_in,
      sleep: async () => {},
    });
    assert.equal(approved.api_key, API_KEY);

    const verified = await verifyKey(server.url, approved.api_key);
    assert.equal(verified.scopeLimited, false);

    const started = server.requests.find((req) => req.url === "/api/v1/device-auth");
    assert.deepEqual(JSON.parse(started.body), {
      client_name: "third-brain-mcp on testhost",
    });
  } finally {
    await server.close();
  }
});
