"use client";

import * as React from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { Eye, EyeOff, Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api, auth } from "@/lib/api";
import { useRedirectIfAuthenticated } from "@/lib/use-redirect-if-authenticated";
import type { AuthTokens, CurrentUser } from "@/lib/types";

/**
 * The three seeded identities, shown as a read-only legend that tells the permission
 * story: the same question returns a different answer to each, because retrieval is
 * permission-aware. Credentials are never shown or pre-filled here - only someone who
 * already holds the workspace credentials can sign in. This demo is private and unlisted.
 */
const DEMO_ROLES = [
  {
    id: "owner",
    name: "Ada Admin",
    role: "Owner",
    blurb: "Sees everything - including the private Board & Finance space.",
  },
  {
    id: "editor",
    name: "Evan Engineer",
    role: "Editor",
    blurb: "Handbook + Engineering runbooks. Board & Finance stays hidden.",
  },
  {
    id: "viewer",
    name: "Vera Viewer",
    role: "Viewer",
    blurb: "Only the org-wide Company Handbook.",
  },
] as const;

export default function DemoDashboardPage() {
  const router = useRouter();

  // Already signed in (e.g. returning from the demo): go straight to the dashboard.
  const redirecting = useRedirectIfAuthenticated(() => "/dashboard");

  const [email, setEmail] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [showPassword, setShowPassword] = React.useState(false);
  const [submitting, setSubmitting] = React.useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!email || !password) {
      toast.error("Demo sign-in failed", {
        description: "Enter the demo workspace email and password to sign in.",
      });
      return;
    }
    setSubmitting(true);
    try {
      // Call login via a direct fetch rather than the shared api client: on a 401 the client
      // force-redirects to /login (correct for the real login page, wrong here - it would
      // swallow the error and bounce the visitor out of the demo). Here we own the error UI.
      const res = await fetch(`${api.baseUrl}/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      if (!res.ok) {
        let detail = "The demo workspace isn't reachable. Please try again shortly.";
        try {
          // FastAPI's `detail` is a string for auth errors but an array for validation
          // errors; only surface it when it is a human-readable string.
          const body = (await res.json()) as { detail?: unknown };
          if (typeof body?.detail === "string") detail = body.detail;
        } catch {
          /* non-JSON error body */
        }
        throw new Error(detail);
      }
      const tokens = (await res.json()) as AuthTokens;
      auth.setSession(tokens.access_token, tokens.refresh_token);

      // Resolve the active org so the dashboard boots against the demo tenant.
      try {
        const me = await api.get<CurrentUser>("/users/me");
        if (me.active_org?.id) auth.setActiveOrg(me.active_org.id);
      } catch {
        /* ignore - the dashboard's AuthProvider reconciles the active org */
      }

      toast.success("Welcome to the Third Brain demo");
      router.replace("/dashboard");
    } catch (err) {
      toast.error("Demo sign-in failed", {
        description:
          err instanceof Error
            ? err.message
            : "The demo workspace isn't reachable. Please try again shortly.",
      });
    } finally {
      setSubmitting(false);
    }
  }

  if (redirecting) return null;

  return (
    <Card className="w-full max-w-lg">
      <CardHeader className="space-y-1.5">
        <CardTitle className="text-[26px] tracking-[-0.02em]">
          Explore the live demo
        </CardTitle>
        <CardDescription className="text-[15px]">
          Sign in to <span className="font-medium text-foreground">Acme Inc.</span>, a
          pre-loaded workspace, with the credentials you were given. The same question
          returns a different answer to each identity, because retrieval respects
          permissions.
        </CardDescription>
      </CardHeader>

      <CardContent className="space-y-5">
        {/* Read-only legend of the demo identities - credentials are never shown here. */}
        <ul className="space-y-2" aria-label="Demo identities">
          {DEMO_ROLES.map((r) => (
            <li
              key={r.id}
              className="flex w-full items-start gap-3 rounded-lg border border-border p-3"
            >
              <span className="min-w-0 flex-1">
                <span className="flex items-center gap-2">
                  <span className="text-sm font-medium text-foreground">{r.name}</span>
                  <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
                    {r.role}
                  </span>
                </span>
                <span className="mt-0.5 block text-xs text-muted-foreground">
                  {r.blurb}
                </span>
              </span>
            </li>
          ))}
        </ul>

        <form onSubmit={onSubmit} noValidate className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="demo-email">Work email</Label>
            <Input
              id="demo-email"
              type="email"
              autoComplete="username"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="h-11 rounded-lg bg-background"
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="demo-password">Password</Label>
            <div className="relative">
              <Input
                id="demo-password"
                type={showPassword ? "text" : "password"}
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="h-11 rounded-lg bg-background pr-10"
              />
              <button
                type="button"
                onClick={() => setShowPassword((v) => !v)}
                className="absolute inset-y-0 right-0 flex w-10 items-center justify-center text-muted-foreground transition-colors hover:text-foreground"
                aria-label={showPassword ? "Hide password" : "Show password"}
                tabIndex={-1}
              >
                {showPassword ? (
                  <EyeOff className="h-4 w-4" />
                ) : (
                  <Eye className="h-4 w-4" />
                )}
              </button>
            </div>
          </div>

          <Button type="submit" className="h-11 w-full" disabled={submitting}>
            {submitting && <Loader2 className="h-4 w-4 animate-spin" />}
            {submitting ? "Signing in…" : "Sign in"}
          </Button>
        </form>
      </CardContent>

      <CardFooter>
        <p className="text-xs text-muted-foreground">
          This is a private demo workspace - please don&apos;t enter real or sensitive
          data. Signing in requires the workspace credentials.
        </p>
      </CardFooter>
    </Card>
  );
}
