"use client";

import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Pencil, Plug, Plus, Trash2, Zap } from "lucide-react";
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
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import type { ConnectorPurpose, ConnectorType } from "@/lib/types";
import { cn } from "@/lib/utils";

// The connectors API returns extra fields beyond the shared `Connector` type
// (config, has_credentials, updated_at). Model them locally for this page.
interface ConnectorRow {
  id: string;
  name: string;
  type: ConnectorType;
  purpose: ConnectorPurpose;
  model: string;
  config: Record<string, unknown>;
  is_default: boolean;
  enabled: boolean;
  has_credentials: boolean;
  created_at: string;
  updated_at: string;
}

const TYPES: ConnectorType[] = [
  "openai",
  "azure_openai",
  "ollama",
  "custom",
  "anthropic",
  "google",
];
const PURPOSES: ConnectorPurpose[] = ["embedding", "completion"];

const TYPE_LABELS: Record<ConnectorType, string> = {
  openai: "OpenAI",
  azure_openai: "Azure OpenAI",
  ollama: "Ollama",
  custom: "Custom (OpenAI-compatible)",
  anthropic: "Anthropic",
  google: "Google Gemini",
};

// Per-provider model hints for the form's model input.
function modelPlaceholder(type: ConnectorType, purpose: ConnectorPurpose): string {
  if (type === "anthropic") return "claude-opus-5";
  if (type === "google")
    return purpose === "embedding" ? "gemini-embedding-001" : "gemini-2.5-flash";
  return purpose === "embedding" ? "text-embedding-3-small" : "gpt-4o-mini";
}

const PURPOSE_VARIANT: Record<ConnectorPurpose, "info" | "success"> = {
  embedding: "info",
  completion: "success",
};

function errMsg(e: unknown, fallback = "Something went wrong") {
  return e instanceof ApiError ? e.message : fallback;
}

