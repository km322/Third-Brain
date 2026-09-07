"use client";

import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { KeyRound, Lock, MoreHorizontal, Plus, ShieldAlert, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { CopyButton } from "@/components/copy-button";
import { DataTable, type Column } from "@/components/dashboard/data-table";
import { EmptyState } from "@/components/dashboard/empty-state";
import { PageHeader } from "@/components/dashboard/page-header";
import { isOrgAdmin } from "@/components/governance/role-badge";
import { Badge } from "@/components/ui/badge";
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
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import type { ApiKey, Membership } from "@/lib/types";
import { cn, formatDate } from "@/lib/utils";

// Coarse scopes the backend accepts (see app/schemas/api_key.ALLOWED_SCOPES).
const SCOPES: { value: string; label: string; hint: string }[] = [
  { value: "read", label: "read", hint: "Read collections & documents" },
  { value: "write", label: "write", hint: "Create & edit content" },
  { value: "search", label: "search", hint: "Query & retrieve" },
  { value: "ingest", label: "ingest", hint: "Upload & index documents" },
  { value: "manage", label: "manage", hint: "Administer the org" },
  { value: "*", label: "* (all)", hint: "Full access" },
];

const NONE = "__none__";

function errMsg(e: unknown, fallback = "Something went wrong") {
  return e instanceof ApiError ? e.message : fallback;
}

function keyStatus(k: ApiKey): {
  label: string;
  variant: "success" | "warning" | "destructive";
} {
  if (k.revoked) return { label: "Revoked", variant: "destructive" };
  if (k.expires_at && new Date(k.expires_at).getTime() < Date.now())
    return { label: "Expired", variant: "warning" };
  return { label: "Active", variant: "success" };
}

export default function ApiKeysPage() {
  const { role } = useAuth();
  const admin = isOrgAdmin(role);
  const queryClient = useQueryClient();

  const [createOpen, setCreateOpen] = React.useState(false);
  const [secret, setSecret] = React.useState<{ name: string; secret: string } | null>(
    null,
  );
  const [revoking, setRevoking] = React.useState<ApiKey | null>(null);
  const [deleting, setDeleting] = React.useState<ApiKey | null>(null);

  const keysQuery = useQuery<ApiKey[]>({
    queryKey: ["api-keys"],
    queryFn: () => api.get<ApiKey[]>("/api-keys"),
    enabled: admin,
  });

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["api-keys"] });

  const revokeKey = useMutation({
    mutationFn: (id: string) => api.post<ApiKey>(`/api-keys/${id}/revoke`),
    onSuccess: () => {
      toast.success("Key revoked");
      setRevoking(null);
      void invalidate();
    },
    onError: (e) => toast.error(errMsg(e, "Couldn't revoke key")),
  });

  const deleteKey = useMutation({
    mutationFn: (id: string) => api.delete(`/api-keys/${id}`),
    onSuccess: () => {
      toast.success("Key deleted");
      setDeleting(null);
      void invalidate();
    },
    onError: (e) => toast.error(errMsg(e, "Couldn't delete key")),
  });

  if (!admin) {
    return (
      <div className="space-y-6">
        <PageHeader
          title="API Keys"
          description="Programmatic credentials for the Third Brain API."
        />
        <EmptyState
          icon={Lock}
          title="Admin access required"
          description="Only organization owners and admins can view and manage API keys."
        />
      </div>
    );
  }

  const columns: Column<ApiKey>[] = [
    {
      id: "name",
      header: "Name",
      cell: (k) => (
        <div className="min-w-0">
          <p className="truncate text-sm font-medium">{k.name}</p>
          <code className="text-xs text-muted-foreground">{k.key_prefix}…</code>
        </div>
      ),
    },
    {
      id: "scopes",
      header: "Scopes",
      hideOnMobile: true,
      cell: (k) =>
        k.scopes.length ? (
          <div className="flex flex-wrap gap-1">
            {k.scopes.map((s) => (
              <Badge key={s} variant="muted" className="font-mono text-[11px]">
                {s}
              </Badge>
            ))}
          </div>
        ) : (
          <span className="text-xs text-muted-foreground">none</span>
        ),
    },
    {
      id: "rate",
      header: "Rate limit",
      align: "right",
      hideOnMobile: true,
      cell: (k) => (
        <span className="text-sm tabular-nums">
          {k.rate_limit_per_minute}
          <span className="text-muted-foreground">/min</span>
        </span>
      ),
    },
    {
      id: "last_used",
      header: "Last used",
      hideOnMobile: true,
      cell: (k) => (
        <span className="text-sm text-muted-foreground">
          {k.last_used_at ? formatDate(k.last_used_at) : "Never"}
        </span>
      ),
    },
    {
      id: "expires",
      header: "Expires",
      hideOnMobile: true,
      cell: (k) => (
        <span className="text-sm text-muted-foreground">
          {k.expires_at ? formatDate(k.expires_at) : "Never"}
        </span>
      ),
    },
    {
      id: "status",
      header: "Status",
      cell: (k) => {
        const s = keyStatus(k);
        return <Badge variant={s.variant}>{s.label}</Badge>;
      },
    },
    {
      id: "actions",
      header: "",
      align: "right",
      cell: (k) => (
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" size="icon" aria-label="Key actions">
              <MoreHorizontal className="h-4 w-4" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-40">
            <DropdownMenuItem disabled={k.revoked} onClick={() => setRevoking(k)}>
              <ShieldAlert className="h-4 w-4" />
              Revoke
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem
              className="text-destructive focus:text-destructive"
              onClick={() => setDeleting(k)}
            >
              <Trash2 className="h-4 w-4" />
              Delete
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      ),
    },
  ];

  return (
    <div className="space-y-6">
      <PageHeader
        title="API Keys"
        description="Programmatic credentials for the Third Brain API. Secrets are shown once at creation."
        actions={
          <Button onClick={() => setCreateOpen(true)}>
            <Plus className="h-4 w-4" />
            Create key
          </Button>
        }
      />

      <DataTable
        columns={columns}
        data={keysQuery.data ?? []}
        rowKey={(k) => k.id}
        isLoading={keysQuery.isLoading}
        empty={
          <EmptyState
            compact
            icon={KeyRound}
            title="No API keys"
            description="Create a key to call the API or connect an OpenAI-compatible client."
          />
        }
      />

      <CreateKeyDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        onCreated={(name, s) => {
          setSecret({ name, secret: s });
          void invalidate();
        }}
      />

      {/* One-time secret reveal */}
      <Dialog open={secret !== null} onOpenChange={(o) => !o && setSecret(null)}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>Copy your API key</DialogTitle>
            <DialogDescription>
              This is the only time the full secret for{" "}
              <span className="font-medium">{secret?.name}</span> will be shown. Store it
              somewhere safe.
            </DialogDescription>
          </DialogHeader>
          <div className="flex items-center gap-2 rounded-md border bg-muted/40 p-3">
            <code className="min-w-0 flex-1 break-all font-mono text-sm">
              {secret?.secret}
            </code>
            <CopyButton
              value={secret?.secret ?? ""}
              variant="outline"
              toastMessage="API key copied"
            />
          </div>
          <div className="flex items-start gap-2 text-xs text-warning">
            <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" />
            <span>
              We only store a hash of this key - it can&apos;t be recovered later. If you
              lose it, delete the key and create a new one.
            </span>
          </div>
          <DialogFooter>
            <Button onClick={() => setSecret(null)}>Done</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Revoke confirm */}
      <Dialog open={revoking !== null} onOpenChange={(o) => !o && setRevoking(null)}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Revoke key?</DialogTitle>
            <DialogDescription>
              <span className="font-medium">{revoking?.name}</span> will stop working
              immediately. Its history is kept for auditing.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setRevoking(null)}
              disabled={revokeKey.isPending}
            >
              Cancel
            </Button>
            <Button
              variant="destructive"
              disabled={revokeKey.isPending}
              onClick={() => revoking && revokeKey.mutate(revoking.id)}
            >
              {revokeKey.isPending ? "Revoking…" : "Revoke key"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete confirm */}
      <Dialog open={deleting !== null} onOpenChange={(o) => !o && setDeleting(null)}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Delete key?</DialogTitle>
            <DialogDescription>
              Permanently delete <span className="font-medium">{deleting?.name}</span>.
              This cannot be undone.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setDeleting(null)}
              disabled={deleteKey.isPending}
            >
              Cancel
            </Button>
            <Button
              variant="destructive"
              disabled={deleteKey.isPending}
              onClick={() => deleting && deleteKey.mutate(deleting.id)}
            >
              {deleteKey.isPending ? "Deleting…" : "Delete key"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function CreateKeyDialog({
  open,
  onOpenChange,
  onCreated,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreated: (name: string, secret: string) => void;
}) {
  const [name, setName] = React.useState("");
  const [scopes, setScopes] = React.useState<string[]>(["read", "search"]);
  const [expiresAt, setExpiresAt] = React.useState("");
  const [rateLimit, setRateLimit] = React.useState("");
  const [actsAs, setActsAs] = React.useState<string>(NONE);

  React.useEffect(() => {
    if (open) {
      setName("");
      setScopes(["read", "search"]);
      setExpiresAt("");
      setRateLimit("");
      setActsAs(NONE);
    }
  }, [open]);

  // For the optional "acts as user" impersonation binding.
  const membersQuery = useQuery<Membership[]>({
    queryKey: ["org-members"],
    queryFn: () => api.get<Membership[]>("/orgs/members"),
    enabled: open,
  });

  function toggleScope(scope: string) {
    setScopes((prev) =>
      prev.includes(scope) ? prev.filter((s) => s !== scope) : [...prev, scope],
    );
  }

  const create = useMutation({
    mutationFn: () => {
      const body: Record<string, unknown> = {
        name: name.trim(),
        scopes,
      };
      if (expiresAt) body.expires_at = new Date(expiresAt).toISOString();
      if (rateLimit.trim()) body.rate_limit_per_minute = Number(rateLimit);
      if (actsAs !== NONE) body.acts_as_user_id = actsAs;
      return api.post<{ api_key: ApiKey; secret: string }>("/api-keys", body);
    },
    onSuccess: (res) => {
      onOpenChange(false);
      onCreated(res.api_key.name, res.secret);
    },
    onError: (e) => toast.error(errMsg(e, "Couldn't create key")),
  });

  function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim() || scopes.length === 0) return;
    create.mutate();
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] max-w-lg overflow-y-auto">
        <form onSubmit={submit}>
          <DialogHeader>
            <DialogTitle>Create API key</DialogTitle>
            <DialogDescription>
              Scope the key to only what it needs. The secret is shown once.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-4">
            <div className="space-y-1.5">
              <Label htmlFor="key-name">Name</Label>
              <Input
                id="key-name"
                required
                placeholder="Production ingestion"
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </div>

            <div className="space-y-1.5">
              <Label>Scopes</Label>
              <div className="grid grid-cols-2 gap-2">
                {SCOPES.map((s) => {
                  const active = scopes.includes(s.value);
                  return (
                    <button
                      type="button"
                      key={s.value}
                      onClick={() => toggleScope(s.value)}
                      className={cn(
                        "flex flex-col items-start rounded-md border px-3 py-2 text-left transition-colors",
                        active ? "border-primary bg-primary/10" : "hover:bg-accent",
                      )}
                    >
                      <span className="font-mono text-sm font-medium">{s.label}</span>
                      <span className="text-xs text-muted-foreground">{s.hint}</span>
                    </button>
                  );
                })}
              </div>
              {scopes.length === 0 ? (
                <p className="text-xs text-destructive">Select at least one scope.</p>
              ) : null}
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label htmlFor="key-expires">Expires</Label>
                <Input
                  id="key-expires"
                  type="datetime-local"
                  value={expiresAt}
                  onChange={(e) => setExpiresAt(e.target.value)}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="key-rate">Rate limit / min</Label>
                <Input
                  id="key-rate"
                  type="number"
                  min={1}
                  placeholder="120"
                  value={rateLimit}
                  onChange={(e) => setRateLimit(e.target.value)}
                />
              </div>
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="key-acts-as">Acts as user (optional)</Label>
              <Select value={actsAs} onValueChange={setActsAs}>
                <SelectTrigger id="key-acts-as">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={NONE}>No impersonation</SelectItem>
                  {(membersQuery.data ?? []).map((m) => (
                    <SelectItem key={m.user_id} value={m.user_id}>
                      {m.user?.full_name || m.user?.email || m.user_id}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <p className="text-xs text-muted-foreground">
                Resolve permissions as if this member made each request.
              </p>
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
              disabled={create.isPending || !name.trim() || scopes.length === 0}
            >
              {create.isPending ? "Creating…" : "Create key"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
