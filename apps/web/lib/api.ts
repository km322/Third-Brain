// Typed API client for the Third Brain backend.
// Dashboard pages use the exported `api` instance: `await api.get<Collection[]>("/collections")`.

import type { ChatStreamMeta, Citation } from "@/lib/types";

const API_BASE = (process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000") + "/api/v1";

export const TOKEN_KEY = "tb_access_token";
const REFRESH_KEY = "tb_refresh_token";
export const ORG_KEY = "tb_active_org";

export class ApiError extends Error {
  status: number;
  code?: string;
  constructor(status: number, message: string, code?: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

// -- token storage (client-side) --
export const auth = {
  get token() {
    if (typeof window === "undefined") return null;
    return window.localStorage.getItem(TOKEN_KEY);
  },
  get refreshToken() {
    if (typeof window === "undefined") return null;
    return window.localStorage.getItem(REFRESH_KEY);
  },
  get activeOrg() {
    if (typeof window === "undefined") return null;
    return window.localStorage.getItem(ORG_KEY);
  },
  setSession(accessToken: string, refreshToken?: string) {
    window.localStorage.setItem(TOKEN_KEY, accessToken);
    if (refreshToken) window.localStorage.setItem(REFRESH_KEY, refreshToken);
  },
  setActiveOrg(orgId: string) {
    window.localStorage.setItem(ORG_KEY, orgId);
  },
  clear() {
    window.localStorage.removeItem(TOKEN_KEY);
    window.localStorage.removeItem(REFRESH_KEY);
    window.localStorage.removeItem(ORG_KEY);
  },
  get isAuthenticated() {
    return !!this.token;
  },
};

// Silently renew an expired access token using the stored refresh token. Concurrent 401s
// share a single in-flight refresh so we don't fire N refreshes at once.
let refreshPromise: Promise<boolean> | null = null;

async function doRefresh(): Promise<boolean> {
  const rt = auth.refreshToken;
  if (!rt) return false;
  try {
    // Send the active org so the refreshed session resumes into the org the user is
    // working in, not their default org - otherwise a silent refresh would quietly switch
    // tenants mid-session and render another org's data under the current org's UI.
    const res = await fetch(`${API_BASE}/auth/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: rt, org_id: auth.activeOrg ?? undefined }),
    });
    if (!res.ok) return false;
    const data = (await res.json()) as {
      access_token: string;
      refresh_token: string;
    };
    auth.setSession(data.access_token, data.refresh_token);
    return true;
  } catch {
    return false;
  }
}

/**
 * Mint a fresh token pair from the stored refresh token (single-flight). The
 * server resumes the active org when that membership is still ACTIVE and falls
 * back to the user's default org otherwise. Resolves `false` when there is no
 * usable refresh token.
 */
export function refreshSession(): Promise<boolean> {
  if (!refreshPromise) {
    refreshPromise = doRefresh().finally(() => {
      refreshPromise = null;
    });
  }
  return refreshPromise;
}

/**
 * The `/login` URL to bounce to when a session dies. Dashboard and `/activate`
 * deep links (the CLI's device-code approval page) are preserved via `?next=` so
 * signing back in resumes where the user was.
 */
export function loginPath(): string {
  if (typeof window === "undefined") return "/login";
  const { pathname, search } = window.location;
  return pathname.startsWith("/dashboard") || pathname.startsWith("/activate")
    ? `/login?next=${encodeURIComponent(pathname + search)}`
    : "/login";
}

/**
 * Tear down the dead session and bounce to `loginPath()` (which preserves a dashboard
 * or activate deep link via `?next=`). Returns true when it actually navigated, so
 * callers can stop settling their promise while the page unloads.
 */
function redirectToLogin(): boolean {
  auth.clear();
  if (typeof window !== "undefined" && !window.location.pathname.startsWith("/login")) {
    window.location.href = loginPath();
    return true;
  }
  return false;
}

type QueryValue = string | number | boolean | undefined | null;

function buildQuery(params?: Record<string, QueryValue | QueryValue[]>) {
  if (!params) return "";
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null) continue;
    if (Array.isArray(v)) v.forEach((x) => x != null && sp.append(k, String(x)));
    else sp.append(k, String(v));
  }
  const s = sp.toString();
  return s ? `?${s}` : "";
}

async function request<T>(
  method: string,
  path: string,
  body?: unknown,
  init?: RequestInit,
  isRetry = false,
): Promise<T> {
  const headers: Record<string, string> = {
    ...(init?.headers as Record<string, string>),
  };
  const token = auth.token;
  if (token) headers["Authorization"] = `Bearer ${token}`;

  let payload: BodyInit | undefined;
  if (body instanceof FormData) {
    payload = body;
  } else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }

  const res = await fetch(`${API_BASE}${path}`, {
    method,
    headers,
    body: payload,
    ...init,
  });

  if (res.status === 401 && typeof window !== "undefined") {
    // The access token is short-lived; try to silently renew it once with the refresh
    // token before giving up, so an expired token doesn't drop in-progress work. Never
    // recurse on the refresh call itself.
    if (!isRetry && !path.startsWith("/auth/") && (await refreshSession())) {
      return request<T>(method, path, body, init, true);
    }
    // Navigation may be under way; never settle so callers don't flash transient
    // error toasts while the page unloads.
    if (redirectToLogin()) return new Promise<T>(() => {});
  }

  if (!res.ok) {
    let detail = res.statusText;
    let code: string | undefined;
    try {
      const data = await res.json();
      detail = data.detail ?? detail;
      code = data.code;
    } catch {
      /* non-JSON error */
    }
    throw new ApiError(res.status, detail, code);
  }

  if (res.status === 204) return undefined as T;
  const contentType = res.headers.get("content-type") || "";
  if (!contentType.includes("application/json"))
    return (await res.text()) as unknown as T;
  return (await res.json()) as T;
}

export const api = {
  get: <T>(path: string, params?: Record<string, QueryValue | QueryValue[]>) =>
    request<T>("GET", `${path}${buildQuery(params)}`),
  post: <T>(path: string, body?: unknown) => request<T>("POST", path, body),
  put: <T>(path: string, body?: unknown) => request<T>("PUT", path, body),
  patch: <T>(path: string, body?: unknown) => request<T>("PATCH", path, body),
  delete: <T>(path: string) => request<T>("DELETE", path),
  upload: <T>(path: string, form: FormData) => request<T>("POST", path, form),
  raw: request,
  baseUrl: API_BASE,
};

// Streaming helper for chat: consumes the SSE frames from POST /search/chat (stream=true).
// Each frame is `data: {"type":"token","text":...}` or a final
// `data: {"type":"citations","citations":[...]}` then `data: [DONE]`. Citations ride the
// same stream so the caller never needs a second, separately-metered /search for them.
export async function streamChat(
  path: string,
  body: unknown,
  onToken: (t: string) => void,
  onCitations?: (c: Citation[], meta?: ChatStreamMeta) => void,
  signal?: AbortSignal,
  isRetry = false,
): Promise<void> {
  const token = auth.token;
  const res = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify(body),
    signal,
  });
  if (res.status === 401 && typeof window !== "undefined") {
    // Mirror request(): try one silent refresh, else clear the dead session and bounce.
    if (!isRetry && (await refreshSession())) {
      return streamChat(path, body, onToken, onCitations, signal, true);
    }
    if (redirectToLogin()) return new Promise<void>(() => {});
  }
  if (!res.ok || !res.body) {
    // Surface the backend's error message (rate limit, missing scope, provider failure…)
    // instead of a generic "Stream failed", so the Ask UI can show what actually went wrong.
    let detail = res.statusText || "Stream failed";
    let code: string | undefined;
    try {
      const data = await res.json();
      detail = data?.detail ?? data?.error?.message ?? detail;
      code = data?.code;
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, detail, code);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  // Consume every complete `\n\n`-terminated SSE frame currently in the buffer.
  const drain = () => {
    let sep: number;
    while ((sep = buffer.indexOf("\n\n")) !== -1) {
      const frame = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      const line = frame.startsWith("data:") ? frame.slice(5).trim() : frame.trim();
      if (!line || line === "[DONE]") continue;
      try {
        const evt = JSON.parse(line) as {
          type?: string;
          text?: string;
          citations?: Citation[];
          insight_id?: string | null;
          conversation_id?: string | null;
          web_sources?: ChatStreamMeta["web_sources"];
        };
        if (evt.type === "token" && typeof evt.text === "string") onToken(evt.text);
        else if (evt.type === "citations")
          onCitations?.(evt.citations ?? [], {
            insight_id: evt.insight_id,
            conversation_id: evt.conversation_id,
            web_sources: evt.web_sources ?? [],
          });
      } catch {
        /* ignore a malformed frame */
      }
    }
  };
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    drain();
  }
  buffer += decoder.decode();
  drain();
}
