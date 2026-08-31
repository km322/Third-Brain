"use client";

import * as React from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Loader2 } from "lucide-react";
import { toast } from "sonner";

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
import { api, ApiError, auth } from "@/lib/api";
import type { AuthTokens, CurrentUser } from "@/lib/types";

function AcceptInviteInner() {
  const router = useRouter();
  const params = useSearchParams();
  const token = params.get("token");

  const [fullName, setFullName] = React.useState("");
  const [password, setPassword] = React.useState("");
  const [submitting, setSubmitting] = React.useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!token || !fullName.trim() || password.length < 8) return;
    setSubmitting(true);
    try {
      const tokens = await api.post<AuthTokens>("/invites/accept", {
        token,
        full_name: fullName.trim(),
        password,
      });
      auth.setSession(tokens.access_token, tokens.refresh_token);
      try {
        const me = await api.get<CurrentUser>("/users/me");
        if (me.active_org?.id) auth.setActiveOrg(me.active_org.id);
      } catch {
        /* reconciled on the dashboard */
      }
      toast.success("Welcome to Third Brain");
      router.replace("/dashboard");
    } catch (err) {
      toast.error(
        err instanceof ApiError ? err.message : "Couldn't accept the invitation",
      );
    } finally {
      setSubmitting(false);
    }
  }

  if (!token) {
    return (
      <Card className="border-0 bg-transparent shadow-none">
        <CardHeader className="space-y-1.5 p-0">
          <CardTitle className="text-[28px] tracking-[-0.02em] text-foreground">
            Invitation link invalid
          </CardTitle>
          <CardDescription className="text-[15px]">
            This link is missing its invitation token. Ask your admin to resend it.
          </CardDescription>
        </CardHeader>
        <CardContent className="p-0 pt-8">
          <Link
            href="/login"
            className="text-sm font-medium text-primary hover:underline"
          >
            Back to sign in
          </Link>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card className="border-0 bg-transparent shadow-none">
      <CardHeader className="space-y-1.5 p-0">
        <CardTitle className="text-[28px] tracking-[-0.02em] text-foreground">
          Accept your invitation
        </CardTitle>
        <CardDescription className="text-[15px]">
          Choose a name and password to join your team on Third Brain.
        </CardDescription>
      </CardHeader>
      <form onSubmit={onSubmit} noValidate>
        <CardContent className="space-y-5 p-0 pt-8">
          <div className="space-y-2">
            <Label htmlFor="full-name">Full name</Label>
            <Input
              id="full-name"
              autoComplete="name"
              className="h-11 rounded-lg bg-background"
              value={fullName}
              onChange={(e) => setFullName(e.target.value)}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="password">Password</Label>
            <Input
              id="password"
              type="password"
              autoComplete="new-password"
              placeholder="At least 8 characters"
              className="h-11 rounded-lg bg-background"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
            {password.length > 0 && password.length < 8 ? (
              <p className="text-sm text-destructive">Use at least 8 characters.</p>
            ) : null}
          </div>
        </CardContent>
        <CardFooter className="p-0 pt-6">
          <Button
            type="submit"
            className="h-11 w-full"
            disabled={submitting || !fullName.trim() || password.length < 8}
          >
            {submitting && <Loader2 className="h-4 w-4 animate-spin" />}
            {submitting ? "Joining…" : "Accept invitation"}
          </Button>
        </CardFooter>
      </form>
    </Card>
  );
}

export default function AcceptInvitePage() {
  return (
    <React.Suspense fallback={<div className="h-4" />}>
      <AcceptInviteInner />
    </React.Suspense>
  );
}
