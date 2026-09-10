"use client";

import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Globe,
  Loader2,
  MessageSquarePlus,
  Search,
  Send,
  Sparkles,
  ThumbsDown,
  ThumbsUp,
} from "lucide-react";
import { toast } from "sonner";

import { EmptyState } from "@/components/dashboard/empty-state";
import { PageHeader } from "@/components/dashboard/page-header";
import { CitationList } from "@/components/knowledge/citation-list";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { api, ApiError, streamChat } from "@/lib/api";
import type {
  AnswerMatch,
  Citation,
  Collection,
  Conversation,
  FeedbackRating,
  SearchResult,
  WebSource,
} from "@/lib/types";

type Mode = "search" | "ask";
const ALL = "__all__";

const EXAMPLES = [
  "Summarize our onboarding process",
  "What's our policy on remote work?",
  "How do I rotate an API key?",
];

interface AskTurn {
  id: string;
  question: string;
  answer: string;
  citations: Citation[];
  webSources: WebSource[];
  insightId: string | null;
  streaming: boolean;
  rating: FeedbackRating | null;
}

/** Renders answer text, turning inline `[n]` markers into subtle source chips. */
function AnswerBody({ text, streaming }: { text: string; streaming: boolean }) {
  const parts = text.split(/(\[\d+\])/g);
  return (
    <div className="whitespace-pre-wrap text-sm leading-relaxed text-foreground">
      {parts.map((part, i) => {
        const m = part.match(/^\[(\d+)\]$/);
        if (m) {
          return (
            <span
              key={i}
              className="mx-0.5 inline-flex h-4 min-w-4 items-center justify-center rounded bg-primary/10 px-1 align-baseline text-[11px] font-semibold text-primary"
            >
              {m[1]}
            </span>
          );
        }
        return <React.Fragment key={i}>{part}</React.Fragment>;
      })}
      {streaming ? (
        <span className="ml-0.5 inline-block h-4 w-1.5 animate-pulse bg-primary align-text-bottom" />
      ) : null}
    </div>
  );
}

/**
 * Search page - two modes over one query box. Search returns ranked chunks; Ask keeps a
 * multi-turn transcript backed by a server-side conversation, shown above those results.
 */
