/** Device-code authorization against POST /api/v1/device-auth (+ /token polling). */

export class DeviceAuthError extends Error {
  constructor(message, reason) {
    super(message);
    this.name = "DeviceAuthError";
    this.reason = reason; // "denied" | "expired" | "protocol"
  }
}

/** Start a grant: returns {device_code, user_code, verification_uri, verification_uri_complete, expires_in, interval}. */
export async function startDeviceAuth(url, clientName, { fetchImpl = fetch } = {}) {
  const resp = await fetchImpl(`${url}/api/v1/device-auth`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ client_name: clientName }),
  });
  if (!resp.ok) {
    throw new Error(`Could not start device authorization (HTTP ${resp.status}).`);
  }
  const grant = await resp.json().catch(() => null);
  if (!grant || !grant.device_code || !grant.user_code) {
    throw new Error("The server returned an invalid device authorization response.");
  }
  return grant;
}

/** One poll: POST /api/v1/device-auth/token -> {status, api_key?, retry_after_seconds?}. */
export async function pollDeviceToken(url, deviceCode, { fetchImpl = fetch } = {}) {
  const resp = await fetchImpl(`${url}/api/v1/device-auth/token`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ device_code: deviceCode }),
  });
  if (resp.status === 429) {
    // The token endpoint shares the server's unauthenticated rate limiter, so a
    // steady poll can be throttled mid-flow; treat it as "slow down", not failure.
    const retryAfter = Number(resp.headers && resp.headers.get?.("retry-after"));
    return {
      status: "slow_down",
      retry_after_seconds:
        Number.isFinite(retryAfter) && retryAfter > 0 ? retryAfter : null,
    };
  }
  if (!resp.ok) {
    throw new Error(`Device authorization poll failed (HTTP ${resp.status}).`);
  }
  return await resp.json();
}

/**
 * Poll `poll()` at the server-given interval until approval, denial or expiry.
 * Resolves with the full approved payload (``{status, api_key, org_name?, ...}``);
 * throws DeviceAuthError otherwise. `sleep` and `now` are injectable for tests.
 */
export async function pollForApproval({
  poll,
  intervalSeconds,
  expiresInSeconds,
  sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
  now = Date.now,
  onPending = () => {},
}) {
  const intervalMs = Math.max(1, Number(intervalSeconds) || 5) * 1000;
  const expiresMs =
    (Number.isFinite(Number(expiresInSeconds)) ? Number(expiresInSeconds) : 900) * 1000;
  const deadline = now() + expiresMs;
  const expired = () =>
    new DeviceAuthError(
      "The sign-in request expired before it was approved. Run connect again.",
      "expired",
    );
  while (now() < deadline) {
    const result = await poll();
    const status = result && result.status;
    if (status === "approved") {
      if (!result.api_key) {
        throw new DeviceAuthError(
          "The server approved the request but returned no API key.",
          "protocol",
        );
      }
      return result;
    }
    if (status === "denied") {
      throw new DeviceAuthError("The sign-in request was denied.", "denied");
    }
    if (status === "expired") {
      throw expired();
    }
    if (status === "slow_down") {
      const extraMs =
        result.retry_after_seconds > 0 ? result.retry_after_seconds * 1000 : intervalMs;
      onPending();
      await sleep(intervalMs + extraMs);
      continue;
    }
    if (status !== "authorization_pending") {
      throw new DeviceAuthError(
        `The server returned an unexpected status: ${JSON.stringify(status)}.`,
        "protocol",
      );
    }
    onPending();
    await sleep(intervalMs);
  }
  throw expired();
}
