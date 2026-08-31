"use client";

import * as React from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useForm, type Resolver } from "react-hook-form";
import { z } from "zod";
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
import { api, ApiError, auth } from "@/lib/api";
import { useRedirectIfAuthenticated } from "@/lib/use-redirect-if-authenticated";
import type { AuthTokens, CurrentUser } from "@/lib/types";

/**
 * Minimal react-hook-form resolver backed by zod. We wire it by hand rather
 * than pulling in `@hookform/resolvers` (which isn't a project dependency);
 * both react-hook-form and zod are, so this keeps the surface consistent.
 */
function zodResolver<TValues extends Record<string, unknown>>(
  schema: z.ZodType<TValues>,
): Resolver<TValues> {
  return (async (values: TValues) => {
    const parsed = schema.safeParse(values);
    if (parsed.success) return { values: parsed.data, errors: {} };
    const errors: Record<string, { type: string; message: string }> = {};
    for (const issue of parsed.error.issues) {
      const path = issue.path.join(".");
      if (path && !errors[path]) {
        errors[path] = { type: String(issue.code), message: issue.message };
      }
    }
    return { values: {}, errors };
  }) as Resolver<TValues>;
}

const loginSchema = z.object({
  email: z.string().min(1, "Email is required").email("Enter a valid email address"),
  password: z.string().min(1, "Password is required"),
});

type LoginValues = z.infer<typeof loginSchema>;

/**
 * The validated `?next=` deep link to resume after login, or `null` when absent or
 * unsafe. The value is resolved against our own origin and accepted only if it stays
 * same-origin; parsing (rather than string-prefix checks) rejects "//host", backslash
 * tricks like "/\\host", and control-character bypasses the browser would normalize when
 * navigating, so a crafted `?next=` can never redirect off-site.
 */
function safeNextPath(): string | null {
  const raw = new URLSearchParams(window.location.search).get("next");
  if (!raw) return null;
  try {
    const url = new URL(raw, window.location.origin);
    if (url.origin !== window.location.origin) return null;
    return url.pathname + url.search + url.hash;
  } catch {
    return null;
  }
}

export default function LoginPage() {
  const router = useRouter();
  const [showPassword, setShowPassword] = React.useState(false);

  // Already signed in: skip the form and go straight to the app (before paint).
  const redirecting = useRedirectIfAuthenticated(() => safeNextPath() ?? "/dashboard");

  const {
    register,
    handleSubmit,
    getValues,
    formState: { errors, isSubmitting },
  } = useForm<LoginValues>({
    resolver: zodResolver(loginSchema),
    defaultValues: { email: "", password: "" },
  });

  const [ssoLoading, setSsoLoading] = React.useState(false);

  // Single sign-on: look up an enabled connection for the typed email's domain and
  // bounce to the IdP. The email/password form above is untouched.
  async function continueWithSso() {
    const email = getValues("email");
    if (!email) {
      toast.error("Enter your work email to continue with SSO");
      return;
    }
    setSsoLoading(true);
    try {
      const connections = await api.get<{ id: string; name: string; protocol: string }[]>(
        "/auth/sso/available",
        { email },
      );
      if (connections.length === 0) {
        toast.error("No single sign-on is configured for that email domain.");
        return;
      }
      const start = await api.get<{ url: string }>("/auth/sso/start", {
        connection_id: connections[0].id,
      });
      window.location.href = start.url;
    } catch (err) {
      toast.error(
        err instanceof ApiError ? err.message : "Couldn't start single sign-on",
      );
    } finally {
      setSsoLoading(false);
    }
  }

  async function onSubmit(values: LoginValues) {
    try {
      const tokens = await api.post<AuthTokens>("/auth/login", values);
      auth.setSession(tokens.access_token, tokens.refresh_token);

      // Resolve the active org so the dashboard boots against the right tenant.
      // Non-fatal: the dashboard's AuthProvider re-fetches identity regardless.
      try {
        const me = await api.get<CurrentUser>("/users/me");
        if (me.active_org?.id) auth.setActiveOrg(me.active_org.id);
      } catch {
        /* ignore - active org will be reconciled on the dashboard */
      }

      toast.success("Welcome back");
      router.replace(safeNextPath() ?? "/dashboard");
    } catch (err) {
      const message =
        err instanceof ApiError ? err.message : "Something went wrong. Please try again.";
      toast.error("Sign in failed", { description: message });
    }
  }

  if (redirecting) return null;

  return (
    <Card className="border-0 bg-transparent shadow-none">
      <CardHeader className="space-y-1.5 p-0">
        <CardTitle className="text-[28px] tracking-[-0.02em] text-foreground">
          Welcome back
        </CardTitle>
        <CardDescription className="text-[15px]">
          Sign in to your Third Brain workspace.
        </CardDescription>
      </CardHeader>

      <form onSubmit={handleSubmit(onSubmit)} noValidate>
        <CardContent className="space-y-5 p-0 pt-8">
          <div className="space-y-2">
            <Label htmlFor="email">Work email</Label>
            <Input
              id="email"
              type="email"
              autoComplete="email"
              placeholder="you@company.com"
              aria-invalid={!!errors.email}
              className="h-11 rounded-lg bg-background"
              {...register("email")}
            />
            {errors.email && (
              <p className="text-sm text-destructive">{errors.email.message}</p>
            )}
          </div>

          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <Label htmlFor="password">Password</Label>
              <Link
                href="/forgot-password"
                className="text-sm font-medium text-primary hover:underline"
              >
                Forgot password?
              </Link>
            </div>
            <div className="relative">
              <Input
                id="password"
                type={showPassword ? "text" : "password"}
                autoComplete="current-password"
                placeholder="••••••••"
                aria-invalid={!!errors.password}
                className="h-11 rounded-lg bg-background pr-10"
                {...register("password")}
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
            {errors.password && (
              <p className="text-sm text-destructive">{errors.password.message}</p>
            )}
          </div>
        </CardContent>

        <CardFooter className="flex-col gap-4 p-0 pt-6">
          <Button type="submit" className="h-11 w-full" disabled={isSubmitting}>
            {isSubmitting && <Loader2 className="h-4 w-4 animate-spin" />}
            {isSubmitting ? "Signing in…" : "Sign in"}
          </Button>
          <p className="text-center text-sm text-muted-foreground">
            Don&apos;t have an account?{" "}
            <Link href="/signup" className="font-medium text-primary hover:underline">
              Create one
            </Link>
          </p>
        </CardFooter>
      </form>

      <div className="pt-6">
        <div className="relative py-1">
          <div className="absolute inset-0 flex items-center" aria-hidden>
            <span className="w-full border-t" />
          </div>
          <div className="relative flex justify-center text-xs">
            <span className="px-2 text-muted-foreground">or</span>
          </div>
        </div>
        <Button
          type="button"
          variant="outline"
          className="mt-3 h-11 w-full"
          disabled={ssoLoading}
          onClick={continueWithSso}
        >
          {ssoLoading && <Loader2 className="h-4 w-4 animate-spin" />}
          Continue with SSO
        </Button>
      </div>
    </Card>
  );
}
