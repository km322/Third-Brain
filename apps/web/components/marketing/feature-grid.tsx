import type { ReactNode } from "react";

interface Statement {
  title: string;
  body: ReactNode;
  proof: string;
}

const STATEMENTS: Statement[] = [
  {
    title: "The documentation writes itself.",
    body: (
      <>
        Point your agents at Third Brain over MCP and, as they work, they call{" "}
        <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-xs">
          add_knowledge
        </code>{" "}
        and{" "}
        <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-xs">
          update_knowledge
        </code>{" "}
        to capture decisions, answers and notes into the right collection. The doc gets
        written without anyone stopping to write it - and the secret scanner gates what an
        agent is allowed to persist.
      </>
    ),
    proof: "MCP add_knowledge / update_knowledge",
  },
  {
    title: "Permission is enforced in the query.",
    body: (
      <>
        Every query - a read or a write-back - is filtered by document-level access
        grants, server-side, before any chunk reaches the model. Immutable audit logs,
        per-key rate limits and usage metering keep a complete record of who asked what,
        and what the agents wrote.
      </>
    ),
    proof: "scope = viewer+ · enforced in SQL",
  },
  {
    title: "Any model. Zero lock-in.",
    body: (
      <>
        Native connectors for Anthropic and Google Gemini, plus any OpenAI-compatible
        endpoint - OpenAI, Azure OpenAI, Ollama, vLLM or your own gateway - all behind one
        plain-httpx layer. Drop-in /v1/chat/completions and /v1/embeddings plus a native
        MCP server mean your existing tools just work, and you can swap providers without
        re-indexing a single document.
      </>
    ),
    proof: "Anthropic · Gemini · OpenAI-compatible",
  },
];

export function FeatureGrid() {
  return (
    <section id="features" className="scroll-mt-20 py-28 sm:py-32 lg:py-40">
      <div className="container">
        <div className="mx-auto max-w-2xl text-center">
          <h2 className="text-balance text-4xl font-semibold leading-[1.08] tracking-[-0.025em] sm:text-5xl">
            A brain that fills itself in
          </h2>
          <p className="mt-6 text-pretty text-lg leading-relaxed text-muted-foreground sm:text-xl">
            The knowledge your team creates with LLMs, captured into one governed source
            of truth - searchable by every model, the moment it&apos;s written.
          </p>
        </div>

        <div className="mx-auto mt-16 max-w-5xl divide-y divide-border/60 lg:mt-20">
          {STATEMENTS.map((statement) => (
            <div
              key={statement.title}
              className="grid gap-10 py-16 lg:grid-cols-12 lg:py-20"
            >
              <h3 className="text-balance text-3xl font-semibold tracking-[-0.02em] sm:text-4xl lg:col-span-5">
                {statement.title}
              </h3>
              <div className="lg:col-span-6 lg:col-start-7">
                <p className="max-w-lg text-base leading-relaxed text-muted-foreground">
                  {statement.body}
                </p>
                <p className="mt-6 inline-block rounded-md border border-border/60 px-3 py-2 font-mono text-[13px] text-muted-foreground">
                  {statement.proof}
                </p>
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
