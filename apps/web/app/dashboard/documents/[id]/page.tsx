"use client";

import * as React from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, FileText, Save } from "lucide-react";
import { toast } from "sonner";

import {
  DocumentStatusBadge,
  isProcessingStatus,
} from "@/components/knowledge/document-status-badge";
import { Markdown } from "@/components/knowledge/markdown";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { api, ApiError } from "@/lib/api";
import type { DocumentChunk, DocumentContent, DocumentItem } from "@/lib/types";
import { cn } from "@/lib/utils";

function errMsg(e: unknown, fallback: string) {
  return e instanceof ApiError ? e.message : fallback;
}

/**
 * Document detail - the editor, the rendered preview and the indexed chunks for one
 * document.
 *
 * Draft state is seeded from the loaded content and replaced wholesale on refetch unless
 * the user has unsaved edits (their draft always wins). An unsaved draft is mirrored into
 * sessionStorage and restored on mount, because client-side navigation unmounts this page
 * without warning. Preview parsing is O(document), so keystrokes land in the textarea at
 * urgent priority and the re-parse runs in an interruptible low-priority render behind
 * them.
 *
 * On save the cache is seeded with what was actually saved BEFORE the draft is cleared, so
 * the editor never flashes back to the pre-save text while the refetch is in flight -
 * and keystrokes typed during the request are still unsaved work, so they are kept.
 *
 * The backend computes `editable` from this caller's effective permission on THIS document
 * (org role alone is not authoritative - grants and collection visibility are).
 */
export default function DocumentDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = React.use(params);
  const queryClient = useQueryClient();

  const docQuery = useQuery<DocumentItem>({
    queryKey: ["document", id],
    queryFn: () => api.get<DocumentItem>(`/documents/${id}`),
    refetchInterval: (query) =>
      query.state.data && isProcessingStatus(query.state.data.status) ? 4000 : false,
  });
  const doc = docQuery.data;
  const isImage = Boolean(doc?.mime_type?.startsWith("image/"));

  const contentQuery = useQuery<DocumentContent>({
    queryKey: ["document-content", id],
    queryFn: () => api.get<DocumentContent>(`/documents/${id}/content`),
    enabled: Boolean(doc) && !isImage && doc?.status !== "quarantined",
    retry: false,
  });

  const chunksQuery = useQuery<DocumentChunk[]>({
    queryKey: ["document-chunks", id],
    queryFn: () => api.get<DocumentChunk[]>(`/documents/${id}/chunks`),
  });
  const chunks = chunksQuery.data ?? [];
  const totalTokens = chunks.reduce((sum, c) => sum + c.token_count, 0);

  const [draft, setDraft] = React.useState<string | null>(null);
  const serverContent = contentQuery.data?.content ?? null;
  const text = draft ?? serverContent ?? "";
  const dirty = draft !== null && draft !== serverContent;
  const previewText = React.useDeferredValue(text);

  React.useEffect(() => {
    const stored = sessionStorage.getItem(`doc-draft:${id}`);
    if (stored !== null) setDraft(stored);
  }, [id]);

  React.useEffect(() => {
    if (dirty) sessionStorage.setItem(`doc-draft:${id}`, draft ?? "");
    else sessionStorage.removeItem(`doc-draft:${id}`);
  }, [dirty, draft, id]);

  React.useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault();
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  const status = doc?.status;
  React.useEffect(() => {
    if (status && !isProcessingStatus(status)) {
      void queryClient.invalidateQueries({ queryKey: ["document-content", id] });
      void queryClient.invalidateQueries({ queryKey: ["document-chunks", id] });
    }
  }, [status, id, queryClient]);

  const save = useMutation({
    mutationFn: (content: string) =>
      api.put<DocumentItem>(`/documents/${id}/content`, { content }),
    onSuccess: (_doc, savedContent) => {
      toast.success("Saved and re-indexed");
      queryClient.setQueryData<DocumentContent>(["document-content", id], (old) =>
        old ? { ...old, content: savedContent } : old,
      );
      setDraft((current) => (current === savedContent ? null : current));
      sessionStorage.removeItem(`doc-draft:${id}`);
      void queryClient.invalidateQueries({ queryKey: ["document", id] });
      void queryClient.invalidateQueries({ queryKey: ["document-content", id] });
      void queryClient.invalidateQueries({ queryKey: ["document-chunks", id] });
      void queryClient.invalidateQueries({ queryKey: ["documents"] });
    },
    onError: (e) => toast.error(errMsg(e, "Couldn't save the document")),
  });

  const editable = (contentQuery.data?.editable ?? false) && !isImage;
  const convertsToText = Boolean(
    contentQuery.data &&
    contentQuery.data.mime_type &&
    !contentQuery.data.mime_type.startsWith("text/"),
  );

  if (docQuery.isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-[420px] w-full" />
      </div>
    );
  }
  if (docQuery.isError || !doc) {
    return (
      <Card className="p-8 text-center text-sm text-muted-foreground">
        That document could not be found.{" "}
        <Link href="/dashboard/documents" className="text-primary underline">
          Back to documents
        </Link>
      </Card>
    );
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 space-y-1">
          <Link
            href="/dashboard/documents"
            className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
          >
            <ArrowLeft className="h-3.5 w-3.5" />
            Documents
          </Link>
          <h1 className="flex min-w-0 items-center gap-2 text-xl font-semibold">
            <FileText className="h-5 w-5 shrink-0 text-muted-foreground" />
            <span className="truncate" title={doc.title}>
              {doc.title}
            </span>
          </h1>
          <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            <DocumentStatusBadge status={doc.status} />
            <span>
              {chunks.length} chunk{chunks.length === 1 ? "" : "s"}
            </span>
            <span>·</span>
            <span>{totalTokens.toLocaleString()} tokens indexed</span>
            {doc.mime_type ? (
              <>
                <span>·</span>
                <span className="font-mono">{doc.mime_type}</span>
              </>
            ) : null}
          </div>
        </div>
        {editable ? (
          <div className="flex items-center gap-3">
            {dirty ? (
              <span className="text-xs text-muted-foreground">Unsaved changes</span>
            ) : null}
            <Button
              onClick={() => save.mutate(text)}
              disabled={!dirty || save.isPending || !text.trim()}
              className={cn(save.isPending && "opacity-80")}
            >
              <Save className="h-4 w-4" />
              {save.isPending ? "Saving…" : "Save & re-index"}
            </Button>
          </div>
        ) : null}
      </div>

      {doc.status === "quarantined" ? (
        <Card className="border-destructive/40 p-4 text-sm">
          This document is quarantined; its content is withheld until it is reviewed on
          the Documents page.
        </Card>
      ) : isImage ? (
        <ImageDocumentView doc={doc} chunks={chunks} />
      ) : (
        <Tabs defaultValue="edit">
          <TabsList>
            <TabsTrigger value="edit">{editable ? "Edit" : "Content"}</TabsTrigger>
            <TabsTrigger value="chunks">
              Chunks{chunks.length ? ` (${chunks.length})` : ""}
            </TabsTrigger>
          </TabsList>

          <TabsContent value="edit" className="mt-4">
            {contentQuery.isLoading ? (
              <Skeleton className="h-[420px] w-full" />
            ) : contentQuery.isError ? (
              <Card className="p-6 text-sm text-muted-foreground">
                {errMsg(
                  contentQuery.error,
                  "This document's source is not editable text.",
                )}{" "}
                The indexed chunks are still available in the Chunks tab.
              </Card>
            ) : (
              <div className="space-y-3">
                {editable && convertsToText ? (
                  <p className="text-xs text-muted-foreground">
                    This document was ingested from {contentQuery.data?.mime_type}; you
                    are editing its extracted text, and saving stores it as plain text.
                  </p>
                ) : null}
                <div className="grid gap-4 lg:grid-cols-2">
                  {editable ? (
                    <Textarea
                      value={text}
                      onChange={(e) => setDraft(e.target.value)}
                      spellCheck={false}
                      className="min-h-[520px] resize-y font-mono text-sm leading-relaxed"
                      aria-label="Document markdown source"
                    />
                  ) : null}
                  <Card
                    className={cn(
                      "max-h-[75vh] overflow-y-auto p-5",
                      !editable && "lg:col-span-2",
                    )}
                  >
                    <p className="mb-3 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                      Preview
                    </p>
                    <Markdown text={previewText} />
                  </Card>
                </div>
              </div>
            )}
          </TabsContent>

          <TabsContent value="chunks" className="mt-4">
            <ChunkInspector chunks={chunks} loading={chunksQuery.isLoading} />
          </TabsContent>
        </Tabs>
      )}
    </div>
  );
}

