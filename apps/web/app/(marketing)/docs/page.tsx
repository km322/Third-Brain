import type { Metadata } from "next";
import Link from "next/link";
import type { ReactNode } from "react";

import { Cta } from "@/components/marketing/cta";
import { cn } from "@/lib/utils";

export const metadata: Metadata = {
  title: "Documentation",
  description:
    "Get started with Third Brain: connect Claude, Claude Code and Cursor over MCP, bring your own model keys, and let your agents write documentation as they work - all governed by document-level permissions.",
};

interface SectionMeta {
  id: string;
  index: string;
  title: string;
}

const SECTIONS: SectionMeta[] = [
  { id: "quick-start", index: "01", title: "Quick start" },
  { id: "connect-tools", index: "02", title: "Connect your tools" },
  { id: "bring-models", index: "03", title: "Bring your models" },
  { id: "agents-write", index: "04", title: "How agents write documentation" },
  { id: "from-code", index: "05", title: "Use it from code" },
  { id: "governance", index: "06", title: "Permissions and governance" },
];

// --- Real commands and API surfaces, cross-checked against docs/API.md and
// --- packages/mcp-cli/README.md. Kept as strings so the code renders verbatim.

const QUICK_START = `npx third-brain-mcp connect           # one-time sign-in via device code
npx third-brain-mcp install claude    # also: cursor, claude-code`;

const CONNECT = `npx third-brain-mcp connect
npx third-brain-mcp install claude    # also: cursor, claude-code
npx third-brain-mcp status`;

const PY_SNIPPET = `from openai import OpenAI

client = OpenAI(base_url="https://api.third-brain.ai/v1", api_key="tb_live_...")
resp = client.chat.completions.create(
    model="third-brain",
    messages=[{"role": "user", "content": "What is our on-call escalation policy?"}],
)
print(resp.choices[0].message.content)  # grounded in your brain, within your ACLs`;

const REST_SNIPPET = `export TB=https://api.third-brain.ai
export TB_KEY=tb_live_...

curl -X POST "$TB/api/v1/search/chat" \\
  -H "Authorization: Bearer $TB_KEY" -H 'Content-Type: application/json' \\
  -d '{"query": "Summarize our PTO policy for a new hire."}'`;

const MCP_CONFIG = `{
  "mcpServers": {
    "third-brain": {
      "url": "https://api.third-brain.ai/mcp",
      "headers": { "Authorization": "Bearer tb_live_..." }
    }
  }
}`;

const INSTALL_TARGETS: { cmd: string; desc: string }[] = [
  { cmd: "install claude", desc: "Writes the Claude Desktop config." },
  { cmd: "install cursor", desc: "Writes the Cursor config." },
  {
    cmd: "install claude-code",
    desc: "Prints the claude mcp add one-liner to paste.",
  },
];

const CONNECTORS: { type: string; note: string }[] = [
  { type: "openai", note: "OpenAI - completions and embeddings." },
  { type: "azure_openai", note: "Azure OpenAI deployments." },
  { type: "anthropic", note: "Anthropic Claude - completions only." },
  {
    type: "google",
    note: "Google Gemini - completions and embeddings (gemini-embedding-001).",
  },
  { type: "ollama", note: "Local models via Ollama." },
  { type: "custom", note: "Any other OpenAI-compatible endpoint via a base URL." },
];

const MCP_TOOLS: { name: string; desc: string }[] = [
  {
    name: "search_knowledge",
    desc: "Permission-aware semantic search; returns citations.",
  },
  { name: "get_document", desc: "Fetch a document you are allowed to view." },
  { name: "list_collections", desc: "List the knowledge bases you can see." },
  {
    name: "add_knowledge",
    desc: "Write a new document into a collection (needs an ingest or write scope).",
  },
  { name: "update_knowledge", desc: "Update an existing document's content." },
];

const ENDPOINTS: { method: string; path: string; note?: string }[] = [
  { method: "POST", path: "/v1/chat/completions", note: "grounded, OpenAI-compatible" },
  { method: "POST", path: "/v1/embeddings" },
  { method: "GET", path: "/v1/models" },
  { method: "POST", path: "/api/v1/search", note: "native retrieval" },
  { method: "POST", path: "/api/v1/search/chat", note: "native RAG" },
  { method: "POST", path: "/mcp", note: "JSON-RPC 2.0 for agents" },
];

/** Inline monospace token for endpoints, flags and other machine words. */
function Mono({ children }: { children: ReactNode }) {
  return (
    <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-[13px] text-foreground">
      {children}
    </code>
  );
}

