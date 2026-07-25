"use client";

import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ShieldCheck, Trash2, UserRound, UsersRound } from "lucide-react";
import { toast } from "sonner";

import { DataTable, type Column } from "@/components/dashboard/data-table";
import { EmptyState } from "@/components/dashboard/empty-state";
import { PageHeader } from "@/components/dashboard/page-header";
import { isOrgAdmin } from "@/components/governance/role-badge";
import {
  PermissionBadge,
  PermissionSelect,
} from "@/components/governance/permission-select";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import type {
  AccessGrant,
  Collection,
  DocumentItem,
  Membership,
  Page,
  PermissionLevel,
  PrincipalType,
  ResourceType,
  Team,
} from "@/lib/types";

type CollectionWithPerm = Collection & { permission?: PermissionLevel | null };

function errMsg(e: unknown, fallback = "Something went wrong") {
  return e instanceof ApiError ? e.message : fallback;
}

export default function PermissionsPage() {
  const { role } = useAuth();
  const canListMembers = isOrgAdmin(role);
  const queryClient = useQueryClient();

  const [resourceType, setResourceType] =
    React.useState<ResourceType>("collection");
  const [collectionId, setCollectionId] = React.useState("");
  const [documentId, setDocumentId] = React.useState("");

  const resourceId = resourceType === "collection" ? collectionId : documentId;
  const hasResource = Boolean(resourceId);

  // --- Resource pickers ---------------------------------------------------
  const collectionsQuery = useQuery<CollectionWithPerm[]>({
    queryKey: ["collections"],
    queryFn: () => api.get<CollectionWithPerm[]>("/collections"),
  });

  const documentsQuery = useQuery<Page<DocumentItem>>({
    queryKey: ["documents", collectionId, "for-access"],
    queryFn: () =>
      api.get<Page<DocumentItem>>("/documents", {
        collection_id: collectionId,
        page_size: 100,
      }),
    enabled: resourceType === "document" && Boolean(collectionId),
  });

  // --- Effective permission (drives whether grant management is allowed) ---
  const effectiveQuery = useQuery<{ permission: PermissionLevel }>({
    queryKey: ["effective", resourceType, resourceId],
    queryFn: () =>
      api.get<{ permission: PermissionLevel }>("/permissions/effective", {
        resource_type: resourceType,
        resource_id: resourceId,
      }),
    enabled: hasResource,
  });
  const effective = effectiveQuery.data?.permission;
  const canManage = effective === "manager";

  // --- Grants (only readable by managers) ---------------------------------
  const grantsKey = ["grants", resourceType, resourceId];
  const grantsQuery = useQuery<AccessGrant[]>({
    queryKey: grantsKey,
    queryFn: () =>
      api.get<AccessGrant[]>("/permissions", {
        resource_type: resourceType,
        resource_id: resourceId,
      }),
    enabled: hasResource && canManage,
  });

  // --- Principal directories ----------------------------------------------
  const membersQuery = useQuery<Membership[]>({
    queryKey: ["org-members"],
    queryFn: () => api.get<Membership[]>("/orgs/members"),
    enabled: hasResource && canManage && canListMembers,
  });
  const teamsQuery = useQuery<Team[]>({
    queryKey: ["teams"],
    queryFn: () => api.get<Team[]>("/teams"),
    enabled: hasResource && canManage,
  });

  const invalidateGrants = () =>
    queryClient.invalidateQueries({ queryKey: grantsKey });

  const upsertGrant = useMutation({
    mutationFn: (body: {
      principal_type: PrincipalType;
      principal_id: string;
      permission: PermissionLevel;
    }) =>
      api.post<AccessGrant>("/permissions", {
        resource_type: resourceType,
        resource_id: resourceId,
        ...body,
      }),
    onSuccess: () => {
      toast.success("Access updated");
      void invalidateGrants();
    },
    onError: (e) => toast.error(errMsg(e, "Couldn't update access")),
  });

  const revokeGrant = useMutation({
    mutationFn: (id: string) => api.delete(`/permissions/${id}`),
    onSuccess: () => {
      toast.success("Access revoked");
      void invalidateGrants();
    },
    onError: (e) => toast.error(errMsg(e, "Couldn't revoke access")),
  });

  const columns: Column<AccessGrant>[] = [
    {
      id: "principal",
      header: "Principal",
      cell: (g) => (
        <div className="flex items-center gap-2.5">
          <span className="flex h-8 w-8 items-center justify-center rounded-full bg-muted text-muted-foreground">
            {g.principal_type === "team" ? (
              <UsersRound className="h-4 w-4" />
            ) : (
              <UserRound className="h-4 w-4" />
            )}
          </span>
          <div className="min-w-0">
            <p className="truncate text-sm font-medium">
              {g.principal_name || g.principal_id}
            </p>
            <p className="text-xs capitalize text-muted-foreground">
              {g.principal_type}
            </p>
          </div>
        </div>
      ),
    },
    {
      id: "permission",
      header: "Permission",
      cell: (g) =>
        canManage ? (
          <PermissionSelect
            value={g.permission}
            withHints={false}
            className="h-8 w-[150px]"
            disabled={upsertGrant.isPending}
            onValueChange={(permission) =>
              upsertGrant.mutate({
                principal_type: g.principal_type,
                principal_id: g.principal_id,
                permission,
              })
            }
          />
        ) : (
          <PermissionBadge level={g.permission} />
        ),
    },
    {
      id: "actions",
      header: "",
      align: "right",
      cell: (g) =>
        canManage ? (
          <Button
            variant="ghost"
            size="icon"
            aria-label="Revoke access"
            className="text-destructive hover:text-destructive"
            disabled={revokeGrant.isPending}
            onClick={() => revokeGrant.mutate(g.id)}
          >
            <Trash2 className="h-4 w-4" />
          </Button>
        ) : null,
    },
  ];

  return (
    <div className="space-y-6">
      <PageHeader
        title="Access"
        description="Review and manage who can see each knowledge base and document."
      />

      {/* Resource picker */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Choose a resource</CardTitle>
        </CardHeader>
        <CardContent className="grid gap-4 sm:grid-cols-3">
          <div className="space-y-1.5">
            <Label htmlFor="resource-type">Type</Label>
            <Select
              value={resourceType}
              onValueChange={(v) => {
                setResourceType(v as ResourceType);
                setDocumentId("");
              }}
            >
              <SelectTrigger id="resource-type" className="capitalize">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="collection">Knowledge base</SelectItem>
                <SelectItem value="document">Document</SelectItem>
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="collection">Knowledge base</Label>
            <Select
              value={collectionId}
              onValueChange={(v) => {
                setCollectionId(v);
                setDocumentId("");
              }}
            >
              <SelectTrigger id="collection">
                <SelectValue placeholder="Select a knowledge base" />
              </SelectTrigger>
              <SelectContent>
                {(collectionsQuery.data ?? []).map((c) => (
                  <SelectItem key={c.id} value={c.id}>
                    {c.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          {resourceType === "document" ? (
            <div className="space-y-1.5">
              <Label htmlFor="document">Document</Label>
              <Select
                value={documentId}
                onValueChange={setDocumentId}
                disabled={!collectionId}
              >
                <SelectTrigger id="document">
                  <SelectValue
                    placeholder={
                      collectionId ? "Select a document" : "Pick a base first"
                    }
                  />
                </SelectTrigger>
                <SelectContent>
                  {(documentsQuery.data?.items ?? []).map((d) => (
                    <SelectItem key={d.id} value={d.id}>
                      <span className="block max-w-[360px] truncate">{d.title}</span>
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          ) : null}
        </CardContent>
      </Card>

      {!hasResource ? (
        <EmptyState
          icon={ShieldCheck}
          title="Select a resource"
          description="Pick a knowledge base or document above to see and manage its access grants."
        />
      ) : (
        <>
          {/* Effective permission + add grant */}
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div className="flex items-center gap-2 text-sm">
              <span className="text-muted-foreground">Your access:</span>
              {effectiveQuery.isLoading || !effective ? (
                <Badge variant="muted">…</Badge>
              ) : (
                <PermissionBadge level={effective} />
              )}
            </div>
            {canManage ? (
              <AddGrantForm
                members={membersQuery.data ?? []}
                teams={teamsQuery.data ?? []}
                canListMembers={canListMembers}
                pending={upsertGrant.isPending}
                onAdd={(body) => upsertGrant.mutate(body)}
              />
            ) : null}
          </div>

          {canManage ? (
            <DataTable
              columns={columns}
              data={grantsQuery.data ?? []}
              rowKey={(g) => g.id}
              isLoading={grantsQuery.isLoading}
              empty={
                <EmptyState
                  compact
                  icon={ShieldCheck}
                  title="No explicit grants"
                  description="Access falls back to this resource's visibility and default permission."
                />
              }
            />
          ) : (
            <EmptyState
              icon={ShieldCheck}
              title="Manager access required"
              description="You can view your own access above, but only a manager of this resource can list and edit its grants."
            />
          )}
        </>
      )}
    </div>
  );
}

function AddGrantForm({
  members,
  teams,
  canListMembers,
  pending,
  onAdd,
}: {
  members: Membership[];
  teams: Team[];
  canListMembers: boolean;
  pending: boolean;
  onAdd: (body: {
    principal_type: PrincipalType;
    principal_id: string;
    permission: PermissionLevel;
  }) => void;
}) {
  const [principalType, setPrincipalType] =
    React.useState<PrincipalType>("user");
  const [principalId, setPrincipalId] = React.useState("");
  const [permission, setPermission] =
    React.useState<PermissionLevel>("viewer");

  const options =
    principalType === "user"
      ? members.map((m) => ({
          id: m.user_id,
          label: m.user?.full_name || m.user?.email || m.user_id,
        }))
      : teams.map((t) => ({ id: t.id, label: t.name }));

  function submit() {
    if (!principalId) return;
    onAdd({
      principal_type: principalType,
      principal_id: principalId,
      permission,
    });
    setPrincipalId("");
  }

  return (
    <div className="flex flex-wrap items-end gap-2 rounded-lg border bg-card p-3">
      <div className="space-y-1">
        <Label className="text-xs">Type</Label>
        <Select
          value={principalType}
          onValueChange={(v) => {
            setPrincipalType(v as PrincipalType);
            setPrincipalId("");
          }}
        >
          <SelectTrigger className="h-9 w-[110px] capitalize">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="user">User</SelectItem>
            <SelectItem value="team">Team</SelectItem>
          </SelectContent>
        </Select>
      </div>

      <div className="space-y-1">
        <Label className="text-xs">Principal</Label>
        <Select value={principalId} onValueChange={setPrincipalId}>
          <SelectTrigger className="h-9 w-[180px]">
            <SelectValue placeholder="Select" />
          </SelectTrigger>
          <SelectContent>
            {options.length === 0 ? (
              <div className="px-2 py-1.5 text-xs text-muted-foreground">
                {principalType === "user" && !canListMembers
                  ? "Admin access needed to list users"
                  : "Nothing to add"}
              </div>
            ) : (
              options.map((o) => (
                <SelectItem key={o.id} value={o.id}>
                  {o.label}
                </SelectItem>
              ))
            )}
          </SelectContent>
        </Select>
      </div>

      <div className="space-y-1">
        <Label className="text-xs">Level</Label>
        <PermissionSelect
          value={permission}
          withHints={false}
          className="h-9 w-[140px]"
          onValueChange={setPermission}
        />
      </div>

      <Separator orientation="vertical" className="hidden h-9 sm:block" />
      <Button onClick={submit} disabled={pending || !principalId}>
        Add grant
      </Button>
    </div>
  );
}
