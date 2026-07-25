/** HTTP client for the Third Brain MCP endpoint (GET descriptor + JSON-RPC POST). */

// The MCP server's JSON-RPC error code for a missing/invalid credential.
export const UNAUTHORIZED = -32001;

const LOOPBACK_HOSTS = new Set(["localhost", "127.0.0.1", "::1"]);

/** Loopback hosts are exempt from the https requirement (local dev never leaves the box). */
function isLoopbackHost(hostname) {
  // URL.hostname keeps the brackets on an IPv6 literal ("[::1]"); strip them before matching,
  // and canonical IPv6 loopback (0:0:...:1) is already collapsed to "::1" by the URL parser.
  const h = String(hostname || "")
    .toLowerCase()
    .replace(/^\[|\]$/g, "");
  return (
    LOOPBACK_HOSTS.has(h) ||
    /^127(\.\d{1,3}){3}$/.test(h) || // 127.0.0.0/8
    /^::ffff:127(\.\d{1,3}){3}$/.test(h) // IPv4-mapped IPv6 loopback, e.g. ::ffff:127.0.0.1
  );
}

function allowsInsecureHttp(env) {
  const v = String((env && env.THIRD_BRAIN_ALLOW_INSECURE_HTTP) || "").trim();
  return /^(1|true|yes|on)$/i.test(v);
}

export function normalizeUrl(raw, { env = process.env } = {}) {
  let url = String(raw || "").trim();
  if (!url) {
    throw new Error("Server URL is required.");
  }
  if (!/^https?:\/\//i.test(url)) {
    url = `https://${url}`;
  }
  url = url.replace(/\/+$/, "");
  if (url.toLowerCase().endsWith("/mcp")) {
    url = url.slice(0, -"/mcp".length).replace(/\/+$/, "");
  }
  const parsed = new URL(url); // throws on garbage input
  // Never send the API key over cleartext HTTP to a remote host. Loopback is exempt (local
  // dev), and THIRD_BRAIN_ALLOW_INSECURE_HTTP=1 is an explicit opt-out for a trusted network.
  if (parsed.protocol === "http:" && !isLoopbackHost(parsed.hostname) && !allowsInsecureHttp(env)) {
    const err = new Error(
      `Refusing to use an insecure http:// URL for ${parsed.host}: your API key would be sent ` +
        `in cleartext and is exposed to anyone on the network path. Use https://, or set ` +
        `THIRD_BRAIN_ALLOW_INSECURE_HTTP=1 to override (loopback hosts are always allowed).`,
    );
    err.code = "INSECURE_URL";
    throw err;
  }
  return url;
}

/** ``fetch`` that turns a transport failure into a legible message with the OS cause. */
async function doFetch(fetchImpl, target, init) {
  try {
    return await fetchImpl(target, init);
  } catch (err) {
    // undici surfaces the real reason (ECONNREFUSED, ENOTFOUND, ...) on err.cause.
    const code = err && err.cause && err.cause.code;
    throw new Error(
      `Could not reach ${target}${code ? ` (${code})` : ""}: ${err.message}`,
    );
  }
}

/** Unauthenticated preflight: GET {server}/mcp returns the server descriptor. */
export async function fetchDescriptor(url, { fetchImpl = fetch } = {}) {
  const resp = await doFetch(fetchImpl, `${url}/mcp`, {
    headers: { Accept: "application/json" },
  });
  if (!resp.ok) {
    throw new Error(`GET ${url}/mcp failed with HTTP ${resp.status}.`);
  }
  const descriptor = await resp.json().catch(() => null);
  if (!descriptor || !descriptor.server || !descriptor.server.name) {
    throw new Error(`${url}/mcp did not return a Third Brain MCP descriptor.`);
  }
  return descriptor;
}

/** One JSON-RPC request over POST {server}/mcp. Returns the parsed response body. */
export async function rpc(url, apiKey, message, { fetchImpl = fetch } = {}) {
  const headers = { "Content-Type": "application/json" };
  if (apiKey) {
    headers.Authorization = `Bearer ${apiKey}`;
  }
  const resp = await doFetch(fetchImpl, `${url}/mcp`, {
    method: "POST",
    headers,
    body: JSON.stringify(message),
  });
  if (!resp.ok && resp.status !== 202) {
    throw new Error(`POST ${url}/mcp failed with HTTP ${resp.status}.`);
  }
  if (resp.status === 202) {
    return null; // notification: acknowledged, no body
  }
  return await resp.json();
}

/**
 * Prove an API key is accepted by the server. ``tools/list`` is unauthenticated, so it
 * cannot tell a good key from a bad one - we make an *authenticated* ``tools/call``
 * instead. A JSON-RPC ``-32001`` means the key was rejected; any other outcome (a normal
 * result, or a tool-level ``isError`` such as a scope limit) proves the key authenticated.
 * Returns ``{ scopeLimited }`` so callers can warn about an under-scoped key.
 */
export async function verifyKey(url, apiKey, { fetchImpl = fetch } = {}) {
  const resp = await rpc(
    url,
    apiKey,
    {
      jsonrpc: "2.0",
      id: 1,
      method: "tools/call",
      params: { name: "list_collections", arguments: {} },
    },
    { fetchImpl },
  );
  if (!resp) {
    throw new Error("The server did not respond when verifying the API key.");
  }
  if (resp.error) {
    if (resp.error.code === UNAUTHORIZED) {
      throw new Error(`The API key was rejected by the server (${resp.error.message}).`);
    }
    throw new Error(`Could not verify the API key: ${resp.error.message}.`);
  }
  return { scopeLimited: !!(resp.result && resp.result.isError) };
}