/** Inline link in the violet link idiom - site routes and in-page anchors. */
function TextLink({ href, children }: { href: string; children: ReactNode }) {
  return (
    <Link
      href={href}
      className="text-primary underline-offset-4 hover:underline"
    >
      {children}
    </Link>
  );
}

/** One line of a code block, dimming a trailing `# ...` comment. */
function CodeLine({ line }: { line: string }) {
  if (line.trimStart().startsWith("#")) {
    return <span className="block text-muted-foreground/60">{line || " "}</span>;
  }
  const idx = line.indexOf("  #");
  if (idx === -1) {
    return <span className="block">{line || " "}</span>;
  }
  return (
    <span className="block">
      {line.slice(0, idx)}
      <span className="text-muted-foreground/60">{line.slice(idx)}</span>
    </span>
  );
}

/** Terminal-style code block matching the hero/integrations panel idiom. */
function CodeBlock({ label, code }: { label: string; code: string }) {
  const lines = code.replace(/\n$/, "").split("\n");
  return (
    <div className="overflow-hidden rounded-xl border border-border/60 bg-muted/20">
      <div className="flex items-center gap-2 border-b border-border/60 bg-muted/30 px-4 py-2.5">
        <span className="h-2.5 w-2.5 rounded-full bg-foreground/[0.12]" />
        <span className="h-2.5 w-2.5 rounded-full bg-foreground/[0.12]" />
        <span className="h-2.5 w-2.5 rounded-full bg-foreground/[0.12]" />
        <span className="ml-2 font-mono text-[11px] text-muted-foreground">{label}</span>
      </div>
      <pre className="overflow-x-auto px-4 py-4 font-mono text-[13px] leading-relaxed text-foreground">
        <code>
          {lines.map((line, i) => (
            <CodeLine key={i} line={line} />
          ))}
        </code>
      </pre>
    </div>
  );
}

/** A hairline-bordered list of `mono term -> description` rows. */
function DefinitionList({
  rows,
  termWidth = "sm:w-44",
}: {
  rows: { term: string; desc: string }[];
  termWidth?: string;
}) {
  return (
    <ul className="divide-y divide-border/60 rounded-lg border border-border/60">
      {rows.map((row) => (
        <li
          key={row.term}
          className="flex flex-col gap-1 px-4 py-3 sm:flex-row sm:items-baseline sm:gap-4"
        >
          <span
            className={cn("shrink-0 font-mono text-[13px] text-foreground", termWidth)}
          >
            {row.term}
          </span>
          <span className="text-sm text-muted-foreground">{row.desc}</span>
        </li>
      ))}
    </ul>
  );
}

function SubHeading({ children }: { children: ReactNode }) {
  return <h3 className="pt-1 text-base font-semibold text-foreground">{children}</h3>;
}

function Section({
  id,
  index,
  title,
  first = false,
  children,
}: {
  id: string;
  index: string;
  title: string;
  first?: boolean;
  children: ReactNode;
}) {
  return (
    <section
      id={id}
      className={cn("scroll-mt-24", first ? "pb-14 sm:pb-16" : "py-14 sm:py-16")}
    >
      <div className="flex items-baseline gap-3">
        <span className="font-mono text-xs text-muted-foreground/70">{index}</span>
        <h2 className="text-2xl font-semibold tracking-[-0.02em] sm:text-3xl">{title}</h2>
      </div>
      <div className="mt-6 space-y-5 text-[15px] leading-relaxed text-muted-foreground">
        {children}
      </div>
    </section>
  );
}

