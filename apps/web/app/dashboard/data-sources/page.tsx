"use client";

import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertCircle,
  Database,
  Loader2,
  Plus,
  RefreshCw,
  Trash2,
  Users,
} from "lucide-react";
import { toast } from "sonner";

import { CardGridSkeleton } from "@/components/dashboard/loading";
import { EmptyState } from "@/components/dashboard/empty-state";
import { PageHeader } from "@/components/dashboard/page-header";
import { isOrgAdmin } from "@/components/governance/role-badge";
import { Badge } from "@/components/ui/badge";
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
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import type {
  Collection,
  DataSource,
  DataSourceKind,
  DataSourceStatus,
  ExternalPrincipalItem,
  Identity,
  Membership,
  SyncResult,
  Team,
  Visibility,
} from "@/lib/types";
import { cn, formatDate, formatNumber } from "@/lib/utils";

const KINDS: DataSourceKind[] = [
  "local_folder",
  "google_drive",
  "slack",
  "github",
  "notion",
  "confluence",
];

const KIND_LABELS: Record<DataSourceKind, string> = {
  local_folder: "Local folder",
  google_drive: "Google Drive",
  slack: "Slack",
  github: "GitHub",
  notion: "Notion",
  confluence: "Confluence",
};

/**
 * Kinds whose live fetch is implemented on the server. The rest validate and store
 * their configuration today - the sync engine, source-ACL mapping and identity
 * resolution behind them are provider-agnostic and done - but they cannot pull yet,
 * so a sync reports them as unavailable. See docs/ROADMAP.md.
 */
const LIVE_KINDS: DataSourceKind[] = ["local_folder"];

const isLiveKind = (kind: DataSourceKind) => LIVE_KINDS.includes(kind);

const CONFIG_PLACEHOLDER: Record<DataSourceKind, string> = {
  local_folder: '{\n  "root": "/path/to/folder"\n}',
  google_drive: '{\n  "folder_id": "…"\n}',
  slack: '{\n  "channels": ["general"]\n}',
  github: '{\n  "repo": "owner/name"\n}',
  notion: '{\n  "database_id": "…"\n}',
  confluence: '{\n  "space": "ENG"\n}',
};

const STATUS_VARIANT: Record<
  DataSourceStatus,
  "success" | "info" | "muted" | "destructive"
> = {
  active: "success",
  syncing: "info",
  paused: "muted",
  error: "destructive",
};

const VISIBILITIES: Visibility[] = ["private", "team", "org", "public"];

const VISIBILITY_LABELS: Record<Visibility, string> = {
  private: "Private",
  team: "Team",
  org: "Organization",
  public: "Public",
};

const errMsg = (e: unknown, f = "Something went wrong") =>
  e instanceof ApiError ? e.message : f;

const RELATIVE = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
const DIVISIONS: { amount: number; unit: Intl.RelativeTimeFormatUnit }[] = [
  { amount: 60, unit: "second" },
  { amount: 60, unit: "minute" },
  { amount: 24, unit: "hour" },
  { amount: 7, unit: "day" },
  { amount: 4.34524, unit: "week" },
  { amount: 12, unit: "month" },
  { amount: Number.POSITIVE_INFINITY, unit: "year" },
];

/** Human, relative label for a timestamp ("3 hours ago"), falling back to a date. */
function relativeTime(value: string): string {
  let duration = (new Date(value).getTime() - Date.now()) / 1000;
  for (const division of DIVISIONS) {
    if (Math.abs(duration) < division.amount) {
      return RELATIVE.format(Math.round(duration), division.unit);
    }
    duration /= division.amount;
  }
  return formatDate(value);
}

