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
 * Minimal react-hook-form resolver backed by zod (see login page for the
 * rationale - we avoid the extra `@hookform/resolvers` dependency).
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

const signupSchema = z.object({
  full_name: z.string().min(1, "Your name is required").max(120, "That name is too long"),
  email: z.string().min(1, "Email is required").email("Enter a valid email address"),
  password: z.string().min(8, "Password must be at least 8 characters"),
  org_name: z
    .string()
    .min(1, "Organization name is required")
    .max(120, "That name is too long"),
});

type SignupValues = z.infer<typeof signupSchema>;

/**
 * Create-workspace form: one step that registers the person and their new organization.
 * A visitor who is already signed in skips the form and goes straight to the app before
 * paint.
 */
export default function SignupPage() {
  const router = useRouter();
  const [showPassword, setShowPassword] = React.useState(false);

  const redirecting = useRedirectIfAuthenticated(() => "/dashboard");

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<SignupValues>({
    resolver: zodResolver(signupSchema),
    defaultValues: { full_name: "", email: "", password: "", org_name: "" },
  });

  /**
   * Register the account and its organization, then persist the freshly created org as
   * active before entering the app. That second call is non-fatal: a failure is ignored,
   * because the active org will be reconciled on the dashboard.
   */
  async function onSubmit(values: SignupValues) {
    try {
      const tokens = await api.post<AuthTokens>("/auth/register", values);
      auth.setSession(tokens.access_token, tokens.refresh_token);

      try {
        const me = await api.get<CurrentUser>("/users/me");
        if (me.active_org?.id) auth.setActiveOrg(me.active_org.id);
      } catch {}

      toast.success("Workspace created", {
        description: `Welcome to Third Brain, ${values.full_name.split(" ")[0]}.`,
      });
      router.replace("/dashboard");
    } catch (err) {
      const message =
        err instanceof ApiError ? err.message : "Something went wrong. Please try again.";
      toast.error("Could not create your account", { description: message });
    }
  }

  if (redirecting) return null;

  return (
    <Card className="border-0 bg-transparent shadow-none">
      <CardHeader className="space-y-1.5 p-0">
        <CardTitle className="text-[28px] tracking-[-0.02em] text-foreground">
          Create your workspace
        </CardTitle>
        <CardDescription className="text-[15px]">
          Start capturing your team&apos;s work into one governed brain.
        </CardDescription>
      </CardHeader>

      <form onSubmit={handleSubmit(onSubmit)} noValidate>
        <CardContent className="space-y-5 p-0 pt-8">
          <div className="space-y-2">
            <Label htmlFor="full_name">Full name</Label>
            <Input
              id="full_name"
              type="text"
              autoComplete="name"
              placeholder="Ada Lovelace"
              aria-invalid={!!errors.full_name}
              className="h-11 rounded-lg bg-background"
              {...register("full_name")}
            />
            {errors.full_name && (
              <p className="text-sm text-destructive">{errors.full_name.message}</p>
            )}
          </div>

          <div className="space-y-2">
            <Label htmlFor="org_name">Organization name</Label>
            <Input
              id="org_name"
              type="text"
              autoComplete="organization"
              placeholder="Acme Inc."
              aria-invalid={!!errors.org_name}
              className="h-11 rounded-lg bg-background"
              {...register("org_name")}
            />
            {errors.org_name && (
              <p className="text-sm text-destructive">{errors.org_name.message}</p>
            )}
          </div>

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
            <Label htmlFor="password">Password</Label>
            <div className="relative">
              <Input
                id="password"
                type={showPassword ? "text" : "password"}
                autoComplete="new-password"
                placeholder="At least 8 characters"
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
            {isSubmitting ? "Creating workspace…" : "Create workspace"}
          </Button>
          <p className="text-center text-sm text-muted-foreground">
            Already have an account?{" "}
            <Link href="/login" className="font-medium text-primary hover:underline">
              Sign in
            </Link>
          </p>
        </CardFooter>
      </form>
    </Card>
  );
}
