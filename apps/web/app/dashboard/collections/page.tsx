"use client";

import * as React from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Building2,
  FileText,
  Globe,
  Library,
  Loader2,
  Lock,
  Plus,
  Search,
  Users,
  type LucideIcon,
} from "lucide-react";
import { toast } from "sonner";

import { CardGridSkeleton } from "@/components/dashboard/loading";
import { EmptyState } from "@/components/dashboard/empty-state";
import { PageHeader } from "@/components/dashboard/page-header";
import { VisibilityBadge } from "@/components/knowledge/visibility-badge";
import { Badge, type BadgeProps } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
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
import { Textarea } from "@/components/ui/textarea";
import { orgRoleAtLeast } from "@/components/governance/role-badge";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { cn, formatDate, formatNumber } from "@/lib/utils";
import type {
  Collection,
  PermissionLevel,
  Visibility,
} from "@/lib/types";

const PERMISSION_OPTIONS: { value: PermissionLevel; label: string }[] = [
  { value: "none", label: "No access" },
  { value: "viewer", label: "Viewer - can read & search" },
  { value: "editor", label: "Editor - can add documents" },
  { value: "manager", label: "Manager - full control" },
];

const VISIBILITY_OPTIONS: { value: Visibility; label: string }[] = [
  { value: "private", label: "Private - only you & invitees" },
  { value: "team", label: "Team - a specific team" },
  { value: "org", label: "Organization - everyone in the org" },
  { value: "public", label: "Public - everyone in the org, incl. API keys" },
];

function NewCollectionDialog() {
  const queryClient = useQueryClient();
  const router = useRouter();
  const [open, setOpen] = React.useState(false);
  const [name, setName] = React.useState("");
  const [description, setDescription] = React.useState("");
  const [visibility, setVisibility] = React.useState<Visibility>("private");
  const [defaultPermission, setDefaultPermission] =
    React.useState<PermissionLevel>("viewer");

  function reset() {
    setName("");
    setDescription("");
    setVisibility("private");
    setDefaultPermission("viewer");
  }

  const mutation = useMutation<Collection>({
    mutationFn: () =>
      api.post<Collection>("/collections", {
        name: name.trim(),
        description: description.trim() || undefined,
        visibility,
        default_permission: defaultPermission,
      }),
    onSuccess: (collection) => {
      toast.success("Knowledge base created");
      queryClient.invalidateQueries({ queryKey: ["collections"] });
      setOpen(false);
      reset();
      router.push(`/dashboard/collections/${collection.id}`);
    },
    onError: (err) => {
      toast.error(
        err instanceof ApiError ? err.message : "Failed to create knowledge base",
      );
    },
  });

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (!next) reset();
      }}
    >
      <DialogTrigger asChild>
        <Button>
          <Plus className="h-4 w-4" />
          New knowledge base
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>New knowledge base</DialogTitle>
          <DialogDescription>
            Group related documents into a searchable, permission-scoped
            knowledge base.
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
            <Label htmlFor="col-name">Name</Label>
            <Input
              id="col-name"
              placeholder="Engineering Handbook"
              value={name}
              onChange={(e) => setName(e.target.value)}
              autoFocus
              maxLength={255}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="col-desc">
              Description{" "}
              <span className="font-normal text-muted-foreground">
                (optional)
              </span>
            </Label>
            <Textarea
              id="col-desc"
              placeholder="What lives in this knowledge base?"
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
                onValueChange={(v) =>
                  setDefaultPermission(v as PermissionLevel)
                }
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

          <div className="flex justify-end gap-2 pt-2">
            <Button
              type="button"
              variant="ghost"
              onClick={() => setOpen(false)}
              disabled={mutation.isPending}
            >
              Cancel
            </Button>
            <Button
              type="submit"
              disabled={!name.trim() || mutation.isPending}
            >
              {mutation.isPending ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin" />
                  Creating…
                </>
              ) : (
                "Create knowledge base"
              )}
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function CollectionCard({ collection }: { collection: Collection }) {
  return (
    <Link href={`/dashboard/collections/${collection.id}`}>
      <Card className="flex h-full flex-col gap-3 p-5 transition-colors hover:border-primary/40 hover:bg-accent/30">
        <div className="flex items-start gap-3">
          <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
            <Library className="h-5 w-5" />
          </span>
          <div className="min-w-0 flex-1">
            <h3 className="truncate font-semibold text-foreground">
              {collection.name}
            </h3>
            <p className="truncate text-xs text-muted-foreground">
              Created {formatDate(collection.created_at)}
            </p>
          </div>
        </div>
        <p className="line-clamp-2 min-h-[2.5rem] text-sm text-muted-foreground">
          {collection.description || "No description provided."}
        </p>
        <div className="mt-auto flex items-center justify-between gap-2 pt-1">
          <VisibilityBadge visibility={collection.visibility} />
          <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <FileText className="h-3.5 w-3.5" />
            {formatNumber(collection.document_count)}{" "}
            {collection.document_count === 1 ? "doc" : "docs"}
          </span>
        </div>
      </Card>
    </Link>
  );
}

export default function CollectionsPage() {
  const [search, setSearch] = React.useState("");
  const { role } = useAuth();
  // Only editors and above can create a knowledge base; viewers get a 403 from the
  // API, so don't offer them a button that always fails.
  const canManage = orgRoleAtLeast(role, "editor");
  const { data, isLoading, isError } = useQuery<Collection[]>({
    queryKey: ["collections"],
    queryFn: () => api.get<Collection[]>("/collections"),
  });

  const filtered = React.useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return data ?? [];
    return (data ?? []).filter(
      (c) =>
        c.name.toLowerCase().includes(q) ||
        (c.description ?? "").toLowerCase().includes(q),
    );
  }, [data, search]);

  return (
    <div className="space-y-6">
      <PageHeader
        title="Knowledge Bases"
        description="Organize documents into searchable, permission-scoped knowledge bases."
        actions={canManage ? <NewCollectionDialog /> : undefined}
      />

      {data && data.length > 0 ? (
        <div className="relative max-w-sm">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            placeholder="Search knowledge bases…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="pl-9"
          />
        </div>
      ) : null}

      {isLoading ? (
        <CardGridSkeleton />
      ) : isError ? (
        <EmptyState
          icon={Library}
          title="Couldn't load collections"
          description="Something went wrong fetching your knowledge bases. Try again in a moment."
        />
      ) : filtered.length === 0 ? (
        search ? (
          <EmptyState
            icon={Search}
            title="No matches"
            description={`No knowledge bases match “${search}”.`}
          />
        ) : (
          <EmptyState
            icon={Library}
            title="No knowledge bases yet"
            description={
              canManage
                ? "Create your first knowledge base to start ingesting and searching documents."
                : "No knowledge bases have been shared with you yet. Ask an admin or editor to grant you access."
            }
            actions={canManage ? <NewCollectionDialog /> : undefined}
          />
        )
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {filtered.map((collection) => (
            <CollectionCard key={collection.id} collection={collection} />
          ))}
        </div>
      )}
    </div>
  );
}
