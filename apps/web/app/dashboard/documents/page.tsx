"use client";

import * as React from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  BadgeCheck,
  Bot,
  ChevronLeft,
  ChevronRight,
  FileText,
  Globe,
  Loader2,
  MoreHorizontal,
  Plug,
  RefreshCw,
  Search,
  ShieldAlert,
  ShieldCheck,
  Trash2,
  Type,
  Upload,
} from "lucide-react";
import { toast } from "sonner";

import { DataTable, type Column } from "@/components/dashboard/data-table";
import { Badge } from "@/components/ui/badge";
import { EmptyState } from "@/components/dashboard/empty-state";
import { PageHeader } from "@/components/dashboard/page-header";
import {
  DocumentStatusBadge,
  isProcessingStatus,
} from "@/components/knowledge/document-status-badge";
import { QuarantineReviewDialog } from "@/components/knowledge/quarantine-review-dialog";
import { useQuarantineAlert } from "@/components/knowledge/use-quarantine-alert";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { orgRoleAtLeast } from "@/components/governance/role-badge";
import { useAuth } from "@/lib/auth-context";
import { api, ApiError } from "@/lib/api";
import { formatBytes, formatDate, formatNumber } from "@/lib/utils";
import type {
  Collection,
  DocumentItem,
  DocumentStatus,
  Page,
  SourceType,
} from "@/lib/types";

const PAGE_SIZE = 20;
const ALL = "__all__";

const SOURCE_META: Record<SourceType, { label: string; icon: typeof FileText }> = {
  file: { label: "File", icon: FileText },
  text: { label: "Text", icon: Type },
  url: { label: "URL", icon: Globe },
  connector: { label: "Connector", icon: Plug },
};

const STATUS_OPTIONS: DocumentStatus[] = [
  "pending",
  "processing",
  "indexed",
  "quarantined",
  "failed",
  "archived",
];

/** Debounce a rapidly-changing value (e.g. a search box). */
function useDebounced<T>(value: T, delay = 350): T {
  const [debounced, setDebounced] = React.useState(value);
  React.useEffect(() => {
    const id = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(id);
  }, [value, delay]);
  return debounced;
}

interface DocumentChunkItem {
  id: string;
  document_id: string;
  chunk_index: number;
  content: string;
  token_count: number;
  metadata?: Record<string, unknown>;
}

/** Slide-over panel showing a document's indexed chunks. */
function ChunkSheet({
  doc,
  open,
  onOpenChange,
}: {
  doc: DocumentItem | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { data, isLoading, isError } = useQuery<DocumentChunkItem[]>({
    queryKey: ["document-chunks", doc?.id],
    queryFn: () => api.get<DocumentChunkItem[]>(`/documents/${doc!.id}/chunks`),
    enabled: open && !!doc,
  });

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="flex w-full flex-col gap-0 p-0 sm:max-w-xl">
        <SheetHeader className="space-y-1 border-b p-6">
          <SheetTitle className="truncate pr-8">{doc?.title ?? "Document"}</SheetTitle>
          <SheetDescription>
            {doc
              ? `${formatNumber(doc.chunk_count)} ${
                  doc.chunk_count === 1 ? "chunk" : "chunks"
                } · ${formatBytes(doc.size_bytes)}`
              : null}
          </SheetDescription>
          {doc ? (
            <Link
              href={`/dashboard/documents/${doc.id}`}
              className="text-xs text-primary hover:underline"
            >
              Open editor & chunk inspector
            </Link>
          ) : null}
        </SheetHeader>

        <ScrollArea className="flex-1">
          <div className="space-y-3 p-6">
            {isLoading ? (
              Array.from({ length: 4 }).map((_, i) => (
                <div key={i} className="space-y-2 rounded-lg border p-4">
                  <Skeleton className="h-3 w-20" />
                  <Skeleton className="h-3 w-full" />
                  <Skeleton className="h-3 w-4/5" />
                </div>
              ))
            ) : isError ? (
              <EmptyState
                compact
                icon={FileText}
                title="Couldn't load chunks"
                description="Try reopening this document."
              />
            ) : !data || data.length === 0 ? (
              <EmptyState
                compact
                icon={FileText}
                title="No chunks yet"
                description={
                  doc && isProcessingStatus(doc.status)
                    ? "This document is still being processed."
                    : "This document hasn't produced any indexed chunks."
                }
              />
            ) : (
              data.map((chunk) => (
                <div key={chunk.id} className="space-y-2 rounded-lg border bg-card p-4">
                  <div className="flex items-center justify-between text-xs text-muted-foreground">
                    <span className="font-medium text-foreground">
                      Chunk {chunk.chunk_index + 1}
                    </span>
                    <span className="tabular-nums">
                      {formatNumber(chunk.token_count)} tokens
                    </span>
                  </div>
                  <p className="whitespace-pre-wrap break-words text-sm leading-relaxed text-muted-foreground">
                    {chunk.content}
                  </p>
                </div>
              ))
            )}
          </div>
        </ScrollArea>
      </SheetContent>
    </Sheet>
  );
}