export default function DocsPage() {
  return (
    <>
      {/* Hero */}
      <section className="border-b border-border/60 pt-24 sm:pt-28 lg:pt-32">
        <div className="container pb-14 sm:pb-16">
          <div className="mx-auto max-w-6xl">
            <p className="font-mono text-xs uppercase tracking-wider text-muted-foreground/70">
              Documentation
            </p>
            <h1 className="mt-4 max-w-3xl text-balance text-4xl font-semibold leading-[1.08] tracking-[-0.03em] text-foreground sm:text-5xl">
              Everything you need to get started.
            </h1>
            <p className="mt-5 max-w-2xl text-pretty text-lg leading-relaxed text-muted-foreground">
              Connect the tools your team already uses over MCP, bring your own model
              keys, and let your agents write documentation as they work. Every command
              and endpoint below is real.
            </p>
          </div>
        </div>
      </section>

      {/* Body: on-page nav + ordered sections */}
      <div className="container py-14 sm:py-16">
        <div className="mx-auto max-w-6xl lg:grid lg:grid-cols-[200px_minmax(0,1fr)] lg:gap-16">
          <nav aria-label="On this page" className="mb-12 lg:mb-0">
            <div className="lg:sticky lg:top-24">
              <p className="font-mono text-[11px] uppercase tracking-wider text-muted-foreground/70">
                On this page
              </p>
              <ul className="mt-4 space-y-2.5">
                {SECTIONS.map((s) => (
                  <li key={s.id}>
                    <a
                      href={`#${s.id}`}
                      className="text-sm text-muted-foreground transition-colors hover:text-foreground"
                    >
                      {s.title}
                    </a>
                  </li>
                ))}
              </ul>
            </div>
          </nav>

          <div className="min-w-0 divide-y divide-border/60">
            <Section id="quick-start" index="01" title="Quick start" first>
              <p>
                Third Brain is a managed service, opening access in waves.{" "}
                <TextLink href="/#waitlist">Join the waitlist</TextLink> to get a
                workspace, or <TextLink href="/login">sign in</TextLink> if your team is
                already on board. Once you are in, two commands wire the tools your team
                already uses into the brain:
              </p>
              <CodeBlock label="shell" code={QUICK_START} />
              <p>
                <Mono>connect</Mono> signs this machine in with a one-time code that an
                org admin approves from the dashboard; <Mono>install</Mono> writes your
                client&apos;s config - Claude Desktop, Cursor, or Claude Code.{" "}
                <TextLink href="#connect-tools">Connect your tools</TextLink> walks
                through both in detail.
              </p>
              <p className="text-sm">
                Building against the API instead? Every workspace also speaks
                OpenAI-compatible, native REST, and MCP - jump to{" "}
                <TextLink href="#from-code">Use it from code</TextLink>.
              </p>
            </Section>

            <Section id="connect-tools" index="02" title="Connect your tools">
              <p>
                Third Brain ships a small CLI, <Mono>third-brain-mcp</Mono>, that wires
                the tools your team already uses into the brain - Claude Desktop, Claude
                Code, Cursor, or anything that speaks MCP over stdio. No config files to
                hand-edit.
              </p>
              <CodeBlock label="shell" code={CONNECT} />
              <p>
                <Mono>connect</Mono> runs a device flow: it prints a one-time code (for
                example <Mono>KTPB-3947</Mono>), opens <Mono>{"<app>"}/activate</Mono> in
                your browser, and waits. An org admin approves the code from the
                dashboard, a scoped API key is minted once, and the CLI saves it to{" "}
                <Mono>~/.third-brain/config.json</Mono>. In CI, pass{" "}
                <Mono>--api-key tb_live_...</Mono> instead of the browser step, or set the{" "}
                <Mono>THIRD_BRAIN_URL</Mono> and <Mono>THIRD_BRAIN_API_KEY</Mono>{" "}
                environment variables.
              </p>
              <p>
                <Mono>install</Mono> registers the bridge with a client, preserving any
                existing entries:
              </p>
              <DefinitionList
                termWidth="sm:w-40"
                rows={INSTALL_TARGETS.map((t) => ({ term: t.cmd, desc: t.desc }))}
              />
              <p>
                Under the hood each client is pointed at{" "}
                <Mono>npx third-brain-mcp serve</Mono>, the stdio-to-HTTP bridge that
                relays MCP traffic to your server - you rarely run it by hand. Run{" "}
                <Mono>npx third-brain-mcp status</Mono> any time to sanity-check the saved
                connection.
              </p>
            </Section>

            <Section id="bring-models" index="03" title="Bring your models">
              <p>
                Third Brain never marks up tokens - you bring your own provider keys. Add
                a <span className="text-foreground">Connector</span> on the dashboard to
                configure a provider for your organization. Credentials are encrypted at
                rest and never returned.
              </p>
              <DefinitionList
                termWidth="sm:w-36"
                rows={CONNECTORS.map((c) => ({ term: c.type, desc: c.note }))}
              />
              <p>
                When a call does not name a provider, one is chosen by key priority.
                Completions try <Mono>openai</Mono>, then <Mono>anthropic</Mono>, then{" "}
                <Mono>google</Mono>; embeddings try <Mono>openai</Mono>, then{" "}
                <Mono>google</Mono> - Anthropic is completions-only and has no embeddings
                API.
              </p>
            </Section>

            <Section id="agents-write" index="04" title="How agents write documentation">
              <p>
                Once a client is connected, your agents get five MCP tools. Two read the
                brain and two write to it - which is how the documentation writes itself.
              </p>
              <DefinitionList
                rows={MCP_TOOLS.map((t) => ({ term: t.name, desc: t.desc }))}
              />
              <p>
                As an agent works, it calls <Mono>add_knowledge</Mono> or{" "}
                <Mono>update_knowledge</Mono> to capture a decision, an answer, or a note
                into the right collection. Ask your assistant to{" "}
                <span className="text-foreground">
                  &quot;write up the decision we just made into Engineering /
                  Decisions&quot;
                </span>{" "}
                and it lands in the brain for the next teammate to find.
              </p>
              <p>
                Every write is governed. An agent can only write where its key is allowed
                - editor on the collection, plus an <Mono>ingest</Mono> or{" "}
                <Mono>write</Mono> scope - and every write passes through the same secret
                and PII scanner as a manual upload. A flagged <Mono>add_knowledge</Mono>{" "}
                is parked as <Mono>quarantined</Mono> for human review and tells the model
                why; a flagged <Mono>update_knowledge</Mono> is rejected outright, so a
                leaked credential never reaches the index.
              </p>
              <p>
                You can see exactly what your agents captured on the dashboard: the
                Overview has a <span className="text-foreground">Written by agents</span>{" "}
                card, and the Documents page has an{" "}
                <span className="text-foreground">Agent-written</span> filter. Quarantined
                writes wait there for a human to review and approve or discard.
              </p>
            </Section>

            <Section id="from-code" index="05" title="Use it from code">
              <p>
                The same permission-aware brain is reachable through three interfaces your
                tools already speak. All of them authenticate with a Third Brain API key
                (they start with <Mono>tb_</Mono>), minted by an admin on the dashboard or
                through the device flow above, and carrying scopes: <Mono>search</Mono>,{" "}
                <Mono>read</Mono>, <Mono>write</Mono>, <Mono>ingest</Mono>,{" "}
                <Mono>manage</Mono>.
              </p>
              <SubHeading>OpenAI-compatible</SubHeading>
              <p>
                Point any OpenAI SDK at <Mono>{"<api>"}/v1</Mono> and use the model id{" "}
                <Mono>third-brain</Mono>. Answers are grounded in your knowledge base and
                filtered to what your key can see, with citations.
              </p>
              <CodeBlock label="python" code={PY_SNIPPET} />
              <SubHeading>Native REST</SubHeading>
              <p>
                The native API under <Mono>/api/v1</Mono> gives you full CRUD plus{" "}
                <Mono>/search</Mono> and <Mono>/search/chat</Mono>.
              </p>
              <CodeBlock label="shell" code={REST_SNIPPET} />
              <SubHeading>MCP endpoint</SubHeading>
              <p>
                The MCP server is a single JSON-RPC 2.0 surface at <Mono>POST /mcp</Mono>{" "}
                using the streamable-HTTP format. A client that speaks MCP over HTTP can
                point straight at it, authenticating with{" "}
                <Mono>Authorization: Bearer</Mono> or <Mono>X-API-Key</Mono>:
              </p>
              <CodeBlock label="mcp client config" code={MCP_CONFIG} />
              <div className="divide-y divide-border/60 rounded-lg border border-border/60">
                {ENDPOINTS.map((e) => (
                  <p
                    key={`${e.method} ${e.path}`}
                    className="flex items-baseline gap-3 px-4 py-2.5 font-mono text-[13px]"
                  >
                    <span className="w-12 shrink-0 text-muted-foreground/70">
                      {e.method}
                    </span>
                    <span className="text-foreground">{e.path}</span>
                    {e.note && (
                      <span className="text-muted-foreground/70">· {e.note}</span>
                    )}
                  </p>
                ))}
              </div>
            </Section>

            <Section id="governance" index="06" title="Permissions and governance">
              <p>
                Access control is the trust pillar under everything the agents write.
                Third Brain models it as org, team, and user roles - owner, admin, editor,
                viewer - plus per-collection and per-document grants for users and teams.
              </p>
              <p>
                Permissions are enforced{" "}
                <span className="text-foreground">at retrieval time</span>, pushed into
                the SQL <Mono>WHERE</Mono> clause. A chunk the caller cannot see never
                enters a search result, an LLM prompt, or a citation - not even
                indirectly. The same gate governs what agents write back.
              </p>
              <p>
                Content that looks like it holds secrets or PII is quarantined for human
                review on the Documents page instead of being indexed. Every query is
                scoped to a single organization, API keys are least-privilege and
                rate-limited, and every search, ingest, agent write-back, and admin action
                is recorded in an immutable audit log with actor, IP, and timestamp.
              </p>
            </Section>
          </div>
        </div>
      </div>

      <Cta />
    </>
  );
}
