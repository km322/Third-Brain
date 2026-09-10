"use client";

import * as React from "react";
import { useQueryClient } from "@tanstack/react-query";
import { usePathname, useRouter } from "next/navigation";

import {
  api,
  ApiError,
  auth,
  loginPath,
  ORG_KEY,
  refreshSession,
  TOKEN_KEY,
} from "@/lib/api";
import { CURRENT_USER_KEY, useCurrentUser } from "@/lib/hooks";
import type { AuthTokens, Organization, OrgRole, User } from "@/lib/types";

interface AuthContextValue {
  /** The authenticated user, or `null` while loading / unauthenticated. */
  user: User | null;
  /** The currently active organization. */
  org: Organization | null;
  /** Every organization the user belongs to (for the org switcher). */
  orgs: Organization[];
  /** The caller's role in the active org. */
  role: OrgRole | null;
  isLoading: boolean;
  isError: boolean;
  /** Re-fetch the current-user context (e.g. after profile edits). */
  refetch: () => void;
  /** Sign out: best-effort server logout, clear tokens, go to /login. */
  logout: () => Promise<void>;
  /** Switch the active org - mints new org-scoped tokens and resets caches. */
  switchOrg: (orgId: string) => Promise<void>;
}

const AuthContext = React.createContext<AuthContextValue | undefined>(undefined);

/**
 * Client-side auth provider for the dashboard shell. Loads `GET /users/me`,
 * exposes the active identity + org, and centralises logout / org-switching.
 * On a 401 (expired or missing session) it clears local state and redirects to
 * `/login`.
 *
 * `hasToken` gates the identity query: only query when we actually hold a token,
 * otherwise bounce to /login.
 *
 * A 403 from `/users/me` means the active membership was suspended or removed
 * mid-session. One silent recovery is attempted per mount: re-mint tokens (the server
 * falls back to the user's default org) and reload identity.
 *
 * Cross-tab session sync signs this tab out when another tab signs out, and reloads
 * identity when another tab switches the active org so this tab re-renders under the new
 * org instead of blending tenants. Either way the previous user's cached, org-scoped
 * queries are dropped before navigating, so a different user signing in from this tab
 * never sees stale cross-tenant data.
 *
 * The persisted active-org id is kept aligned with the server's view.
 */
export function AuthProvider({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const queryClient = useQueryClient();

  const [hasToken, setHasToken] = React.useState<boolean>(() =>
    typeof window === "undefined" ? true : auth.isAuthenticated,
  );

  React.useEffect(() => {
    if (!auth.isAuthenticated) {
      setHasToken(false);
      if (!pathname?.startsWith("/login")) router.replace(loginPath());
    }
  }, [pathname, router]);

  const query = useCurrentUser({ enabled: hasToken });
  const { data, isLoading, isError, refetch } = query;

  const attempted403Recovery = React.useRef(false);

  React.useEffect(() => {
    if (!query.isError || !(query.error instanceof ApiError)) return;
    const status = query.error.status;
    if (status === 401) {
      auth.clear();
      if (!pathname?.startsWith("/login")) router.replace(loginPath());
      return;
    }
    if (status !== 403) return;
    if (attempted403Recovery.current) {
      auth.clear();
      router.replace(loginPath());
      return;
    }
    attempted403Recovery.current = true;
    void (async () => {
      if (await refreshSession()) {
        queryClient.clear();
        void refetch();
      } else {
        auth.clear();
        router.replace(loginPath());
      }
    })();
  }, [query.isError, query.error, refetch, pathname, router, queryClient]);

  React.useEffect(() => {
    function onStorage(e: StorageEvent) {
      if (e.key === TOKEN_KEY && e.newValue === null) {
        queryClient.clear();
        router.replace("/login");
      } else if (e.key === ORG_KEY && e.newValue && e.newValue !== e.oldValue) {
        queryClient.clear();
        void queryClient.refetchQueries({ queryKey: CURRENT_USER_KEY });
      }
    }
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, [router, queryClient]);

  React.useEffect(() => {
    const activeId = query.data?.active_org?.id;
    if (activeId && auth.activeOrg !== activeId) auth.setActiveOrg(activeId);
  }, [query.data?.active_org?.id]);

  /**
   * Sign out. The refresh token is surrendered so the server revokes it, not just us;
   * that call is best effort - the client is cleared regardless.
   */
  const logout = React.useCallback(async () => {
    try {
      const refreshToken = auth.refreshToken;
      await api.post(
        "/auth/logout",
        refreshToken ? { refresh_token: refreshToken } : undefined,
      );
    } catch {}
    auth.clear();
    queryClient.clear();
    router.replace("/login");
  }, [queryClient, router]);

  /**
   * Switch the active org, minting org-scoped tokens for it. All dashboard data is
   * org-scoped, so every cached query is dropped and the UI re-fetches under the new
   * organization.
   */
  const switchOrg = React.useCallback(
    async (orgId: string) => {
      const tokens = await api.post<AuthTokens>("/orgs/switch", {
        org_id: orgId,
      });
      auth.setSession(tokens.access_token, tokens.refresh_token);
      auth.setActiveOrg(orgId);
      await queryClient.invalidateQueries();
      await queryClient.refetchQueries({ queryKey: CURRENT_USER_KEY });
    },
    [queryClient],
  );

  const value = React.useMemo<AuthContextValue>(
    () => ({
      user: data?.user ?? null,
      org: data?.active_org ?? null,
      orgs: data?.organizations ?? [],
      role: data?.role ?? null,
      isLoading: hasToken && isLoading,
      isError,
      refetch: () => void refetch(),
      logout,
      switchOrg,
    }),
    [data, isLoading, isError, refetch, hasToken, logout, switchOrg],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

/** Access the active auth context. Must be used within an {@link AuthProvider}. */
export function useAuth(): AuthContextValue {
  const ctx = React.useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within an AuthProvider");
  return ctx;
}
