import Link from "next/link";
import { Bot, FileText, ShieldCheck } from "lucide-react";

import { Button } from "@/components/ui/button";

/** The captured-document card shown inside the product preview mock. */
function CapturedDoc({
  title,
  snippet,
  destination,
}: {
  title: string;
  snippet: string;
  destination: string;
}) {
  return (
    <div className="rounded-lg border border-border/60 bg-background/60 p-3">
      <div className="flex items-start gap-3">
        <FileText className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-2">
            <p className="truncate text-xs font-medium text-foreground">
              {title}
            </p>
            <span className="shrink-0 rounded bg-muted px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wide text-muted-foreground">
              Agent-written
            </span>
          </div>
          <p className="mt-0.5 line-clamp-1 text-[11px] text-muted-foreground">
            {snippet}
          </p>
          <p className="mt-1 font-mono text-[10px] text-muted-foreground/70">
            {destination}
          </p>
        </div>
      </div>
    </div>
  );
}

/**
 * Landing hero: the "The documentation writes itself." headline set in ink over
 * plain background, with the primary/secondary CTAs and a capture-moment mock
 * framed like hardware photography - hairline edge, one neutral shadow.
 */
export function Hero() {
  return (
    <section className="pb-24 pt-24 sm:pb-32 sm:pt-28 lg:pt-32">
      <div className="container">
        <div className="mx-auto flex max-w-4xl flex-col items-center text-center">
          <h1 className="animate-fade-up text-balance text-5xl font-semibold leading-[1.05] tracking-[-0.03em] text-foreground sm:text-6xl lg:text-7xl">
            The documentation writes itself.
          </h1>

          <p className="animate-fade-up mt-6 max-w-2xl text-pretty text-lg leading-relaxed text-muted-foreground sm:text-xl">
            Connect Third Brain to the tools your team already uses - Claude,
            Cursor, your agents - and every decision and answer is captured into
            a governed brain your whole company can search. Permissions enforced
            in the query. Works with every model.
          </p>

          <div className="animate-fade-up mt-10 flex flex-col items-center gap-3 sm:flex-row">
            <Button
              asChild
              size="lg"
              className="h-12 w-full rounded-full px-7 sm:w-auto"
            >
              <Link href="#waitlist">Join the waitlist</Link>
            </Button>
          </div>

          <p className="animate-fade-up mt-6 text-xs text-muted-foreground">
            Early access · No credit card required · Bring your own model keys
          </p>
        </div>

        {/* Product preview mock - the page's one proof element: an agent
            capturing a decision as it works, and where it lands. */}
        <div className="animate-fade-up mx-auto mt-20 max-w-4xl lg:mt-24">
          <div className="overflow-hidden rounded-2xl border border-border bg-card shadow-[0_1px_1px_rgba(0,0,0,0.03),0_12px_32px_-8px_rgba(0,0,0,0.08)] ring-1 ring-black/[0.04] dark:shadow-none dark:ring-white/[0.08]">
            {/* Window chrome */}
            <div className="flex items-center gap-2 border-b border-border/60 bg-muted/30 px-4 py-3">
              <span className="h-2.5 w-2.5 rounded-full bg-foreground/[0.12]" />
              <span className="h-2.5 w-2.5 rounded-full bg-foreground/[0.12]" />
              <span className="h-2.5 w-2.5 rounded-full bg-foreground/[0.12]" />
              <span className="ml-3 truncate font-mono text-xs text-muted-foreground">
                claude · third-brain
              </span>
            </div>

            <div className="grid gap-0 sm:grid-cols-5">
              {/* Agent session column - the work happening. */}
              <div className="space-y-4 border-b border-border/60 p-5 sm:col-span-3 sm:border-b-0 sm:border-r">
                <div className="flex items-center gap-2 rounded-lg bg-muted/50 px-3 py-2 text-sm text-muted-foreground">
                  <Bot className="h-4 w-4 text-muted-foreground" />
                  Agent session
                </div>
                <div className="space-y-2 text-sm leading-relaxed text-foreground">
                  <p>
                    We&apos;re moving on-call to a{" "}
                    <span className="font-medium">weekly rotation</span> with a
                    secondary. Documenting that now so the team has it.
                  </p>
                </div>
                <div className="flex items-center gap-2 rounded-md border border-border/60 px-3 py-2 font-mono text-[12px] text-muted-foreground">
                  <span className="text-muted-foreground/70">call</span>
                  add_knowledge → Engineering / Decisions
                </div>
              </div>

              {/* Captured column - what landed in the brain. */}
              <div className="space-y-2 bg-muted/20 p-5 sm:col-span-2">
                <p className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                  Captured
                </p>
                <CapturedDoc
                  title="On-call rotation change"
                  snippet="On-call moves to a weekly rotation with a secondary…"
                  destination="Engineering / Decisions"
                />
                <div className="flex items-center gap-2 rounded-lg border border-dashed border-border/70 px-3 py-2 text-[11px] text-muted-foreground">
                  <ShieldCheck className="h-3.5 w-3.5 text-muted-foreground" />
                  Visible to Engineering · Board &amp; Finance never sees it
                </div>
              </div>
            </div>
          </div>

          {/* Figure caption - left-aligned to the frame edge. */}
          <p className="mt-4 flex items-baseline gap-2 text-sm text-muted-foreground">
            <span className="font-mono text-xs text-muted-foreground/70">
              Fig. 1
            </span>
            An agent captures a decision as it works - a teammate finds it
            seconds later, scoped by permissions.
          </p>
        </div>

        {/* Works-with caption line. */}
        <p className="mt-10 text-center text-[13px] text-muted-foreground">
          Works with Anthropic Claude · Google Gemini · any OpenAI-compatible
          model · Claude, Claude Code &amp; Cursor via MCP
        </p>
      </div>
    </section>
  );
}
