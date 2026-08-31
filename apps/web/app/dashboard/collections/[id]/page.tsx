"use client";

import * as React from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  FileText,
  Loader2,
  MoreHorizontal,
  RefreshCw,
  Settings2,
  ShieldAlert,
  Trash2,
} from "lucide-react";
import { toast } from "sonner";

import { DataTable, type Column } from "@/components/dashboard/data-table";
import { EmptyState } from "@/components/dashboard/empty-state";
import { PageHeader } from "@/components/dashboard/page-header";
import { PageLoading } from "@/components/dashboard/loading";
import {
  DocumentStatusBadge,
  isProcessingStatus,
} from "@/components/knowledge/document-status-badge";
import { QuarantineReviewDialog } from "@/components/knowledge/quarantine-review-dialog";
import { useQuarantineAlert } from "@/components/knowledge/use-quarantine-alert";
import { UploadDialog } from "@/components/knowledge/upload-dialog";
import { VisibilityBadge } from "@/components/knowledge/visibility-badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
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
import { Label } from "@/components/ui/label";
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
import { Textarea } from "@/components/ui/textarea";
import { api, ApiError } from "@/lib/api";
import { formatBytes, formatDate, formatNumber } from "@/lib/utils";
import type {
  Collection,
  DocumentItem,
  Page,
  PermissionLevel,
  Visibility,
} from "@/lib/types";

type CollectionDetail = Collection & { permission?: PermissionLevel | null };

const PERM_RANK: Record<PermissionLevel, number> = {
  none: 0,
  viewer: 1,
  editor: 2,
  manager: 3,
};

/** True when `perm` is at least `needed`; unknown permission optimistically allows. */
function permissionAtLeast(
  perm: PermissionLevel | null | undefined,
  needed: PermissionLevel,
): boolean {
  if (perm == null) return true; // backend still enforces
  return PERM_RANK[perm] >= PERM_RANK[needed];
}

interface DocumentChunkItem {
  id: string;
  document_id: string;
  chunk_index: number;
  content: string;
  token_count: number;
}

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

const VISIBILITY_OPTIONS: { value: Visibility; label: string }[] = [
  { value: "private", label: "Private" },
  { value: "team", label: "Team" },
  { value: "org", label: "Organization" },
  { value: "public", label: "Public" },
];

const PERMISSION_OPTIONS: { value: PermissionLevel; label: string }[] = [
  { value: "none", label: "No access" },
  { value: "viewer", label: "Viewer" },
  { value: "editor", label: "Editor" },
  { value: "manager", label: "Manager" },
];

