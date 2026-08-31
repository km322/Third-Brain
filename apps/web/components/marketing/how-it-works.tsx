interface Step {
  step: string;
  title: string;
  description: string;
}

const STEPS: Step[] = [
  {
    step: "01",
    title: "Connect",
    description:
      "One command wires the tools your team already uses into the brain: npx third-brain-mcp connect signs you in, then install claude, cursor or claude-code does the rest.",
  },
  {
    step: "02",
    title: "Work",
    description:
      "Your agents search the brain for grounded answers and write decisions, answers and notes back to it as they go - no one stops to author a doc.",
  },
  {
    step: "03",
    title: "Documented",
    description:
      "Review and track it all on the dashboard. An Agent-written filter shows exactly what your agents captured, and every query is scoped to what the caller may see.",
  },
];

export function HowItWorks() {
  return (
    <section
      id="how-it-works"
      className="scroll-mt-20 border-y border-border/60 bg-muted/20 py-28 sm:py-32 lg:py-40"
    >
      <div className="container">
        <div className="mx-auto max-w-2xl text-center">
          <h2 className="text-balance text-4xl font-semibold leading-[1.08] tracking-[-0.025em] sm:text-5xl">
            Connect. Work. Documented.
          </h2>
          <p className="mt-6 text-pretty text-lg leading-relaxed text-muted-foreground sm:text-xl">
            The knowledge captures itself as your team works - and every read and write is
            scoped to what the caller is allowed to see.
          </p>
        </div>

        <div className="mx-auto mt-16 grid max-w-4xl gap-12 sm:grid-cols-3">
          {STEPS.map((step) => (
            <div key={step.step}>
              <span className="font-mono text-xs text-muted-foreground/70">
                {step.step}
              </span>
              <h3 className="mt-3 text-lg font-semibold">{step.title}</h3>
              <p className="mt-2 text-[15px] leading-relaxed text-muted-foreground">
                {step.description}
              </p>
            </div>
          ))}
        </div>

        {/* The permission gate, as a set-piece sentence. */}
        <div className="mx-auto mt-20 max-w-2xl text-center">
          <p className="text-balance text-xl font-medium leading-snug tracking-[-0.01em] text-foreground sm:text-2xl">
            Before any chunk reaches a model, the permission gate drops everything the
            caller can&apos;t access.
          </p>
          <p className="mt-4 text-xs text-muted-foreground">
            <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-xs">
              scope = viewer+
            </code>{" "}
            · enforced server-side
          </p>
        </div>
      </div>
    </section>
  );
}