/**
 * The documents table with its filters, chunk viewer and row actions.
 *
 * Reprocess and Delete require EDITOR+ on the backend, and a plain viewer would only get
 * a 403, so those actions are not offered to them.
 *
 * The collection and author filters are URL-seeded. The App Router does not remount the
 * page when only search params change (e.g. clicking the sidebar "Documents" link while
 * filtered), so an effect re-syncs each filter whenever its param actually changes. The
 * author filter means ALL = everyone and "mcp" = written by connected agents, deep-linkable
 * via ?via=mcp exactly as ?collection= seeds the collection filter. Any filter change
 * resets back to the first page, and the chunk viewer is itself deep-linkable from
 * citations (`?doc=<id>`).
 *
 * The list polls while any visible document is still being ingested.
 */
function DocumentsInner() {
  const searchParams = useSearchParams();
  const queryClient = useQueryClient();
  const { role } = useAuth();
  const canManage = orgRoleAtLeast(role, "editor");

  const collectionParam = searchParams.get("collection") ?? ALL;
  const [collectionId, setCollectionId] = React.useState<string>(collectionParam);
  React.useEffect(() => {
    setCollectionId(collectionParam);
  }, [collectionParam]);
  const [status, setStatus] = React.useState<string>(ALL);
  const viaParam = searchParams.get("via") === "mcp" ? "mcp" : ALL;
  const [author, setAuthor] = React.useState<string>(viaParam);
  React.useEffect(() => {
    setAuthor(viaParam);
  }, [viaParam]);
  const [rawSearch, setRawSearch] = React.useState("");
  const search = useDebounced(rawSearch);
  const [page, setPage] = React.useState(1);

  React.useEffect(() => {
    setPage(1);
  }, [collectionId, status, author, search]);

  const { data: collections } = useQuery<Collection[]>({
    queryKey: ["collections"],
    queryFn: () => api.get<Collection[]>("/collections"),
  });

  const params = {
    collection_id: collectionId === ALL ? undefined : collectionId,
    status: status === ALL ? undefined : status,
    via: author === ALL ? undefined : author,
    q: search.trim() || undefined,
    page,
    page_size: PAGE_SIZE,
  };

  const { data, isLoading, isError, isFetching } = useQuery<Page<DocumentItem>>({
    queryKey: ["documents", params],
    queryFn: () => api.get<Page<DocumentItem>>("/documents", params),
    placeholderData: (prev) => prev,
    refetchInterval: (query) => {
      const items = query.state.data?.items ?? [];
      return items.some((d) => isProcessingStatus(d.status)) ? 4000 : false;
    },
  });

  const items = data?.items ?? [];
  const total = data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  const [activeDoc, setActiveDoc] = React.useState<DocumentItem | null>(null);
  const [sheetOpen, setSheetOpen] = React.useState(false);
  const [deleteTarget, setDeleteTarget] = React.useState<DocumentItem | null>(null);
  const [reviewDocId, setReviewDocId] = React.useState<string | null>(null);
  const [reviewOpen, setReviewOpen] = React.useState(false);

  function openReview(doc: DocumentItem) {
    setReviewDocId(doc.id);
    setReviewOpen(true);
  }

  useQuarantineAlert(items, canManage ? openReview : undefined);

  const deepLinkDoc = searchParams.get("doc");
  const handledDeepLink = React.useRef<string | null>(null);
  React.useEffect(() => {
    if (!deepLinkDoc || handledDeepLink.current === deepLinkDoc) return;
    handledDeepLink.current = deepLinkDoc;
    api
      .get<DocumentItem>(`/documents/${deepLinkDoc}`)
      .then((doc) => {
        setActiveDoc(doc);
        setSheetOpen(true);
      })
      .catch(() => {
        toast.error("That document could not be found.");
      });
  }, [deepLinkDoc]);

  function openChunks(doc: DocumentItem) {
    setActiveDoc(doc);
    setSheetOpen(true);
  }

  const reprocess = useMutation({
    mutationFn: (id: string) => api.post<DocumentItem>(`/documents/${id}/reprocess`),
    onSuccess: () => {
      toast.success("Reprocessing started");
      queryClient.invalidateQueries({ queryKey: ["documents"] });
    },
    onError: (err) =>
      toast.error(err instanceof ApiError ? err.message : "Failed to reprocess"),
  });

  const remove = useMutation({
    mutationFn: (id: string) => api.delete(`/documents/${id}`),
    onSuccess: () => {
      toast.success("Document deleted");
      setDeleteTarget(null);
      queryClient.invalidateQueries({ queryKey: ["documents"] });
      queryClient.invalidateQueries({ queryKey: ["collections"] });
    },
    onError: (err) =>
      toast.error(err instanceof ApiError ? err.message : "Failed to delete"),
  });

  const setVerification = useMutation({
    mutationFn: ({ id, verify }: { id: string; verify: boolean }) =>
      api.post<DocumentItem>(`/documents/${id}/${verify ? "verify" : "unverify"}`, {}),
    onSuccess: (_d, v) => {
      toast.success(v.verify ? "Document verified" : "Verification cleared");
      queryClient.invalidateQueries({ queryKey: ["documents"] });
    },
    onError: (err) =>
      toast.error(err instanceof ApiError ? err.message : "Couldn't update verification"),
  });

  const columns: Column<DocumentItem>[] = [
    {
      id: "title",
      header: "Title",
      cell: (d) => (
        <Link
          href={`/dashboard/documents/${d.id}`}
          className="flex max-w-[280px] flex-col items-start text-left"
        >
          <span className="max-w-full truncate font-medium text-foreground hover:text-primary">
            {d.title}
          </span>
          {d.source_uri ? (
            <span className="max-w-[280px] truncate text-xs text-muted-foreground">
              {d.source_uri}
            </span>
          ) : null}
        </Link>
      ),
    },
    {
      id: "status",
      header: "Status",
      cell: (d) => <DocumentStatusBadge status={d.status} error={d.error} />,
    },
    {
      id: "signals",
      header: "Signals",
      hideOnMobile: true,
      cell: (d) => {
        const badges: React.ReactNode[] = [];
        if (d.verification_status === "verified") {
          badges.push(
            <Badge key="v" variant="success" className="gap-1">
              <BadgeCheck className="h-3 w-3" />
              Verified
            </Badge>,
          );
        } else if (d.verification_status === "stale") {
          badges.push(
            <Badge key="v" variant="muted">
              Stale
            </Badge>,
          );
        }
        if (d.sensitivity === "pii") {
          badges.push(
            <Badge key="s" variant="info">
              PII
            </Badge>,
          );
        } else if (d.sensitivity === "confidential") {
          badges.push(
            <Badge key="s" variant="destructive">
              Confidential
            </Badge>,
          );
        }
        if (d.doc_type) {
          badges.push(
            <Badge key="doctype" variant="warning">
              {d.doc_type.charAt(0).toUpperCase() + d.doc_type.slice(1)}
            </Badge>,
          );
        }
        if (d.via === "mcp") {
          badges.push(
            <Badge key="via" variant="info" className="gap-1">
              <Bot className="h-3 w-3" />
              Agent-written
            </Badge>,
          );
        }
        return badges.length ? (
          <div className="flex flex-wrap gap-1">{badges}</div>
        ) : (
          <span className="text-muted-foreground">—</span>
        );
      },
    },
    {
      id: "source",
      header: "Source",
      hideOnMobile: true,
      cell: (d) => {
        const meta = SOURCE_META[d.source_type] ?? SOURCE_META.file;
        const Icon = meta.icon;
        return (
          <span className="flex items-center gap-1.5 text-sm text-muted-foreground">
            <Icon className="h-3.5 w-3.5" />
            {meta.label}
          </span>
        );
      },
    },
    {
      id: "chunks",
      header: "Chunks",
      align: "right",
      hideOnMobile: true,
      cell: (d) => (
        <span className="tabular-nums text-muted-foreground">
          {formatNumber(d.chunk_count)}
        </span>
      ),
    },
    {
      id: "size",
      header: "Size",
      align: "right",
      hideOnMobile: true,
      cell: (d) => (
        <span className="tabular-nums text-muted-foreground">
          {formatBytes(d.size_bytes)}
        </span>
      ),
    },
    {
      id: "created",
      header: "Added",
      hideOnMobile: true,
      cell: (d) => (
        <span className="text-muted-foreground">{formatDate(d.created_at)}</span>
      ),
    },
    {
      id: "actions",
      header: "",
      align: "right",
      cell: (d) => (
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" size="icon" className="h-8 w-8">
              <MoreHorizontal className="h-4 w-4" />
              <span className="sr-only">Document actions</span>
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-44">
            <DropdownMenuItem asChild>
              <Link href={`/dashboard/documents/${d.id}`}>Open editor</Link>
            </DropdownMenuItem>
            <DropdownMenuItem onClick={() => openChunks(d)}>
              <FileText className="h-4 w-4" />
              View chunks
            </DropdownMenuItem>
            {canManage ? (
              <>
                {d.status === "quarantined" ? (
                  <DropdownMenuItem onClick={() => openReview(d)}>
                    <ShieldAlert className="h-4 w-4" />
                    Review
                  </DropdownMenuItem>
                ) : (
                  <DropdownMenuItem
                    onClick={() => reprocess.mutate(d.id)}
                    disabled={reprocess.isPending}
                  >
                    <RefreshCw className="h-4 w-4" />
                    Reprocess
                  </DropdownMenuItem>
                )}
                {d.status === "indexed" ? (
                  d.verification_status === "verified" ? (
                    <DropdownMenuItem
                      onClick={() => setVerification.mutate({ id: d.id, verify: false })}
                    >
                      <ShieldCheck className="h-4 w-4" />
                      Clear verification
                    </DropdownMenuItem>
                  ) : (
                    <DropdownMenuItem
                      onClick={() => setVerification.mutate({ id: d.id, verify: true })}
                    >
                      <BadgeCheck className="h-4 w-4" />
                      Verify
                    </DropdownMenuItem>
                  )
                ) : null}
                <DropdownMenuSeparator />
                <DropdownMenuItem
                  className="text-destructive focus:text-destructive"
                  onClick={() => setDeleteTarget(d)}
                >
                  <Trash2 className="h-4 w-4" />
                  Delete
                </DropdownMenuItem>
              </>
            ) : null}
          </DropdownMenuContent>
        </DropdownMenu>
      ),
    },
  ];

  return (
    <div className="space-y-6">
      <PageHeader
        title="Documents"
        description="Every ingested source across your knowledge bases."
      />

      <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
        <div className="relative flex-1 sm:max-w-xs">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            placeholder="Search by title…"
            value={rawSearch}
            onChange={(e) => setRawSearch(e.target.value)}
            className="pl-9"
          />
          {isFetching && rawSearch ? (
            <Loader2 className="absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 animate-spin text-muted-foreground" />
          ) : null}
        </div>

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

        <Select value={status} onValueChange={setStatus}>
          <SelectTrigger className="sm:w-40">
            <SelectValue placeholder="Any status" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>Any status</SelectItem>
            {STATUS_OPTIONS.map((s) => (
              <SelectItem key={s} value={s} className="capitalize">
                {s}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        <Select value={author} onValueChange={setAuthor}>
          <SelectTrigger className="sm:w-44">
            <SelectValue placeholder="Everyone" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>Everyone</SelectItem>
            <SelectItem value="mcp">Written by agents</SelectItem>
          </SelectContent>
        </Select>
      </div>

      {isError ? (
        <EmptyState
          icon={FileText}
          title="Couldn't load documents"
          description="Something went wrong. Please try again."
        />
      ) : (
        <>
          <DataTable
            columns={columns}
            data={items}
            rowKey={(d) => d.id}
            isLoading={isLoading}
            empty={
              <EmptyState
                compact
                icon={Upload}
                title="No documents found"
                description="Adjust your filters, or add documents from a collection."
              />
            }
          />

          {totalPages > 1 || total > 0 ? (
            <div className="flex items-center justify-between gap-4">
              <p className="text-sm text-muted-foreground">
                {total === 0
                  ? "No results"
                  : `Showing ${(page - 1) * PAGE_SIZE + 1}–${Math.min(
                      page * PAGE_SIZE,
                      total,
                    )} of ${formatNumber(total)}`}
              </p>
              <div className="flex items-center gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setPage((p) => Math.max(1, p - 1))}
                  disabled={page <= 1 || isFetching}
                >
                  <ChevronLeft className="h-4 w-4" />
                  Previous
                </Button>
                <span className="text-sm tabular-nums text-muted-foreground">
                  {page} / {totalPages}
                </span>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                  disabled={page >= totalPages || isFetching}
                >
                  Next
                  <ChevronRight className="h-4 w-4" />
                </Button>
              </div>
            </div>
          ) : null}
        </>
      )}

      <ChunkSheet doc={activeDoc} open={sheetOpen} onOpenChange={setSheetOpen} />

      <QuarantineReviewDialog
        documentId={reviewDocId}
        open={reviewOpen}
        onOpenChange={setReviewOpen}
      />

      <Dialog open={!!deleteTarget} onOpenChange={(o) => !o && setDeleteTarget(null)}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Delete document?</DialogTitle>
            <DialogDescription>
              This permanently removes{" "}
              <span className="break-words font-medium text-foreground">
                {deleteTarget?.title}
              </span>{" "}
              and all of its indexed chunks. This cannot be undone.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="ghost"
              onClick={() => setDeleteTarget(null)}
              disabled={remove.isPending}
            >
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={() => deleteTarget && remove.mutate(deleteTarget.id)}
              disabled={remove.isPending}
            >
              {remove.isPending ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin" />
                  Deleting…
                </>
              ) : (
                "Delete"
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

export default function DocumentsPage() {
  return (
    <React.Suspense fallback={<div className="h-4" />}>
      <DocumentsInner />
    </React.Suspense>
  );
}
