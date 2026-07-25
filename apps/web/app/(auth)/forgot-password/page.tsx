"use client";

import * as React from "react";
import Link from "next/link";
import { useForm, type Resolver } from "react-hook-form";
import { z } from "zod";
import { ArrowLeft, CheckCircle2, Loader2 } from "lucide-react";

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

const forgotSchema = z.object({
  email: z
    .string()
    .min(1, "Email is required")
    .email("Enter a valid email address"),
});

type ForgotValues = z.infer<typeof forgotSchema>;

export default function ForgotPasswordPage() {
  // Self-service (emailed) reset needs a delivery pipeline the backend does not have yet.
  // What exists today: an owner/admin resets the password from the Members page and hands
  // the user a temporary password, which they change under Settings > Security. Rather
  // than falsely claim a link was sent, this page explains that flow. `submitted` just
  // toggles the guidance card.
  const [submitted, setSubmitted] = React.useState(false);

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<ForgotValues>({
    resolver: zodResolver(forgotSchema),
    defaultValues: { email: "" },
  });

  async function onSubmit() {
    setSubmitted(true);
  }

  if (submitted) {
    return (
      <Card className="border-0 bg-transparent shadow-none">
        <CardHeader className="items-center space-y-3 p-0 text-center">
          <CheckCircle2 className="h-7 w-7 text-muted-foreground" aria-hidden />
          <CardTitle className="text-[28px] tracking-[-0.02em] text-foreground">
            Ask an admin to help
          </CardTitle>
          <CardDescription className="text-[15px]">
            Ask an owner or admin of your organization to reset your password
            from the Members page. They&apos;ll give you a temporary password:
            sign in with it, then set a new one in Settings &gt; Security.
          </CardDescription>
        </CardHeader>
        <CardFooter className="flex-col gap-3 p-0 pt-8">
          <Button asChild className="h-11 w-full">
            <Link href="/login">Back to sign in</Link>
          </Button>
        </CardFooter>
      </Card>
    );
  }

  return (
    <Card className="border-0 bg-transparent shadow-none">
      <CardHeader className="space-y-1.5 p-0">
        <CardTitle className="text-[28px] tracking-[-0.02em] text-foreground">
          Trouble signing in?
        </CardTitle>
        <CardDescription className="text-[15px]">
          Password reset is handled by your organization&apos;s admins. Enter your email
          and we&apos;ll show you how to regain access.
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
        </CardContent>

        <CardFooter className="flex-col gap-4 p-0 pt-6">
          <Button type="submit" className="h-11 w-full" disabled={isSubmitting}>
            {isSubmitting && <Loader2 className="h-4 w-4 animate-spin" />}
            Continue
          </Button>
          <Link
            href="/login"
            className="inline-flex items-center gap-1.5 text-sm font-medium text-muted-foreground transition-colors hover:text-foreground"
          >
            <ArrowLeft className="h-4 w-4" />
            Back to sign in
          </Link>
        </CardFooter>
      </form>
    </Card>
  );
}