export default function ConnectorsPage() {
  const { role } = useAuth();
  const admin = isOrgAdmin(role);
  const queryClient = useQueryClient();

  const [formOpen, setFormOpen] = React.useState(false);
  const [editing, setEditing] = React.useState<ConnectorRow | null>(null);
  const [deleting, setDeleting] = React.useState<ConnectorRow | null>(null);

  const connectorsQuery = useQuery<ConnectorRow[]>({
    queryKey: ["connectors"],
    queryFn: () => api.get<ConnectorRow[]>("/connectors"),
  });

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["connectors"] });

  const patchConnector = useMutation({
    mutationFn: ({ id, body }: { id: string; body: Record<string, unknown> }) =>
      api.patch<ConnectorRow>(`/connectors/${id}`, body),
    onSuccess: () => void invalidate(),
    onError: (e) => toast.error(errMsg(e, "Couldn't update connector")),
  });

  const deleteConnector = useMutation({
    mutationFn: (id: string) => api.delete(`/connectors/${id}`),
    onSuccess: () => {
      toast.success("Connector deleted");
      setDeleting(null);
      void invalidate();
    },
    onError: (e) => toast.error(errMsg(e, "Couldn't delete connector")),
  });

  const testConnector = useMutation({
    mutationFn: (id: string) =>
      api.post<{ ok: boolean; message: string; latency_ms?: number }>(
        `/connectors/${id}/test`,
      ),
    onSuccess: (res) => {
      const suffix = res.latency_ms != null ? ` (${res.latency_ms} ms)` : "";
      if (res.ok) toast.success(res.message + suffix);
      else toast.error(res.message + suffix);
    },
    onError: (e) => toast.error(errMsg(e, "Test failed")),
  });

  const connectors = connectorsQuery.data ?? [];

  function openCreate() {
    setEditing(null);
    setFormOpen(true);
  }
  function openEdit(c: ConnectorRow) {
    setEditing(c);
    setFormOpen(true);
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Connectors"
        description="Route embeddings and completions through your own OpenAI-compatible, Anthropic, or Google Gemini account and keys."
        actions={
          admin ? (
            <Button onClick={openCreate}>
              <Plus className="h-4 w-4" />
              Add connector
            </Button>
          ) : null
        }
      />

      {connectorsQuery.isLoading ? (
        <CardGridSkeleton count={3} />
      ) : connectorsQuery.isError ? (
        <EmptyState
          icon={Plug}
          title="Couldn't load connectors"
          description="Something went wrong fetching your connectors. Try again in a moment."
        />
      ) : connectors.length === 0 ? (
        <EmptyState
          icon={Plug}
          title="No connectors configured"
          description="Add a provider to route embeddings and completions through your own account and keys."
          actions={
            admin ? (
              <Button onClick={openCreate}>
                <Plus className="h-4 w-4" />
                Add connector
              </Button>
            ) : null
          }
        />
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {connectors.map((c) => {
            const testing = testConnector.isPending && testConnector.variables === c.id;
            return (
              <Card key={c.id} className="flex flex-col p-5">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <p className="truncate font-semibold leading-tight">{c.name}</p>
                    <p className="text-xs text-muted-foreground">
                      {TYPE_LABELS[c.type] ?? c.type}
                    </p>
                  </div>
                  <div className="flex shrink-0 flex-col items-end gap-1">
                    <Badge variant={PURPOSE_VARIANT[c.purpose]}>{c.purpose}</Badge>
                    {c.is_default ? (
                      <Badge variant="muted" className="text-[10px]">
                        Default
                      </Badge>
                    ) : null}
                  </div>
                </div>

                <dl className="mt-3 space-y-1 text-sm">
                  <div className="flex justify-between gap-2">
                    <dt className="text-muted-foreground">Model</dt>
                    <dd className="truncate font-mono text-xs">{c.model}</dd>
                  </div>
                  <div className="flex justify-between gap-2">
                    <dt className="text-muted-foreground">Credentials</dt>
                    <dd>
                      {c.has_credentials ? (
                        <span className="text-success">Stored</span>
                      ) : (
                        <span className="text-muted-foreground">None</span>
                      )}
                    </dd>
                  </div>
                </dl>

                <Separator className="my-4" />

                <div className="mt-auto space-y-3">
                  <div className="flex items-center justify-between">
                    <Label
                      htmlFor={`enabled-${c.id}`}
                      className="text-sm text-muted-foreground"
                    >
                      Enabled
                    </Label>
                    <Switch
                      id={`enabled-${c.id}`}
                      checked={c.enabled}
                      disabled={!admin || patchConnector.isPending}
                      onCheckedChange={(v) =>
                        patchConnector.mutate({
                          id: c.id,
                          body: { enabled: v },
                        })
                      }
                    />
                  </div>

                  {/* All actions here (including Test, which spends metered tokens) are
                      admin-only on the backend, so only render them for admins. */}
                  {admin ? (
                    <div className="flex items-center gap-2">
                      <Button
                        variant="outline"
                        size="sm"
                        className="flex-1"
                        disabled={testing}
                        onClick={() => testConnector.mutate(c.id)}
                      >
                        <Zap className="h-4 w-4" />
                        {testing ? "Testing…" : "Test"}
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        disabled={!c.enabled || c.is_default || patchConnector.isPending}
                        onClick={() =>
                          patchConnector.mutate({
                            id: c.id,
                            body: { is_default: true },
                          })
                        }
                      >
                        Set default
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon"
                        aria-label="Edit connector"
                        onClick={() => openEdit(c)}
                      >
                        <Pencil className="h-4 w-4" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon"
                        aria-label="Delete connector"
                        className="text-destructive hover:text-destructive"
                        onClick={() => setDeleting(c)}
                      >
                        <Trash2 className="h-4 w-4" />
                      </Button>
                    </div>
                  ) : null}
                </div>
              </Card>
            );
          })}
        </div>
      )}

      {admin ? (
        <ConnectorFormDialog
          open={formOpen}
          onOpenChange={setFormOpen}
          connector={editing ?? undefined}
          onSaved={invalidate}
        />
      ) : null}

      <Dialog open={deleting !== null} onOpenChange={(o) => !o && setDeleting(null)}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Delete connector?</DialogTitle>
            <DialogDescription>
              <span className="font-medium">{deleting?.name}</span> will be removed. If it
              was the default for its purpose, another enabled connector is promoted
              automatically.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setDeleting(null)}
              disabled={deleteConnector.isPending}
            >
              Cancel
            </Button>
            <Button
              variant="destructive"
              disabled={deleteConnector.isPending}
              onClick={() => deleting && deleteConnector.mutate(deleting.id)}
            >
              {deleteConnector.isPending ? "Deleting…" : "Delete"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function ConnectorFormDialog({
  open,
  onOpenChange,
  connector,
  onSaved,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  connector?: ConnectorRow;
  onSaved: () => void;
}) {
  const editing = Boolean(connector);
  const [name, setName] = React.useState("");
  const [type, setType] = React.useState<ConnectorType>("openai");
  const [purpose, setPurpose] = React.useState<ConnectorPurpose>("completion");
  const [model, setModel] = React.useState("");
  const [configText, setConfigText] = React.useState("");
  const [apiKey, setApiKey] = React.useState("");
  const [credsTouched, setCredsTouched] = React.useState(false);
  const [isDefault, setIsDefault] = React.useState(false);
  const [enabled, setEnabled] = React.useState(true);

  React.useEffect(() => {
    if (!open) return;
    setName(connector?.name ?? "");
    setType(connector?.type ?? "openai");
    setPurpose(connector?.purpose ?? "completion");
    setModel(connector?.model ?? "");
    setConfigText(
      connector && Object.keys(connector.config ?? {}).length
        ? JSON.stringify(connector.config, null, 2)
        : "",
    );
    setApiKey("");
    setCredsTouched(false);
    setIsDefault(connector?.is_default ?? false);
    setEnabled(connector?.enabled ?? true);
  }, [open, connector]);

  const save = useMutation({
    mutationFn: () => {
      let config: Record<string, unknown> = {};
      if (configText.trim()) {
        config = JSON.parse(configText) as Record<string, unknown>;
      }
      const body: Record<string, unknown> = {
        name: name.trim(),
        type,
        purpose,
        model: model.trim(),
        config,
        is_default: isDefault,
        enabled,
      };
      // Only send credentials when the user actually touched the field, so an
      // edit that leaves it blank keeps the stored secret intact.
      if (!editing) {
        if (apiKey.trim()) body.credentials = { api_key: apiKey.trim() };
      } else if (credsTouched) {
        body.credentials = apiKey.trim() ? { api_key: apiKey.trim() } : {};
      }
      return editing
        ? api.patch<ConnectorRow>(`/connectors/${connector!.id}`, body)
        : api.post<ConnectorRow>("/connectors", body);
    },
    onSuccess: () => {
      toast.success(editing ? "Connector updated" : "Connector created");
      onOpenChange(false);
      onSaved();
    },
    onError: (e) => {
      if (e instanceof SyntaxError) {
        toast.error("Config must be valid JSON");
        return;
      }
      toast.error(errMsg(e, "Couldn't save connector"));
    },
  });

  function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim() || !model.trim()) return;
    save.mutate();
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] max-w-lg overflow-y-auto">
        <form onSubmit={submit}>
          <DialogHeader>
            <DialogTitle>{editing ? "Edit connector" : "Add connector"}</DialogTitle>
            <DialogDescription>
              Credentials are encrypted at rest and never returned.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-4">
            <div className="space-y-1.5">
              <Label htmlFor="c-name">Name</Label>
              <Input
                id="c-name"
                required
                placeholder="OpenAI production"
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label htmlFor="c-type">Provider</Label>
                <Select value={type} onValueChange={(v) => setType(v as ConnectorType)}>
                  <SelectTrigger id="c-type">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {TYPES.map((t) => (
                      <SelectItem
                        key={t}
                        value={t}
                        // Anthropic has no embeddings API.
                        disabled={t === "anthropic" && purpose === "embedding"}
                      >
                        {TYPE_LABELS[t]}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="c-purpose">Purpose</Label>
                <Select
                  value={purpose}
                  onValueChange={(v) => setPurpose(v as ConnectorPurpose)}
                >
                  <SelectTrigger id="c-purpose" className="capitalize">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {PURPOSES.map((p) => (
                      <SelectItem
                        key={p}
                        value={p}
                        className="capitalize"
                        // Anthropic has no embeddings API.
                        disabled={p === "embedding" && type === "anthropic"}
                      >
                        {p}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="c-model">Model</Label>
              <Input
                id="c-model"
                required
                placeholder={modelPlaceholder(type, purpose)}
                value={model}
                onChange={(e) => setModel(e.target.value)}
              />
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="c-key">API key / secret</Label>
              <Input
                id="c-key"
                type="password"
                autoComplete="off"
                placeholder={
                  editing && connector?.has_credentials
                    ? "•••••••• (leave blank to keep)"
                    : "sk-…"
                }
                value={apiKey}
                onChange={(e) => {
                  setApiKey(e.target.value);
                  setCredsTouched(true);
                }}
              />
              {editing && connector?.has_credentials ? (
                <button
                  type="button"
                  className="text-xs text-destructive hover:underline"
                  onClick={() => {
                    setApiKey("");
                    setCredsTouched(true);
                  }}
                >
                  Remove stored credential
                </button>
              ) : null}
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="c-config">Config (JSON, non-secret)</Label>
              <Textarea
                id="c-config"
                className="font-mono text-xs"
                placeholder={
                  '{\n  "base_url": "https://…",\n  "api_version": "2024-02-01"\n}'
                }
                value={configText}
                onChange={(e) => setConfigText(e.target.value)}
              />
              <p className="text-xs text-muted-foreground">
                Provider options like base_url, api_version or region.
              </p>
            </div>

            <div className="flex items-center justify-between rounded-md border p-3">
              <div>
                <p className="text-sm font-medium">Default for this purpose</p>
                <p className="text-xs text-muted-foreground">
                  Used automatically for {purpose} requests.
                </p>
              </div>
              <Switch checked={isDefault} onCheckedChange={setIsDefault} />
            </div>
            <div className="flex items-center justify-between rounded-md border p-3">
              <div>
                <p className="text-sm font-medium">Enabled</p>
                <p className="text-xs text-muted-foreground">
                  Disabled connectors are never used.
                </p>
              </div>
              <Switch checked={enabled} onCheckedChange={setEnabled} />
            </div>
          </div>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => onOpenChange(false)}
              disabled={save.isPending}
            >
              Cancel
            </Button>
            <Button
              type="submit"
              disabled={save.isPending || !name.trim() || !model.trim()}
              className={cn(save.isPending && "opacity-80")}
            >
              {save.isPending ? "Saving…" : editing ? "Save changes" : "Create connector"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