function SettingsDialog({
  collection,
  open,
  onOpenChange,
}: {
  collection: CollectionDetail;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const queryClient = useQueryClient();
  const [name, setName] = React.useState(collection.name);
  const [description, setDescription] = React.useState(collection.description ?? "");
  const [visibility, setVisibility] = React.useState<Visibility>(collection.visibility);
  const [defaultPermission, setDefaultPermission] = React.useState<PermissionLevel>(
    collection.default_permission,
  );

  // Re-sync when the dialog opens for a (potentially updated) collection.
  React.useEffect(() => {
    if (open) {
      setName(collection.name);
      setDescription(collection.description ?? "");
      setVisibility(collection.visibility);
      setDefaultPermission(collection.default_permission);
    }
  }, [open, collection]);

  const mutation = useMutation<Collection>({
    mutationFn: () =>
      api.patch<Collection>(`/collections/${collection.id}`, {
        name: name.trim(),
        description: description.trim() || null,
        visibility,
        default_permission: defaultPermission,
      }),
    onSuccess: () => {
      toast.success("Collection updated");
      queryClient.invalidateQueries({ queryKey: ["collection", collection.id] });
      queryClient.invalidateQueries({ queryKey: ["collections"] });
      onOpenChange(false);
    },
    onError: (err) =>
      toast.error(err instanceof ApiError ? err.message : "Failed to update"),
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Collection settings</DialogTitle>
          <DialogDescription>
            Update how this knowledge base is named and shared.
          </DialogDescription>
        </DialogHeader>
        <form
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault();
            if (name.trim()) mutation.mutate();
          }}
        >
          <div className="space-y-1.5">
            <Label htmlFor="edit-name">Name</Label>
            <Input
              id="edit-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              maxLength={255}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="edit-desc">Description</Label>
            <Textarea
              id="edit-desc"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              maxLength={2048}
            />
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label>Visibility</Label>
              <Select
                value={visibility}
                onValueChange={(v) => setVisibility(v as Visibility)}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {VISIBILITY_OPTIONS.map((o) => (
                    <SelectItem key={o.value} value={o.value}>
                      {o.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label>Default permission</Label>
              <Select
                value={defaultPermission}
                onValueChange={(v) => setDefaultPermission(v as PermissionLevel)}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {PERMISSION_OPTIONS.map((o) => (
                    <SelectItem key={o.value} value={o.value}>
                      {o.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
          <DialogFooter>
            <Button
              type="button"
              variant="ghost"
              onClick={() => onOpenChange(false)}
              disabled={mutation.isPending}
            >
              Cancel
            </Button>
            <Button type="submit" disabled={!name.trim() || mutation.isPending}>
              {mutation.isPending ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin" />
                  Saving…
                </>
              ) : (
                "Save changes"
              )}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export default function CollectionDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = React.use(params);
  const router = useRouter();
  const queryClient = useQueryClient();

  const [settingsOpen, setSettingsOpen] = React.useState(false);
  const [confirmDelete, setConfirmDelete] = React.useState(false);
  const [activeDoc, setActiveDoc] = React.useState<DocumentItem | null>(null);
  const [sheetOpen, setSheetOpen] = React.useState(false);
  const [deleteTarget, setDeleteTarget] = React.useState<DocumentItem | null>(null);
  const [reviewDocId, setReviewDocId] = React.useState<string | null>(null);
  const [reviewOpen, setReviewOpen] = React.useState(false);

  const {
    data: collection,
    isLoading,
    isError,
  } = useQuery<CollectionDetail>({
    queryKey: ["collection", id],
    queryFn: () => api.get<CollectionDetail>(`/collections/${id}`),
  });

  const docParams = { collection_id: id, page: 1, page_size: 100 };
  const { data: docPage, isLoading: docsLoading } = useQuery<Page<DocumentItem>>({
    queryKey: ["documents", docParams],
    queryFn: () => api.get<Page<DocumentItem>>("/documents", docParams),
    enabled: !!collection,
    refetchInterval: (query) => {
      const items = query.state.data?.items ?? [];
      return items.some((d) => isProcessingStatus(d.status)) ? 4000 : false;
    },
  });

  // Derived before the early returns below so hooks always run in the same order.
  const canEdit = permissionAtLeast(collection?.permission, "editor");
  const canManage = permissionAtLeast(collection?.permission, "manager");

  function openReview(doc: DocumentItem) {
    setReviewDocId(doc.id);
    setReviewOpen(true);
  }

  useQuarantineAlert(docPage?.items, canEdit ? openReview : undefined);

  const reprocess = useMutation({
    mutationFn: (docId: string) =>
      api.post<DocumentItem>(`/documents/${docId}/reprocess`),
    onSuccess: () => {
      toast.success("Reprocessing started");
      queryClient.invalidateQueries({ queryKey: ["documents"] });
    },
    onError: (err) =>
      toast.error(err instanceof ApiError ? err.message : "Failed to reprocess"),
  });

  const removeDoc = useMutation({
    mutationFn: (docId: string) => api.delete(`/documents/${docId}`),
    onSuccess: () => {
      toast.success("Document deleted");
      setDeleteTarget(null);
      queryClient.invalidateQueries({ queryKey: ["documents"] });
      queryClient.invalidateQueries({ queryKey: ["collection", id] });
      queryClient.invalidateQueries({ queryKey: ["collections"] });
    },
    onError: (err) =>
      toast.error(err instanceof ApiError ? err.message : "Failed to delete"),
  });

  const removeCollection = useMutation({
    mutationFn: () => api.delete(`/collections/${id}`),
    onSuccess: () => {
      toast.success("Collection deleted");
      queryClient.invalidateQueries({ queryKey: ["collections"] });
      router.push("/dashboard/collections");
    },
    onError: (err) =>
      toast.error(err instanceof ApiError ? err.message : "Failed to delete"),
  });

  if (isLoading) return <PageLoading />;
  if (isError || !collection) {
    return (
      <EmptyState
        icon={FileText}
        title="Collection not found"
        description="It may have been deleted, or you no longer have access."
        actions={
          <Button asChild variant="outline">
            <Link href="/dashboard/collections">Back to collections</Link>
          </Button>
        }
      />
    );
  }

  const items = docPage?.items ?? [];

  function openChunks(doc: DocumentItem) {
    setActiveDoc(doc);
    setSheetOpen(true);
  }

  const columns: Column<DocumentItem>[] = [
    {
      id: "title",
      header: "Title",
      cell: (d) => (
        <Link
          href={`/dashboard/documents/${d.id}`}
          className="flex max-w-[320px] flex-col items-start text-left"
        >
          <span className="max-w-full truncate font-medium text-foreground hover:text-primary">
            {d.title}
          </span>
          {d.source_uri ? (
            <span className="max-w-[320px] truncate text-xs text-muted-foreground">
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
            {canEdit ? (
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
      <Link
        href="/dashboard/collections"
        className="inline-flex items-center gap-1.5 text-sm text-muted-foreground transition-colors hover:text-foreground"
      >
        <ArrowLeft className="h-4 w-4" />
        Knowledge Bases
      </Link>

      <PageHeader
        title={
          <span className="flex items-center gap-3">
            {collection.name}
            <VisibilityBadge visibility={collection.visibility} />
          </span>
        }
        description={collection.description || "No description provided."}
        actions={
          <div className="flex items-center gap-2">
            {canEdit ? <UploadDialog collectionId={collection.id} /> : null}
            {canManage ? (
              <>
                <Button
                  variant="outline"
                  size="icon"
                  onClick={() => setSettingsOpen(true)}
                  aria-label="Collection settings"
                >
                  <Settings2 className="h-4 w-4" />
                </Button>
                <Button
                  variant="outline"
                  size="icon"
                  className="text-destructive hover:text-destructive"
                  onClick={() => setConfirmDelete(true)}
                  aria-label="Delete collection"
                >
                  <Trash2 className="h-4 w-4" />
                </Button>
              </>
            ) : null}
          </div>
        }
      />

      <div className="grid gap-4 sm:grid-cols-3">
        <Card className="p-4">
          <p className="text-xs font-medium text-muted-foreground">Documents</p>
          <p
            className="mt-1 text-2xl font-semibold tabular-nums"
            data-testid="documents-count"
          >
            {formatNumber(collection.document_count)}
          </p>
        </Card>
        <Card className="p-4">
          <p className="text-xs font-medium text-muted-foreground">Embedding model</p>
          <p
            className="mt-1 truncate text-sm font-medium"
            title={collection.embedding_model}
          >
            {collection.embedding_model}
          </p>
        </Card>
        <Card className="p-4">
          <p className="text-xs font-medium text-muted-foreground">Created</p>
          <p className="mt-1 text-sm font-medium">{formatDate(collection.created_at)}</p>
        </Card>
      </div>

      <DataTable
        columns={columns}
        data={items}
        rowKey={(d) => d.id}
        isLoading={docsLoading}
        empty={
          <EmptyState
            compact
            icon={FileText}
            title="No documents yet"
            description="Add text, a URL or a file to start building this knowledge base."
            actions={canEdit ? <UploadDialog collectionId={collection.id} /> : undefined}
          />
        }
      />

      <ChunkSheet doc={activeDoc} open={sheetOpen} onOpenChange={setSheetOpen} />

      <QuarantineReviewDialog
        documentId={reviewDocId}
        open={reviewOpen}
        onOpenChange={setReviewOpen}
      />

      <SettingsDialog
        collection={collection}
        open={settingsOpen}
        onOpenChange={setSettingsOpen}
      />

      {/* Delete document confirm */}
      <Dialog open={!!deleteTarget} onOpenChange={(o) => !o && setDeleteTarget(null)}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Delete document?</DialogTitle>
            <DialogDescription>
              This permanently removes{" "}
              <span className="break-words font-medium text-foreground">
                {deleteTarget?.title}
              </span>{" "}
              and all of its indexed chunks.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="ghost"
              onClick={() => setDeleteTarget(null)}
              disabled={removeDoc.isPending}
            >
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={() => deleteTarget && removeDoc.mutate(deleteTarget.id)}
              disabled={removeDoc.isPending}
            >
              {removeDoc.isPending ? (
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

      {/* Delete collection confirm */}
      <Dialog open={confirmDelete} onOpenChange={setConfirmDelete}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Delete this collection?</DialogTitle>
            <DialogDescription>
              This permanently deletes{" "}
              <span className="font-medium text-foreground">{collection.name}</span> and
              all {formatNumber(collection.document_count)} of its documents. This cannot
              be undone.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="ghost"
              onClick={() => setConfirmDelete(false)}
              disabled={removeCollection.isPending}
            >
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={() => removeCollection.mutate()}
              disabled={removeCollection.isPending}
            >
              {removeCollection.isPending ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin" />
                  Deleting…
                </>
              ) : (
                "Delete collection"
              )}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