export default function SearchPage() {
  const [mode, setMode] = React.useState<Mode>("ask");
  const [query, setQuery] = React.useState("");
  const [collectionId, setCollectionId] = React.useState<string>(ALL);
  const [web, setWeb] = React.useState(false);

  const [searchResult, setSearchResult] = React.useState<SearchResult | null>(null);
  const [searching, setSearching] = React.useState(false);

  const [turns, setTurns] = React.useState<AskTurn[]>([]);
  const [conversationId, setConversationId] = React.useState<string | null>(null);
  const [asking, setAsking] = React.useState(false);

  const askAbortRef = React.useRef<AbortController | null>(null);
  React.useEffect(() => () => askAbortRef.current?.abort(), []);

  const { data: collections } = useQuery<Collection[]>({
    queryKey: ["collections"],
    queryFn: () => api.get<Collection[]>("/collections"),
  });

  const collectionIds = collectionId === ALL ? undefined : [collectionId];

  function patchTurn(id: string, patch: Partial<AskTurn>) {
    setTurns((prev) => prev.map((t) => (t.id === id ? { ...t, ...patch } : t)));
  }

  function newConversation() {
    askAbortRef.current?.abort();
    setTurns([]);
    setConversationId(null);
  }

  async function runSearch() {
    setSearching(true);
    setSearchResult(null);
    try {
      const result = await api.post<SearchResult>("/search", {
        query: query.trim(),
        collection_ids: collectionIds,
        hybrid: true,
      });
      setSearchResult(result);
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : "Search failed");
    } finally {
      setSearching(false);
    }
  }

  /**
   * Run one Ask turn, streaming the answer into the transcript.
   *
   * The server-side conversation is opened lazily, so follow-ups are grounded in history.
   *
   * The `finally` block resets only when no newer run has taken over, which is what the
   * ref identity tells us. Keying on `signal.aborted` instead meant an abort with no
   * successor - "New conversation" (or unmount) rather than a replacing question -
   * skipped setAsking(false) forever, leaving `busy` stuck true and the Ask box
   * permanently disabled until a page reload.
   */
  async function runAsk() {
    askAbortRef.current?.abort();
    const controller = new AbortController();
    askAbortRef.current = controller;
    setAsking(true);

    const q = query.trim();
    setQuery("");
    const turnId =
      typeof crypto !== "undefined" && crypto.randomUUID
        ? crypto.randomUUID()
        : String(Date.now());
    setTurns((prev) => [
      ...prev,
      {
        id: turnId,
        question: q,
        answer: "",
        citations: [],
        webSources: [],
        insightId: null,
        streaming: true,
        rating: null,
      },
    ]);

    try {
      let convId = conversationId;
      if (!convId) {
        const conv = await api.post<Conversation>("/conversations", { web_enabled: web });
        convId = conv.id;
        setConversationId(convId);
      }
      await streamChat(
        "/search/chat",
        {
          query: q,
          collection_ids: collectionIds,
          stream: true,
          conversation_id: convId,
          web,
        },
        (token) =>
          setTurns((prev) =>
            prev.map((t) => (t.id === turnId ? { ...t, answer: t.answer + token } : t)),
          ),
        (citations, meta) =>
          patchTurn(turnId, {
            citations,
            webSources: meta?.web_sources ?? [],
            insightId: meta?.insight_id ?? null,
          }),
        controller.signal,
      );
    } catch (err) {
      if (err instanceof DOMException && err.name === "AbortError") return;
      toast.error(err instanceof ApiError ? err.message : "Ask failed");
    } finally {
      if (askAbortRef.current === controller) {
        setAsking(false);
        patchTurn(turnId, { streaming: false });
        askAbortRef.current = null;
      }
    }
  }

  async function rate(turn: AskTurn, rating: FeedbackRating) {
    if (!turn.insightId || turn.rating) return;
    patchTurn(turn.id, { rating });
    try {
      await api.post("/feedback", { insight_id: turn.insightId, rating });
      toast.success("Thanks for the feedback");
    } catch {
      patchTurn(turn.id, { rating: null });
      toast.error("Couldn't record feedback");
    }
  }

  function submit() {
    if (!query.trim() || searching || asking) return;
    if (mode === "search") {
      setSearchResult(null);
      void runSearch();
    } else {
      void runAsk();
    }
  }

  const busy = searching || asking;
  const hasSearchResults = mode === "search" && searchResult !== null;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Ask"
        description="Query your knowledge with ranked sources, or hold a grounded, multi-turn conversation with citations."
        actions={
          mode === "ask" && turns.length > 0 ? (
            <Button variant="outline" onClick={newConversation}>
              <MessageSquarePlus className="h-4 w-4" />
              New conversation
            </Button>
          ) : null
        }
      />

      <Card className="space-y-4 p-5">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <Tabs value={mode} onValueChange={(v) => setMode(v as Mode)}>
            <TabsList>
              <TabsTrigger value="ask" className="gap-1.5">
                <Sparkles className="h-3.5 w-3.5" /> Ask
              </TabsTrigger>
              <TabsTrigger value="search" className="gap-1.5">
                <Search className="h-3.5 w-3.5" /> Search
              </TabsTrigger>
            </TabsList>
          </Tabs>

          <div className="flex items-center gap-3">
            {mode === "ask" ? (
              <div className="flex items-center gap-2">
                <Globe className="h-4 w-4 text-muted-foreground" />
                <Label htmlFor="web-toggle" className="text-sm text-muted-foreground">
                  Web
                </Label>
                <Switch id="web-toggle" checked={web} onCheckedChange={setWeb} />
              </div>
            ) : null}
            <Select value={collectionId} onValueChange={setCollectionId}>
              <SelectTrigger className="sm:w-56">
                <SelectValue placeholder="All knowledge bases" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ALL}>All knowledge bases</SelectItem>
                {(collections ?? []).map((c) => (
                  <SelectItem key={c.id} value={c.id}>
                    {c.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>

        <div className="relative">
          <Textarea
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                submit();
              }
            }}
            placeholder={
              mode === "ask"
                ? turns.length > 0
                  ? "Ask a follow-up…"
                  : "Ask a question about your knowledge…"
                : "Search across your documents…"
            }
            className="min-h-[92px] resize-none pr-28 text-base"
          />
          <Button
            onClick={submit}
            disabled={!query.trim() || busy}
            className="absolute bottom-3 right-3"
          >
            {busy ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : mode === "ask" ? (
              <>
                <Sparkles className="h-4 w-4" />
                Ask
              </>
            ) : (
              <>
                <Send className="h-4 w-4" />
                Search
              </>
            )}
          </Button>
        </div>

        {mode === "ask" && turns.length === 0 && !asking ? (
          <div className="flex flex-wrap gap-2">
            {EXAMPLES.map((ex) => (
              <button
                key={ex}
                type="button"
                onClick={() => setQuery(ex)}
                className="rounded-full border bg-card px-3 py-1 text-xs text-muted-foreground transition-colors hover:border-primary/40 hover:text-foreground"
              >
                {ex}
              </button>
            ))}
          </div>
        ) : null}
      </Card>

      {mode === "ask" && turns.length > 0 ? (
        <div className="space-y-6">
          {turns.map((turn) => (
            <div key={turn.id} className="grid gap-4 lg:grid-cols-[1fr_20rem]">
              <div className="space-y-3">
                <div className="text-sm font-semibold text-foreground">
                  {turn.question}
                </div>
                <Card className="space-y-3 p-5" data-testid="answer-panel">
                  {turn.answer.length === 0 && turn.streaming ? (
                    <div className="space-y-2">
                      <Skeleton className="h-4 w-full" />
                      <Skeleton className="h-4 w-11/12" />
                      <Skeleton className="h-4 w-3/4" />
                    </div>
                  ) : (
                    <AnswerBody text={turn.answer} streaming={turn.streaming} />
                  )}
                  {!turn.streaming && turn.answer.length === 0 ? (
                    <p className="text-sm text-muted-foreground">
                      No answer was produced. Try rephrasing or widening the collection
                      filter.
                    </p>
                  ) : null}
                  {!turn.streaming && turn.insightId ? (
                    <div className="flex items-center gap-2 pt-1">
                      <span className="text-xs text-muted-foreground">
                        Was this helpful?
                      </span>
                      <Button
                        variant="ghost"
                        size="icon"
                        aria-label="Helpful"
                        disabled={turn.rating !== null}
                        onClick={() => rate(turn, "up")}
                        className={turn.rating === "up" ? "text-success" : ""}
                      >
                        <ThumbsUp className="h-4 w-4" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon"
                        aria-label="Not helpful"
                        disabled={turn.rating !== null}
                        onClick={() => rate(turn, "down")}
                        className={turn.rating === "down" ? "text-destructive" : ""}
                      >
                        <ThumbsDown className="h-4 w-4" />
                      </Button>
                    </div>
                  ) : null}
                </Card>
              </div>

              <div className="space-y-3">
                {turn.citations.length > 0 ? (
                  <>
                    <p className="text-sm font-semibold text-foreground">
                      Sources{" "}
                      <span className="text-muted-foreground">
                        ({turn.citations.length})
                      </span>
                    </p>
                    <CitationList citations={turn.citations} />
                  </>
                ) : turn.streaming ? (
                  <div className="space-y-2">
                    {Array.from({ length: 2 }).map((_, i) => (
                      <Skeleton key={i} className="h-20 w-full rounded-lg" />
                    ))}
                  </div>
                ) : null}
                {turn.webSources.length > 0 ? (
                  <div className="space-y-2">
                    <p className="flex items-center gap-1.5 text-sm font-semibold text-foreground">
                      <Globe className="h-3.5 w-3.5 text-muted-foreground" /> Web
                    </p>
                    {turn.webSources.map((w, i) => (
                      <a
                        key={i}
                        href={w.url}
                        target="_blank"
                        rel="noreferrer"
                        className="block rounded-lg border p-3 text-xs transition-colors hover:border-primary/40"
                      >
                        <span className="line-clamp-1 font-medium text-foreground">
                          {w.title}
                        </span>
                        <span className="line-clamp-2 text-muted-foreground">
                          {w.snippet}
                        </span>
                      </a>
                    ))}
                  </div>
                ) : null}
              </div>
            </div>
          ))}
        </div>
      ) : null}

      {hasSearchResults ? (
        <div className="space-y-4">
          {searchResult!.answers.length > 0 ? (
            <div className="space-y-2">
              {searchResult!.answers.map((a: AnswerMatch) => (
                <Card key={a.id} className="space-y-1 border-success/40 p-4">
                  <div className="flex items-center gap-2">
                    <Badge variant="success">Verified answer</Badge>
                    <span className="text-sm font-medium text-foreground">
                      {a.question}
                    </span>
                  </div>
                  <p className="text-sm text-muted-foreground">{a.answer}</p>
                </Card>
              ))}
            </div>
          ) : null}
          {searchResult!.hits.length > 0 ? (
            <div className="space-y-3">
              <p className="text-sm text-muted-foreground">
                {searchResult!.hits.length} result
                {searchResult!.hits.length === 1 ? "" : "s"} for{" "}
                <span className="font-medium text-foreground">
                  “{searchResult!.query}”
                </span>
              </p>
              <CitationList citations={searchResult!.hits} />
            </div>
          ) : searchResult!.answers.length === 0 ? (
            <EmptyState
              icon={Search}
              title="No matches"
              description="Nothing matched your query in the collections you can access. Try different wording."
            />
          ) : null}
        </div>
      ) : null}

      {mode === "search" && searching ? (
        <div className="space-y-2">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-24 w-full rounded-lg" />
          ))}
        </div>
      ) : null}
    </div>
  );
}
