/**
 * In-process fake of the Third Brain server surface the CLI talks to:
 * GET /mcp (descriptor), POST /mcp (JSON-RPC), and the device-auth endpoints.
 * Speaks exactly the contract pinned by apps/api/tests/integration/test_mcp.py.
 */

import http from "node:http";

export const API_KEY = "tb_test_key_abc123";
export const TOOL_NAMES = [
  "search_knowledge",
  "get_document",
  "list_collections",
  "add_knowledge",
  "update_knowledge",
];

const UNAUTHORIZED = -32001;

function readBody(req) {
  return new Promise((resolve) => {
    let data = "";
    req.on("data", (chunk) => {
      data += chunk;
    });
    req.on("end", () => resolve(data));
  });
}

function json(res, status, body) {
  res.writeHead(status, { "Content-Type": "application/json" });
  res.end(JSON.stringify(body));
}

function authorized(req) {
  const auth = req.headers.authorization || "";
  return auth === `Bearer ${API_KEY}` || req.headers["x-api-key"] === API_KEY;
}

function rpcResult(id, result) {
  return { jsonrpc: "2.0", id, result };
}

function handleRpc(message) {
  const { id, method, params } = message;
  switch (method) {
    case "initialize":
      return rpcResult(id, {
        protocolVersion: "2025-03-26",
        capabilities: { tools: {} },
        serverInfo: { name: "third-brain", version: "1.0.0" },
      });
    case "ping":
      return rpcResult(id, {});
    case "tools/list":
      return rpcResult(id, { tools: TOOL_NAMES.map((name) => ({ name })) });
    case "tools/call":
      return rpcResult(id, {
        content: [{ type: "text", text: "ok" }],
        structuredContent: { echo: params, count: 1 },
        isError: false,
      });
    default:
      return {
        jsonrpc: "2.0",
        id: id === undefined ? null : id,
        error: { code: -32601, message: `Method not found: ${method}` },
      };
  }
}

/**
 * Start the fake server on an ephemeral port. Options:
 *   tokenResponses: array of bodies POST /api/v1/device-auth/token shifts through
 *     (the last one repeats once exhausted).
 * Returns {url, requests, deviceGrant, close()}.
 *
 * A notification (a message with no "id") is acknowledged with HTTP 202 and no body. Like
 * the real server, tools/call authenticates first (a bad key -> -32001) while tools/list
 * is unauthenticated - which is why a real key must be verified via a tools/call, not
 * tools/list.
 */
export async function startFakeServer({ tokenResponses = [] } = {}) {
  const requests = [];
  const pending = [...tokenResponses];
  const deviceGrant = {
    device_code: "devcode-123",
    user_code: "WXYZ-1234",
    expires_in: 900,
    interval: 5,
  };

  const server = http.createServer(async (req, res) => {
    const body = await readBody(req);
    requests.push({ method: req.method, url: req.url, body, headers: req.headers });

    if (req.method === "GET" && req.url === "/mcp") {
      json(res, 200, {
        server: { name: "third-brain", version: "1.0.0" },
        protocol: "mcp",
        protocolVersion: "2025-03-26",
        transport: "streamable-http (json-rpc 2.0 over POST)",
        endpoint: "/mcp",
        authentication: "Authorization: Bearer tb_... (Third Brain API key)",
        methods: ["initialize", "ping", "tools/list", "tools/call"],
        tools: TOOL_NAMES,
      });
      return;
    }

    if (req.method === "POST" && req.url === "/mcp") {
      let message;
      try {
        message = JSON.parse(body);
      } catch {
        json(res, 200, {
          jsonrpc: "2.0",
          id: null,
          error: { code: -32700, message: "Parse error" },
        });
        return;
      }
      if (message && typeof message === "object" && !Array.isArray(message)) {
        if (message.method === "explode") {
          res.writeHead(500, { "Content-Type": "text/plain" });
          res.end("boom");
          return;
        }
        if (!("id" in message)) {
          res.writeHead(202);
          res.end();
          return;
        }
        if (message.method === "tools/call" && !authorized(req)) {
          json(res, 200, {
            jsonrpc: "2.0",
            id: message.id,
            error: { code: UNAUTHORIZED, message: "Unauthorized" },
          });
          return;
        }
        json(res, 200, handleRpc(message));
        return;
      }
      json(res, 200, {
        jsonrpc: "2.0",
        id: null,
        error: { code: -32600, message: "Invalid request" },
      });
      return;
    }

    if (req.method === "POST" && req.url === "/api/v1/device-auth") {
      const base = `http://127.0.0.1:${server.address().port}`;
      json(res, 200, {
        ...deviceGrant,
        verification_uri: `${base}/activate`,
        verification_uri_complete: `${base}/activate?code=${deviceGrant.user_code}`,
      });
      return;
    }

    if (req.method === "POST" && req.url === "/api/v1/device-auth/token") {
      const next = pending.length > 1 ? pending.shift() : pending[0];
      json(res, 200, next || { status: "authorization_pending" });
      return;
    }

    res.writeHead(404);
    res.end();
  });

  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const url = `http://127.0.0.1:${server.address().port}`;
  return {
    url,
    requests,
    deviceGrant,
    close: () => new Promise((resolve) => server.close(resolve)),
  };
}
