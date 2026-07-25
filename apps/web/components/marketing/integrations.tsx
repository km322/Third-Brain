interface ManifestRow {
  label: string;
  value: string;
}

const MANIFEST: ManifestRow[] = [
  {
    label: "Generate with",
    value:
      "Anthropic · Google Gemini · OpenAI · Azure OpenAI · Ollama · vLLM · Any OpenAI-compatible endpoint",
  },
  {
    label: "Connect from",
    value: "Claude · Claude Code · Cursor via MCP · Any OpenAI SDK",
  },
  {
    label: "Capture from",
    value: "Agents via add_knowledge · Files · Text · URLs",
  },
];

/** The real API surface, verified against apps/api. */
const ENDPOINTS: { method: string; path: string; note?: string }[] = [
  { method: "POST", path: "/v1/chat/completions" },
  { method: "POST", path: "/v1/embeddings" },
  { method: "GET", path: "/v1/models" },
  { method: "POST", path: "/api/v1/device-auth", note: "CLI sign-in" },
  { method: "MCP", path: "add_knowledge", note: "write-back" },
];

export function Integrations() {
  return (
    <section id="integrations" className="scroll-mt-20 py-28 sm:py-32 lg:py-40">
      <div className="container">
        <div className="mx-auto max-w-2xl text-center">
          <h2 className="text-balance text-4xl font-semibold leading-[1.08] tracking-[-0.025em] sm:text-5xl">
            Works with what you already run.
          </h2>
          <p className="mt-6 text-pretty text-lg leading-relaxed text-muted-foreground sm:text-xl">
            One command wires Claude, Claude Code, Cursor or your agents into the
            brain over MCP. Generate with Anthropic, Gemini or any
            OpenAI-compatible endpoint. No lock-in.
          </p>
        </div>

        {/* Featured install one-liner - the fastest way in. */}
        <div className="mx-auto mt-12 max-w-3xl">
          <div className="flex items-baseline gap-3 rounded-lg border border-border/60 bg-muted/20 px-4 py-3 font-mono text-[13px]">
            <span className="shrink-0 text-muted-foreground/70">$</span>
            <span className="text-foreground">npx third-brain-mcp connect</span>
          </div>
          <p className="mt-2 text-center text-xs text-muted-foreground">
            Then <span className="font-mono">install claude</span>,{" "}
            <span className="font-mono">cursor</span> or{" "}
            <span className="font-mono">claude-code</span> to wire in your client.
          </p>
        </div>

        <div className="mx-auto mt-12 max-w-3xl divide-y divide-border/60">
          {MANIFEST.map((row) => (
            <div
              key={row.label}
              className="grid gap-4 py-5 sm:grid-cols-[160px_1fr]"
            >
              <p className="text-sm font-medium text-foreground">{row.label}</p>
              <p className="text-sm text-muted-foreground">{row.value}</p>
            </div>
          ))}
        </div>

        {/* The API surface itself - endpoints as machine truth. */}
        <div className="mx-auto mt-12 max-w-3xl divide-y divide-border/60 rounded-lg border border-border/60">
          {ENDPOINTS.map((endpoint) => (
            <p
              key={`${endpoint.method} ${endpoint.path}`}
              className="flex items-baseline gap-3 px-4 py-2.5 font-mono text-[13px]"
            >
              <span className="w-10 shrink-0 text-muted-foreground/70">
                {endpoint.method}
              </span>
              <span className="text-foreground">{endpoint.path}</span>
              {endpoint.note && (
                <span className="text-muted-foreground/70">
                  · {endpoint.note}
                </span>
              )}
            </p>
          ))}
        </div>

        <p className="mt-8 text-center text-xs text-muted-foreground">
          Slack, Drive &amp; Notion connectors are on the roadmap.
        </p>
      </div>
    </section>
  );
}
