"use client";

import * as React from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import { Loader2 } from "lucide-react";

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { api, ApiError, auth } from "@/lib/api";
import type { AuthTokens, CurrentUser } from "@/lib/types";

/**
 * Landing page the identity provider returns to: exchanges the `code`/`state` pair for a
 * session, resolves the active org so the dashboard boots against the right tenant, and
 * continues into the app. Resolving the org is non-fatal - a failure there is ignored and
 * reconciled on the dashboard, which re-fetches identity regardless.
 */
function SsoCallbackInner() {
  const router = useRouter();
  const params = useSearchParams();
  const [error, setError] = React.useState<string | null>(null);
  const ran = React.useRef(false);

  React.useEffect(() => {
    if (ran.current) return;
    ran.current = true;
    const code = params.get("code");
    const state = params.get("state");
    if (!code || !state) {
      setError("This sign-in link is missing its authorization code.");
      return;
    }
    (async () => {
      try {
        const tokens = await api.post<AuthTokens>("/auth/sso/callback", { code, state });
        auth.setSession(tokens.access_token, tokens.refresh_token);
        try {
          const me = await api.get<CurrentUser>("/users/me");
          if (me.active_org?.id) auth.setActiveOrg(me.active_org.id);
        } catch {}
        router.replace("/dashboard");
      } catch (err) {
        setError(err instanceof ApiError ? err.message : "Single sign-on failed.");
      }
    })();
  }, [params, router]);

  return (
    <Card className="border-0 bg-transparent shadow-none">
      <CardHeader className="space-y-1.5 p-0">
        <CardTitle className="text-[28px] tracking-[-0.02em] text-foreground">
          {error ? "Sign-in failed" : "Signing you in…"}
        </CardTitle>
        <CardDescription className="text-[15px]">
          {error ?? "Completing single sign-on with your identity provider."}
        </CardDescription>
      </CardHeader>
      <CardContent className="p-0 pt-8">
        {error ? (
          <Link
            href="/login"
            className="text-sm font-medium text-primary hover:underline"
          >
            Back to sign in
          </Link>
        ) : (
          <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
        )}
      </CardContent>
    </Card>
  );
}

export default function SsoCallbackPage() {
  return (
    <React.Suspense fallback={<div className="h-4" />}>
      <SsoCallbackInner />
    </React.Suspense>
  );
}
