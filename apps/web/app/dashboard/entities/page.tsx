"use client";

import * as React from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { Boxes, FileText, Loader2, Plus, Search } from "lucide-react";

import { EmptyState } from "@/components/dashboard/empty-state";
import { PageHeader } from "@/components/dashboard/page-header";
import { TableSkeleton } from "@/components/dashboard/loading";
import { isOrgAdmin } from "@/components/governance/role-badge";
import { DocumentStatusBadge } from "@/components/knowledge/document-status-badge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import type { DocumentItem, Entity, EntityKind } from "@/lib/types";

const ALL = "all" as const;
type KindFilter = EntityKind | typeof ALL;

const KINDS: EntityKind[] = ["person", "org", "product", "project", "location", "other"];

const KIND_LABELS: Record<EntityKind, string> = {
  person: "Person",
  org: "Organization",
  product: "Product",
  project: "Project",
  location: "Location",
  other: "Other",
};

const KIND_VARIANT: Record<EntityKind, "default" | "info" | "success" | "muted"> = {
  person: "info",
  org: "success",
  product: "default",
  project: "info",
  location: "muted",
  other: "muted",
};

const errMsg = (e: unknown, f = "Something went wrong") =>
  e instanceof ApiError ? e.message : f;

/** Debounce a rapidly-changing value (e.g. a search box). */
function useDebounced<T>(value: T, delay = 300): T {
  const [debounced, setDebounced] = React.useState(value);
  React.useEffect(() => {
    const id = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(id);
  }, [value, delay]);
  return debounced;
}

export default function EntitiesPage() {
  const { role } = useAuth();
  const admin = isOrgAdmin(role);

  const [kind, setKind] = React.useState<KindFilter>(ALL);
  const [rawQuery, setRawQuery] = React.useState("");
  const q = useDebounced(rawQuery);
  const [selected, setSelected] = React.useState<Entity | null>(null);

  const entitiesQuery = useQuery<Entity[]>({
    queryKey: ["entities", kind, q],
    queryFn: () =>
      api.get<Entity[]>("/entities", {
        kind: kind === ALL ? undefined : kind,
        q: q.trim() || undefined,
        limit: 200,
      }),
  });

  const entities = entitiesQuery.data ?? [];
  const filtered = kind !== ALL || q.trim().length > 0;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Entities"
        description="Browse the people, organizations and topics extracted from your knowledge, then jump to the documents that mention them. Only documents you can access are shown."
      />

      <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
        <div className="relative flex-1">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            placeholder="Search entities…"
            className="pl-9"
            value={rawQuery}
            onChange={(e) => setRawQuery(e.target.value)}
          />
          {entitiesQuery.isFetching && rawQuery ? (
            <Loader2 className="absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 animate-spin text-muted-foreground" />
          ) : null}
        </div>
        <div className="flex items-center gap-2">
          <Label htmlFor="kind-filter" className="sr-only">
            Kind
          </Label>
          <Select value={kind} onValueChange={(v) => setKind(v as KindFilter)}>
            <SelectTrigger id="kind-filter" className="w-full sm:w-44">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>All kinds</SelectItem>
              {KINDS.map((k) => (
                <SelectItem key={k} value={k}>
                  {KIND_LABELS[k]}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>

      {entitiesQuery.isLoading ? (
        <TableSkeleton rows={8} columns={3} />
      ) : entitiesQuery.isError ? (
        <EmptyState
          icon={Boxes}
          title="Couldn't load entities"
          description={errMsg(
            entitiesQuery.error,
            "Something went wrong fetching entities. Try again in a moment.",
          )}
        />
      ) : entities.length === 0 ? (
        <EmptyState
          icon={Boxes}
          title={filtered ? "No matching entities" : "No entities yet"}
          description={
            filtered
              ? "No entities match your search and filter. Try broadening them."
              : "Entities are extracted automatically as documents are indexed. They'll appear here once your knowledge is processed."
          }
          actions={
            !filtered && admin ? (
              <Button asChild>
                <Link href="/dashboard/documents">
                  <Plus className="h-4 w-4" />
                  Add documents
                </Link>
              </Button>
            ) : null
          }
        />
      ) : (
        <Card className="overflow-hidden">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Name</TableHead>
                <TableHead>Kind</TableHead>
                <TableHead className="text-right">Documents</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {entities.map((entity) => (
                <TableRow
                  key={entity.id}
                  role="button"
                  tabIndex={0}
                  aria-label={`View documents mentioning ${entity.name}`}
                  className="cursor-pointer"
                  onClick={() => setSelected(entity)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      setSelected(entity);
                    }
                  }}
                >
                  <TableCell className="font-medium">{entity.name}</TableCell>
                  <TableCell>
                    <Badge variant={KIND_VARIANT[entity.kind]}>
                      {KIND_LABELS[entity.kind] ?? entity.kind}
                    </Badge>
                  </TableCell>
                  <TableCell className="text-right tabular-nums text-muted-foreground">
                    {entity.document_count.toLocaleString()}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Card>
      )}

      <EntityDocumentsDialog
        entity={selected}
        onOpenChange={(open) => !open && setSelected(null)}
      />
    </div>
  );
}

function EntityDocumentsDialog({
  entity,
  onOpenChange,
}: {
  entity: Entity | null;
  onOpenChange: (open: boolean) => void;
}) {
  const docsQuery = useQuery<DocumentItem[]>({
    queryKey: ["entity-documents", entity?.id],
    queryFn: () => api.get<DocumentItem[]>(`/entities/${entity!.id}/documents`),
    enabled: entity !== null,
  });

  const documents = docsQuery.data ?? [];

  return (
    <Dialog open={entity !== null} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] max-w-lg overflow-hidden">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <span className="truncate">{entity?.name}</span>
            {entity ? (
              <Badge variant={KIND_VARIANT[entity.kind]}>
                {KIND_LABELS[entity.kind] ?? entity.kind}
              </Badge>
            ) : null}
          </DialogTitle>
          <DialogDescription>
            {entity
              ? `Mentioned in ${entity.document_count.toLocaleString()} ${
                  entity.document_count === 1 ? "document" : "documents"
                } you can access.`
              : null}
          </DialogDescription>
        </DialogHeader>

        <div className="-mr-2 max-h-[55vh] space-y-2 overflow-y-auto pr-2">
          {docsQuery.isLoading ? (
            Array.from({ length: 4 }).map((_, i) => (
              <div
                key={i}
                className="flex items-center justify-between gap-3 rounded-md border p-3"
              >
                <Skeleton className="h-4 w-2/3" />
                <Skeleton className="h-5 w-20" />
              </div>
            ))
          ) : docsQuery.isError ? (
            <EmptyState
              compact
              icon={FileText}
              title="Couldn't load documents"
              description={errMsg(
                docsQuery.error,
                "Something went wrong. Try again in a moment.",
              )}
            />
          ) : documents.length === 0 ? (
            <EmptyState
              compact
              icon={FileText}
              title="No documents"
              description="You don't have access to any documents that mention this entity."
            />
          ) : (
            documents.map((doc) => (
              <div
                key={doc.id}
                className="flex items-center justify-between gap-3 rounded-md border p-3"
              >
                <span className="min-w-0 truncate text-sm font-medium">{doc.title}</span>
                <DocumentStatusBadge
                  status={doc.status}
                  error={doc.error}
                  className="shrink-0"
                />
              </div>
            ))
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
