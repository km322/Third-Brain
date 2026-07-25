"use client";

import * as React from "react";
import { CheckCircle2, Loader2 } from "lucide-react";
import { toast } from "sonner";

import { Turnstile, TURNSTILE_SITE_KEY } from "@/components/marketing/turnstile";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api, ApiError } from "@/lib/api";
import { sendWaitlistNotification } from "@/lib/emailjs";

/** Only surface a signup count once it reads as social proof rather than "you're first". */
const SOCIAL_PROOF_THRESHOLD = 25;

/**
 * Closing conversion section for the waitlist-first launch. Captures an email, verifies the
 * visitor with Turnstile (when configured), persists the signup via the backend and fires a
 * best-effort EmailJS notification. On success the form is replaced by a confirmation.
 */
export function Waitlist() {
  const [email, setEmail] = React.useState("");
  const [honeypot, setHoneypot] = React.useState("");
  const [token, setToken] = React.useState("");
  const [resetSignal, setResetSignal] = React.useState(0);
  const [submitting, setSubmitting] = React.useState(false);
  const [submitted, setSubmitted] = React.useState(false);
  const [count, setCount] = React.useState<number | null>(null);

  React.useEffect(() => {
    let active = true;
    api
      .get<{ count: number }>("/waitlist/stats")
      .then((s) => active && setCount(s.count))
      .catch(() => {
        /* social proof is optional - ignore a failed count */
      });
    return () => {
      active = false;
    };
  }, []);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();

    // Honeypot: a real user never fills this. Pretend success without hitting the API.
    if (honeypot) {
      setSubmitted(true);
      return;
    }
    if (TURNSTILE_SITE_KEY && !token) {
      toast.error("Please complete the verification below.");
      return;
    }

    setSubmitting(true);
    try {
      await api.post("/waitlist", {
        email,
        source: "landing",
        turnstile_token: token || undefined,
        company_website: honeypot || undefined,
      });
      // Fire-and-forget: the signup is already durably recorded server-side.
      void sendWaitlistNotification({ email, source: "landing" });
      setSubmitted(true);
      setCount((c) => (c === null ? c : c + 1));
    } catch (err) {
      toast.error("Couldn't join the waitlist", {
        description:
          err instanceof ApiError
            ? err.message
            : "Something went wrong. Please try again.",
      });
      // A Turnstile token is single-use; force a fresh one for the retry.
      setResetSignal((s) => s + 1);
      setToken("");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section id="waitlist" className="scroll-mt-20 py-32 lg:py-40">
      <div className="container">
        <div className="mx-auto max-w-2xl text-center">
          <h2 className="text-balance text-4xl font-semibold leading-[1.08] tracking-[-0.025em] sm:text-5xl lg:text-6xl">
            Let the documentation write itself.
          </h2>
          <p className="mx-auto mt-6 max-w-xl text-pretty text-lg leading-relaxed text-muted-foreground sm:text-xl">
            Third Brain is opening access in waves. Join the waitlist and we&apos;ll
            reach out when your workspace is ready - so your agents can start
            capturing the work, permissions built in from the first query.
          </p>

          {submitted ? (
            <div className="mx-auto mt-10 flex max-w-md items-center gap-3 rounded-2xl border border-primary/30 bg-primary/5 p-5 text-left">
              <CheckCircle2 className="h-6 w-6 shrink-0 text-primary" />
              <div>
                <p className="text-sm font-semibold text-foreground">
                  You&apos;re on the list.
                </p>
                <p className="text-sm text-muted-foreground">
                  We&apos;ll email you the moment your spot opens up.
                </p>
              </div>
            </div>
          ) : (
            <>
              <form
                onSubmit={onSubmit}
                className="mx-auto mt-10 flex max-w-md flex-col gap-3 sm:flex-row"
              >
                {/* Honeypot: hidden from users + assistive tech, catches naive bots. */}
                <div
                  aria-hidden
                  className="pointer-events-none absolute left-[-9999px] h-0 w-0 overflow-hidden"
                >
                  <label htmlFor="company_website">Company website</label>
                  <input
                    id="company_website"
                    name="company_website"
                    type="text"
                    tabIndex={-1}
                    autoComplete="off"
                    value={honeypot}
                    onChange={(e) => setHoneypot(e.target.value)}
                  />
                </div>

                <Input
                  type="email"
                  required
                  autoComplete="email"
                  placeholder="you@company.com"
                  aria-label="Work email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  disabled={submitting}
                  className="h-12 flex-1 rounded-full bg-background px-5 text-base"
                />
                <Button
                  type="submit"
                  size="lg"
                  disabled={submitting}
                  className="h-12 shrink-0 rounded-full px-7"
                >
                  {submitting && <Loader2 className="h-4 w-4 animate-spin" />}
                  {submitting ? "Joining…" : "Join the waitlist"}
                </Button>
              </form>

              {TURNSTILE_SITE_KEY && (
                <div className="mt-5 flex justify-center">
                  <Turnstile onToken={setToken} resetSignal={resetSignal} />
                </div>
              )}

              <p className="mt-4 text-xs text-muted-foreground">
                {count !== null && count >= SOCIAL_PROOF_THRESHOLD
                  ? `Join ${count.toLocaleString()} teams already on the list. No spam - unsubscribe anytime.`
                  : "No spam, ever. We'll only email you about early access."}
              </p>
            </>
          )}
        </div>
      </div>
    </section>
  );
}