export default function DataSourcesPage() {
  const { role } = useAuth();
  const admin = isOrgAdmin(role);
  const queryClient = useQueryClient();

  const [createOpen, setCreateOpen] = React.useState(false);
  const [managing, setManaging] = React.useState<DataSource | null>(null);
  const [deleting, setDeleting] = React.useState<DataSource | null>(null);

  const sourcesQuery = useQuery<DataSource[]>({
    queryKey: ["data-sources"],
    queryFn: () => api.get<DataSource[]>("/data-sources"),
  });

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["data-sources"] });

  const syncSource = useMutation({
    mutationFn: (id: string) => api.post<SyncResult>(`/data-sources/${id}/sync`),
    onSuccess: (res) => {
      toast.success(
        `Synced: ${res.created} added, ${res.updated} updated, ${res.deleted} removed`,
      );
      void invalidate();
    },
    onError: (e) => toast.error(errMsg(e, "Sync failed")),
  });

  const deleteSource = useMutation({
    mutationFn: (id: string) => api.delete(`/data-sources/${id}`),
    onSuccess: () => {
      toast.success("Data source removed");
      setDeleting(null);
      void invalidate();
    },
    onError: (e) => toast.error(errMsg(e, "Couldn't remove data source")),
  });

  const sources = sourcesQuery.data ?? [];

  return (
    <div className="space-y-6">
      <PageHeader
        title="Data sources"
        description="Sync a source's documents into a collection and mirror its access controls. Local folder is the connector that is live today; the others store their configuration but do not sync yet."
        actions={
          admin ? (
            <Button onClick={() => setCreateOpen(true)}>
              <Plus className="h-4 w-4" />
              Add data source
            </Button>
          ) : null
        }
      />

      {sourcesQuery.isLoading ? (
        <CardGridSkeleton count={3} />
      ) : sourcesQuery.isError ? (
        <EmptyState
          icon={Database}
          title="Couldn't load data sources"
          description="Something went wrong fetching your data sources. Try again in a moment."
        />
      ) : sources.length === 0 ? (
        <EmptyState
          icon={Database}
          title="No data sources yet"
          description="Add a data source to sync knowledge from a server-side folder. Google Drive, Slack, GitHub, Notion and Confluence can be configured, but their live sync is not built yet."
          actions={
            admin ? (
              <Button onClick={() => setCreateOpen(true)}>
                <Plus className="h-4 w-4" />
                Add data source
              </Button>
            ) : null
          }
        />
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {sources.map((ds) => {
            const syncing = syncSource.isPending && syncSource.variables === ds.id;
            return (
              <Card key={ds.id} className="flex flex-col p-5">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <p className="truncate font-semibold leading-tight">{ds.name}</p>
                    <p className="text-xs text-muted-foreground">
                      {KIND_LABELS[ds.kind] ?? ds.kind}
                      {isLiveKind(ds.kind) ? null : " · sync not built yet"}
                    </p>
                  </div>
                  <Badge
                    variant={STATUS_VARIANT[ds.status]}
                    className="shrink-0 capitalize"
                  >
                    {ds.status}
                  </Badge>
                </div>

                <dl className="mt-3 space-y-1 text-sm">
                  <div className="flex justify-between gap-2">
                    <dt className="text-muted-foreground">Documents</dt>
                    <dd className="font-medium">{formatNumber(ds.document_count)}</dd>
                  </div>
                  <div className="flex justify-between gap-2">
                    <dt className="text-muted-foreground">Last synced</dt>
                    <dd>
                      {ds.last_synced_at ? relativeTime(ds.last_synced_at) : "Never"}
                    </dd>
                  </div>
                  <div className="flex justify-between gap-2">
                    <dt className="text-muted-foreground">Visibility</dt>
                    <dd>{VISIBILITY_LABELS[ds.default_visibility]}</dd>
                  </div>
                </dl>

                {ds.last_error ? (
                  <div className="mt-3 flex items-start gap-2 rounded-md border border-destructive/30 bg-destructive/10 p-2 text-xs text-destructive">
                    <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                    <span className="min-w-0 break-words">{ds.last_error}</span>
                  </div>
                ) : null}

                {admin ? (
                  <div className="mt-auto">
                    <Separator className="my-4" />
                    <div className="flex items-center gap-2">
                      <Button
                        variant="outline"
                        size="sm"
                        className="flex-1"
                        disabled={syncing}
                        onClick={() => syncSource.mutate(ds.id)}
                      >
                        {syncing ? (
                          <Loader2 className="h-4 w-4 animate-spin" />
                        ) : (
                          <RefreshCw className="h-4 w-4" />
                        )}
                        {syncing ? "Syncing…" : "Sync now"}
                      </Button>
                      <Button variant="ghost" size="sm" onClick={() => setManaging(ds)}>
                        <Users className="h-4 w-4" />
                        Manage access
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon"
                        aria-label="Delete data source"
                        className="text-destructive hover:text-destructive"
                        onClick={() => setDeleting(ds)}
                      >
                        <Trash2 className="h-4 w-4" />
                      </Button>
                    </div>
                  </div>
                ) : null}
              </Card>
            );
          })}
        </div>
      )}

      {admin ? (
        <CreateDialog
          open={createOpen}
          onOpenChange={setCreateOpen}
          onCreated={invalidate}
        />
      ) : null}

      {admin ? (
        <ManageAccessDialog
          source={managing}
          open={managing !== null}
          onOpenChange={(o) => !o && setManaging(null)}
        />
      ) : null}

      <Dialog open={deleting !== null} onOpenChange={(o) => !o && setDeleting(null)}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Remove data source?</DialogTitle>
            <DialogDescription>
              <span className="font-medium">{deleting?.name}</span> will stop syncing.
              Documents it already ingested are kept unless you delete them from their
              collection.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setDeleting(null)}
              disabled={deleteSource.isPending}
            >
              Cancel
            </Button>
            <Button
              variant="destructive"
              disabled={deleteSource.isPending}
              onClick={() => deleting && deleteSource.mutate(deleting.id)}
            >
              {deleteSource.isPending ? "Removing…" : "Remove"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function CreateDialog({
  open,
  onOpenChange,
  onCreated,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreated: () => void;
}) {
  const [name, setName] = React.useState("");
  const [kind, setKind] = React.useState<DataSourceKind>("local_folder");
  const [collectionId, setCollectionId] = React.useState("");
  const [visibility, setVisibility] = React.useState<Visibility>("private");
  const [configText, setConfigText] = React.useState("");
  const [secret, setSecret] = React.useState("");
  const [syncInterval, setSyncInterval] = React.useState("");

  const collectionsQuery = useQuery<Collection[]>({
    queryKey: ["collections"],
    queryFn: () => api.get<Collection[]>("/collections"),
    enabled: open,
  });
  const collections = collectionsQuery.data ?? [];

  React.useEffect(() => {
    if (!open) return;
    setName("");
    setKind("local_folder");
    setCollectionId("");
    setVisibility("private");
    setConfigText("");
    setSecret("");
    setSyncInterval("");
  }, [open]);

  const create = useMutation({
    mutationFn: () => {
      const config: Record<string, unknown> = configText.trim()
        ? (JSON.parse(configText) as Record<string, unknown>)
        : {};
      const body: Record<string, unknown> = {
        name: name.trim(),
        kind,
        collection_id: collectionId,
        config,
        default_visibility: visibility,
      };
      if (secret.trim()) body.secret = secret.trim();
      if (syncInterval.trim()) {
        const minutes = Number(syncInterval);
        if (Number.isFinite(minutes)) body.sync_interval_minutes = minutes;
      }
      return api.post<DataSource>("/data-sources", body);
    },
    onSuccess: () => {
      toast.success("Data source created");
      onOpenChange(false);
      onCreated();
    },
    onError: (e) => {
      if (e instanceof SyntaxError) {
        toast.error("Config must be valid JSON");
        return;
      }
      toast.error(errMsg(e, "Couldn't create data source"));
    },
  });

  function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim() || !collectionId) return;
    create.mutate();
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] max-w-lg overflow-y-auto">
        <form onSubmit={submit}>
          <DialogHeader>
            <DialogTitle>Add data source</DialogTitle>
            <DialogDescription>
              Point Third Brain at a source to sync. Secrets are encrypted at rest and
              never returned.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-4">
            <div className="space-y-1.5">
              <Label htmlFor="ds-name">Name</Label>
              <Input
                id="ds-name"
                required
                placeholder="Engineering wiki"
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label htmlFor="ds-kind">Kind</Label>
                <Select value={kind} onValueChange={(v) => setKind(v as DataSourceKind)}>
                  <SelectTrigger id="ds-kind">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectGroup>
                      <SelectLabel>Live</SelectLabel>
                      {KINDS.filter(isLiveKind).map((k) => (
                        <SelectItem key={k} value={k}>
                          {KIND_LABELS[k]}
                        </SelectItem>
                      ))}
                    </SelectGroup>
                    <SelectGroup>
                      <SelectLabel>Not syncing yet</SelectLabel>
                      {KINDS.filter((k) => !isLiveKind(k)).map((k) => (
                        <SelectItem key={k} value={k}>
                          {KIND_LABELS[k]}
                        </SelectItem>
                      ))}
                    </SelectGroup>
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="ds-visibility">Default visibility</Label>
                <Select
                  value={visibility}
                  onValueChange={(v) => setVisibility(v as Visibility)}
                >
                  <SelectTrigger id="ds-visibility">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {VISIBILITIES.map((v) => (
                      <SelectItem key={v} value={v}>
                        {VISIBILITY_LABELS[v]}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>

            {isLiveKind(kind) ? null : (
              <p className="rounded-md border border-border bg-muted/40 p-2.5 text-xs text-muted-foreground">
                {KIND_LABELS[kind]} sync is not built yet. The source can be created and
                configured now - its config and secret are validated and stored - but a
                sync will report the connector as unavailable.
              </p>
            )}

            <div className="space-y-1.5">
              <Label htmlFor="ds-collection">Target collection</Label>
              <Select value={collectionId} onValueChange={setCollectionId}>
                <SelectTrigger id="ds-collection">
                  <SelectValue
                    placeholder={
                      collectionsQuery.isLoading
                        ? "Loading collections…"
                        : "Select a collection"
                    }
                  />
                </SelectTrigger>
                <SelectContent>
                  {collections.map((c) => (
                    <SelectItem key={c.id} value={c.id}>
                      {c.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {!collectionsQuery.isLoading && collections.length === 0 ? (
                <p className="text-xs text-muted-foreground">
                  Create a collection first to give synced documents a home.
                </p>
              ) : null}
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="ds-config">Config (JSON)</Label>
              <Textarea
                id="ds-config"
                className="font-mono text-xs"
                placeholder={CONFIG_PLACEHOLDER[kind]}
                value={configText}
                onChange={(e) => setConfigText(e.target.value)}
              />
              <p className="text-xs text-muted-foreground">
                Connection settings for this {KIND_LABELS[kind]} source.
              </p>
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="ds-secret">Secret (optional)</Label>
              <Input
                id="ds-secret"
                type="password"
                autoComplete="off"
                placeholder="API token or access key"
                value={secret}
                onChange={(e) => setSecret(e.target.value)}
              />
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="ds-interval">Sync interval (minutes)</Label>
              <Input
                id="ds-interval"
                type="number"
                min={1}
                placeholder="Leave blank for manual sync only"
                value={syncInterval}
                onChange={(e) => setSyncInterval(e.target.value)}
              />
            </div>
          </div>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => onOpenChange(false)}
              disabled={create.isPending}
            >
              Cancel
            </Button>
            <Button
              type="submit"
              disabled={create.isPending || !name.trim() || !collectionId}
              className={cn(create.isPending && "opacity-80")}
            >
              {create.isPending ? "Creating…" : "Create data source"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function ManageAccessDialog({
  source,
  open,
  onOpenChange,
}: {
  source: DataSource | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const queryClient = useQueryClient();
  const principalsKey = ["data-source-principals", source?.id];

  const principalsQuery = useQuery<ExternalPrincipalItem[]>({
    queryKey: principalsKey,
    queryFn: () =>
      api.get<ExternalPrincipalItem[]>(`/data-sources/${source!.id}/principals`),
    enabled: open && source !== null,
  });
  const membersQuery = useQuery<Membership[]>({
    queryKey: ["org-members"],
    queryFn: () => api.get<Membership[]>("/orgs/members"),
    enabled: open,
  });
  const teamsQuery = useQuery<Team[]>({
    queryKey: ["teams"],
    queryFn: () => api.get<Team[]>("/teams"),
    enabled: open,
  });

  const [targets, setTargets] = React.useState<Record<string, string>>({});

  React.useEffect(() => {
    if (open) setTargets({});
  }, [open, source?.id]);

  const mapIdentity = useMutation({
    mutationFn: (p: ExternalPrincipalItem) => {
      const target = targets[`${p.provider}:${p.external_id}`];
      const body: Record<string, unknown> = {
        provider: p.provider,
        external_id: p.external_id,
        kind: p.kind,
      };
      if (p.kind === "group") body.team_id = target;
      else body.user_id = target;
      return api.post<Identity>("/data-sources/identities", body);
    },
    onSuccess: () => {
      toast.success("Principal mapped");
      void queryClient.invalidateQueries({ queryKey: principalsKey });
    },
    onError: (e) => toast.error(errMsg(e, "Couldn't map principal")),
  });

  const principals = principalsQuery.data ?? [];

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] max-w-lg overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Manage access</DialogTitle>
          <DialogDescription>
            External principals seen in{" "}
            <span className="font-medium">{source?.name}</span>. Map each one to a Third
            Brain user or team so its source-side permissions are enforced here.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3 py-2">
          {principalsQuery.isLoading ? (
            <div className="space-y-2">
              {Array.from({ length: 3 }).map((_, i) => (
                <Skeleton key={i} className="h-16 w-full rounded-md" />
              ))}
            </div>
          ) : principalsQuery.isError ? (
            <EmptyState
              compact
              icon={Users}
              title="Couldn't load principals"
              description="Try running a sync first, or reopen this dialog in a moment."
            />
          ) : principals.length === 0 ? (
            <EmptyState
              compact
              icon={Users}
              title="No principals yet"
              description="Run a sync to discover the users and groups this source shares documents with."
            />
          ) : (
            principals.map((p) => {
              const key = `${p.provider}:${p.external_id}`;
              const mapping =
                mapIdentity.isPending &&
                mapIdentity.variables !== undefined &&
                `${mapIdentity.variables.provider}:${mapIdentity.variables.external_id}` ===
                  key;
              return (
                <div key={key} className="space-y-2 rounded-md border p-3">
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <p className="truncate text-sm font-medium">{p.external_id}</p>
                      <p className="text-xs capitalize text-muted-foreground">
                        {p.provider} · {p.kind} · {formatNumber(p.document_count)} docs
                      </p>
                    </div>
                    <Badge variant={p.mapped ? "success" : "muted"} className="shrink-0">
                      {p.mapped ? "Mapped" : "Unmapped"}
                    </Badge>
                  </div>
                  {!p.mapped ? (
                    <div className="flex items-center gap-2">
                      <Select
                        value={targets[key]}
                        onValueChange={(v) => setTargets((t) => ({ ...t, [key]: v }))}
                      >
                        <SelectTrigger className="h-8 flex-1">
                          <SelectValue
                            placeholder={
                              p.kind === "group" ? "Map to team…" : "Map to user…"
                            }
                          />
                        </SelectTrigger>
                        <SelectContent>
                          {p.kind === "group"
                            ? (teamsQuery.data ?? []).map((tm) => (
                                <SelectItem key={tm.id} value={tm.id}>
                                  {tm.name}
                                </SelectItem>
                              ))
                            : (membersQuery.data ?? []).map((m) => (
                                <SelectItem key={m.user_id} value={m.user_id}>
                                  {m.user?.full_name || m.user?.email || m.user_id}
                                </SelectItem>
                              ))}
                        </SelectContent>
                      </Select>
                      <Button
                        size="sm"
                        disabled={!targets[key] || mapping}
                        onClick={() => mapIdentity.mutate(p)}
                      >
                        {mapping ? "Mapping…" : "Map"}
                      </Button>
                    </div>
                  ) : null}
                </div>
              );
            })
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Close
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