function ImageDocumentView({
  doc,
  chunks,
}: {
  doc: DocumentItem;
  chunks: DocumentChunk[];
}) {
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card className="flex items-center justify-center p-4">
        {doc.image_url ? (
          // eslint-disable-next-line @next/next/no-img-element -- capability URL on the API origin
          <img
            src={doc.image_url}
            alt={doc.title}
            className="max-h-[70vh] w-auto max-w-full rounded-md"
          />
        ) : (
          <p className="py-16 text-sm text-muted-foreground">
            The image preview becomes available once ingestion completes.
          </p>
        )}
      </Card>
      <div className="space-y-3">
        <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
          Indexed summary (what search and LLMs see)
        </p>
        <ChunkInspector chunks={chunks} loading={false} />
      </div>
    </div>
  );
}

function ChunkInspector({
  chunks,
  loading,
}: {
  chunks: DocumentChunk[];
  loading: boolean;
}) {
  if (loading) return <Skeleton className="h-64 w-full" />;
  if (!chunks.length) {
    return (
      <Card className="p-6 text-sm text-muted-foreground">
        No chunks yet - the document may still be processing.
      </Card>
    );
  }
  return (
    <div className="space-y-0">
      <p className="mb-2 text-xs text-muted-foreground">
        This is exactly how the document is stored for retrieval: each segment below is
        one chunk, embedded and searched independently. Boundaries land on headings,
        paragraphs and sentence ends.
      </p>
      <Card className="overflow-hidden">
        {chunks.map((chunk, i) => (
          <React.Fragment key={chunk.id}>
            <div
              className="flex items-center gap-3 border-b border-t bg-muted/60 px-4 py-1.5 first:border-t-0"
              role="separator"
              aria-label={`Chunk ${chunk.chunk_index + 1} boundary`}
            >
              <Badge variant="muted" className="text-[10px]">
                Chunk {chunk.chunk_index + 1}
              </Badge>
              <span className="text-[11px] text-muted-foreground">
                {chunk.token_count.toLocaleString()} tokens
              </span>
            </div>
            <div
              className={cn(
                "whitespace-pre-wrap px-4 py-3 text-sm leading-relaxed",
                i % 2 === 1 && "bg-muted/20",
              )}
            >
              {chunk.content}
            </div>
          </React.Fragment>
        ))}
      </Card>
    </div>
  );
}
