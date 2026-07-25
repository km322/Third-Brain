/**
 * The stdio <-> HTTP bridge ("serve" command).
 *
 * MCP stdio transport is newline-delimited JSON-RPC: each stdin line is forwarded
 * verbatim to POST {server}/mcp with the API key, and the response is written back
 * to stdout as a single line. Notifications (messages without an "id") are
 * acknowledged by the server with HTTP 202 and no body, so nothing is written.
 * stdout is the protocol channel; human logs go to stderr only.
 *
 * Requests are dispatched concurrently so a slow one does not head-of-line block pings,
 * cancellations, or parallel requests; a JSON-RPC client matches replies by id, so the
 * out-of-order arrival is fine. Each reply is written with a single ``output.write`` (one
 * atomic line) and honours backpressure.
 */

import readline from "node:readline";

export const TRANSPORT_ERROR = -32000;

function transportErrorResponse(id, message) {
  return {
    jsonrpc: "2.0",
    id: id === undefined ? null : id,
    error: { code: TRANSPORT_ERROR, message },
  };
}

/** Extract the request id and whether a transport failure should stay silent. */
export function classifyMessage(line) {
  let parsed;
  try {
    parsed = JSON.parse(line);
  } catch {
    return { id: null, isNotification: false };
  }
  if (Array.isArray(parsed)) {
    // Stay silent on failure only for a well-formed notification-only batch (every element
    // an object with no id). A batch that carries any request, or is malformed, still gets
    // a single null-id error reply.
    const allNotifications =
      parsed.length > 0 &&
      parsed.every(
        (m) => m && typeof m === "object" && !Array.isArray(m) && !("id" in m),
      );
    return { id: null, isNotification: allNotifications };
  }
  if (parsed === null || typeof parsed !== "object") {
    return { id: null, isNotification: false };
  }
  if (!("id" in parsed)) {
    return { id: null, isNotification: true };
  }
  return { id: parsed.id, isNotification: false };
}

/** Write one line, resolving once the chunk is flushed (respects backpressure). */
function writeLine(output, text) {
  return new Promise((resolve) => {
    if (output.write(text)) {
      resolve();
    } else {
      output.once("drain", resolve);
    }
  });
}

async function forwardOne({ endpoint, headers, line, output, logger, fetchImpl }) {
  const { id, isNotification } = classifyMessage(line);
  try {
    const resp = await fetchImpl(endpoint, { method: "POST", headers, body: line });
    if (resp.status === 202) {
      return; // notification acknowledged: no reply expected
    }
    const text = await resp.text();
    if (!resp.ok) {
      throw new Error(`server answered HTTP ${resp.status}`);
    }
    if (!text.trim()) {
      return;
    }
    // The server emits compact single-line JSON. Pass a single-line body through verbatim
    // so large numeric ids survive (a JSON.parse/stringify round-trip would truncate them
    // past 2^53); only a multi-line body is collapsed to keep one reply per stdout line.
    const oneLine = text.includes("\n")
      ? JSON.stringify(JSON.parse(text))
      : text.trimEnd();
    await writeLine(output, oneLine + "\n");
  } catch (err) {
    logger(`transport error: ${err.message}`);
    if (!isNotification) {
      await writeLine(
        output,
        JSON.stringify(
          transportErrorResponse(id, `Third Brain transport error: ${err.message}`),
        ) + "\n",
      );
    }
  }
}

/**
 * Pump messages from `input` to the server until `input` ends. Transport failures
 * (network down, non-2xx, unparseable response) become JSON-RPC error responses carrying
 * the request's id; failed notifications stay silent per JSON-RPC (logged to stderr only).
 */
export async function runBridge({
  url,
  apiKey,
  input,
  output,
  logger = () => {},
  fetchImpl = fetch,
}) {
  const endpoint = `${url}/mcp`;
  const headers = {
    "Content-Type": "application/json",
    Authorization: `Bearer ${apiKey}`,
  };
  const rl = readline.createInterface({ input, crlfDelay: Infinity });
  const inflight = [];
  for await (const line of rl) {
    if (!line.trim()) {
      continue;
    }
    inflight.push(forwardOne({ endpoint, headers, line, output, logger, fetchImpl }));
  }
  await Promise.all(inflight);
}
