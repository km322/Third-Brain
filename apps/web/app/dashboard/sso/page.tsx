"use client";

import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { KeyRound, Lock, Plus, ShieldAlert, ShieldCheck, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { CopyButton } from "@/components/copy-button";
import { EmptyState } from "@/components/dashboard/empty-state";
import { CardGridSkeleton, TableSkeleton } from "@/components/dashboard/loading";
import { PageHeader } from "@/components/dashboard/page-header";
import { isOrgAdmin } from "@/components/governance/role-badge";
import { Badge, type BadgeProps } from "@/components/ui/badge";
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
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import type {
  OrgRole,
  ScimToken,
  ScimTokenCreated,
  SsoConnection,
  SsoProtocol,
} from "@/lib/types";
import { formatDate } from "@/lib/utils";

const PROTOCOLS: SsoProtocol[] = ["oidc", "saml"];
const ROLES: OrgRole[] = ["viewer", "editor", "admin", "owner"];

const PROTOCOL_LABELS: Record<SsoProtocol, string> = {
  oidc: "OIDC",
  saml: "SAML",
};

const PROTOCOL_VARIANT: Record<SsoProtocol, BadgeProps["variant"]> = {
  oidc: "info",
  saml: "success",
};

// `redirect_uri` is the dashboard's own callback page, so it defaults to
// `<APP_BASE_URL>/sso/callback` on the server when the config omits it.
const CONFIG_PLACEHOLDER: Record<SsoProtocol, string> = {
  oidc: `{
  "authorization_endpoint": "https://idp.example.com/authorize",
  "token_endpoint": "https://idp.example.com/token",
  "client_id": "your-client-id",
  "redirect_uri": "https://your-domain.example/sso/callback"
}`,
  saml: `{
  "idp_sso_url": "https://idp.example.com/sso",
  "idp_x509_cert": "-----BEGIN CERTIFICATE-----\\n…\\n-----END CERTIFICATE-----"
}`,
};

const errMsg = (e: unknown, f = "Something went wrong") =>
  e instanceof ApiError ? e.message : f;

export default function SsoPage() {
  const { role } = useAuth();
  const admin = isOrgAdmin(role);
  const queryClient = useQueryClient();

  const [connFormOpen, setConnFormOpen] = React.useState(false);
  const [deletingConn, setDeletingConn] = React.useState<SsoConnection | null>(null);
  const [tokenFormOpen, setTokenFormOpen] = React.useState(false);
  const [revealToken, setRevealToken] = React.useState<ScimTokenCreated | null>(null);
  const [revokingToken, setRevokingToken] = React.useState<ScimToken | null>(null);

  const connectionsQuery = useQuery<SsoConnection[]>({
    queryKey: ["sso-connections"],
    queryFn: () => api.get<SsoConnection[]>("/sso-connections"),
    enabled: admin,
  });

  const tokensQuery = useQuery<ScimToken[]>({
    queryKey: ["scim-tokens"],
    queryFn: () => api.get<ScimToken[]>("/scim-tokens"),
    enabled: admin,
  });

  const invalidateConnections = () =>
    queryClient.invalidateQueries({ queryKey: ["sso-connections"] });
  const invalidateTokens = () =>
    queryClient.invalidateQueries({ queryKey: ["scim-tokens"] });

  const patchConnection = useMutation({
    mutationFn: ({ id, body }: { id: string; body: Record<string, unknown> }) =>
      api.patch<SsoConnection>(`/sso-connections/${id}`, body),
    onSuccess: () => void invalidateConnections(),
    onError: (e) => toast.error(errMsg(e, "Couldn't update connection")),
  });

  const deleteConnection = useMutation({
    mutationFn: (id: string) => api.delete(`/sso-connections/${id}`),
    onSuccess: () => {
      toast.success("Connection deleted");
      setDeletingConn(null);
      void invalidateConnections();
    },
    onError: (e) => toast.error(errMsg(e, "Couldn't delete connection")),
  });

  const revokeToken = useMutation({
    mutationFn: (id: string) => api.delete(`/scim-tokens/${id}`),
    onSuccess: () => {
      toast.success("Token revoked");
      setRevokingToken(null);
      void invalidateTokens();
    },
    onError: (e) => toast.error(errMsg(e, "Couldn't revoke token")),
  });

  if (!admin) {
    return (
      <div className="space-y-6">
        <PageHeader
          title="SSO & SCIM"
          description="Single sign-on and directory provisioning for your organization."
        />
        <EmptyState
          icon={Lock}
          title="Admin access required"
          description="Only organization owners and admins can configure single sign-on and SCIM provisioning."
        />
      </div>
    );
  }

  const connections = connectionsQuery.data ?? [];
  const tokens = tokensQuery.data ?? [];

  return (
    <div className="space-y-6">
      <PageHeader
        title="SSO & SCIM"
        description="Let your team sign in with your identity provider and keep membership in sync automatically."
      />

      {/* Section 1: Single sign-on */}
      <section className="space-y-4">
        <div className="flex flex-col justify-between gap-3 sm:flex-row sm:items-start">
          <div className="min-w-0 space-y-1">
            <h2 className="text-lg font-semibold tracking-tight">Single sign-on</h2>
            <p className="max-w-2xl text-sm text-muted-foreground">
              Connect an OIDC or SAML identity provider. Users matching a
              connection&apos;s email domain are signed in through it.
            </p>
          </div>
          <Button className="shrink-0" onClick={() => setConnFormOpen(true)}>
            <Plus className="h-4 w-4" />
            Add connection
          </Button>
        </div>

        {connectionsQuery.isLoading ? (
          <CardGridSkeleton count={3} />
        ) : connectionsQuery.isError ? (
          <EmptyState
            icon={ShieldCheck}
            title="Couldn't load connections"
            description="Something went wrong fetching your SSO connections. Try again in a moment."
          />
        ) : connections.length === 0 ? (
          <EmptyState
            icon={ShieldCheck}
            title="No SSO connections"
            description="Add an identity provider so your team can sign in with their existing corporate credentials."
            actions={
              <Button onClick={() => setConnFormOpen(true)}>
                <Plus className="h-4 w-4" />
                Add connection
              </Button>
            }
          />
        ) : (
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {connections.map((c) => (
              <Card key={c.id} className="flex flex-col p-5">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <p className="truncate font-semibold leading-tight">{c.name}</p>
                    <p className="text-xs text-muted-foreground">
                      {c.email_domain ? c.email_domain : "Any email domain"}
                    </p>
                  </div>
                  <Badge variant={PROTOCOL_VARIANT[c.protocol]}>
                    {PROTOCOL_LABELS[c.protocol]}
                  </Badge>
                </div>

                <dl className="mt-3 space-y-1 text-sm">
                  <div className="flex justify-between gap-2">
                    <dt className="text-muted-foreground">Default role</dt>
                    <dd className="capitalize">{c.default_role}</dd>
                  </div>
                  <div className="flex justify-between gap-2">
                    <dt className="text-muted-foreground">Client secret</dt>
                    <dd>
                      {c.has_secret ? (
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
                      htmlFor={`sso-enabled-${c.id}`}
                      className="text-sm text-muted-foreground"
                    >
                      Enabled
                    </Label>
                    <Switch
                      id={`sso-enabled-${c.id}`}
                      checked={c.enabled}
                      disabled={patchConnection.isPending}
                      onCheckedChange={(v) =>
                        patchConnection.mutate({ id: c.id, body: { enabled: v } })
                      }
                    />
                  </div>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="w-full text-destructive hover:text-destructive"
                    onClick={() => setDeletingConn(c)}
                  >
                    <Trash2 className="h-4 w-4" />
                    Delete connection
                  </Button>
                </div>
              </Card>
            ))}
          </div>
        )}
      </section>

      <Separator />

      {/* Section 2: SCIM provisioning tokens */}
      <section className="space-y-4">
        <div className="flex flex-col justify-between gap-3 sm:flex-row sm:items-start">
          <div className="min-w-0 space-y-1">
            <h2 className="text-lg font-semibold tracking-tight">
              SCIM provisioning tokens
            </h2>
            <p className="max-w-2xl text-sm text-muted-foreground">
              Bearer tokens for your identity provider to create, update and deactivate
              members via SCIM. Shown once at creation.
            </p>
          </div>
          <Button className="shrink-0" onClick={() => setTokenFormOpen(true)}>
            <Plus className="h-4 w-4" />
            Create token
          </Button>
        </div>

        {tokensQuery.isLoading ? (
          <TableSkeleton rows={3} columns={4} />
        ) : tokensQuery.isError ? (
          <EmptyState
            icon={KeyRound}
            title="Couldn't load tokens"
            description="Something went wrong fetching your SCIM tokens. Try again in a moment."
          />
        ) : tokens.length === 0 ? (
          <EmptyState
            compact
            icon={KeyRound}
            title="No SCIM tokens"
            description="Create a token and paste it into your identity provider to enable automatic user provisioning."
          />
        ) : (
          <Card className="overflow-hidden">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Name</TableHead>
                  <TableHead>Prefix</TableHead>
                  <TableHead>Last used</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {tokens.map((t) => (
                  <TableRow key={t.id}>
                    <TableCell>
                      <div className="flex items-center gap-2">
                        <span className="font-medium">{t.name}</span>
                        {t.revoked ? <Badge variant="destructive">Revoked</Badge> : null}
                      </div>
                    </TableCell>
                    <TableCell>
                      <code className="font-mono text-xs text-muted-foreground">
                        {t.token_prefix}…
                      </code>
                    </TableCell>
                    <TableCell className="text-sm text-muted-foreground">
                      {t.last_used_at ? formatDate(t.last_used_at) : "Never"}
                    </TableCell>
                    <TableCell className="text-right">
                      {t.revoked ? (
                        <span className="text-xs text-muted-foreground">—</span>
                      ) : (
                        <Button
                          variant="ghost"
                          size="sm"
                          className="text-destructive hover:text-destructive"
                          onClick={() => setRevokingToken(t)}
                        >
                          <ShieldAlert className="h-4 w-4" />
                          Revoke
                        </Button>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </Card>
        )}
      </section>

      <SsoConnectionDialog
        open={connFormOpen}
        onOpenChange={setConnFormOpen}
        onSaved={invalidateConnections}
      />

      <CreateScimTokenDialog
        open={tokenFormOpen}
        onOpenChange={setTokenFormOpen}
        onCreated={(token) => {
          setRevealToken(token);
          void invalidateTokens();
        }}
      />

      {/* One-time SCIM token reveal */}
      <Dialog
        open={revealToken !== null}
        onOpenChange={(o) => !o && setRevealToken(null)}
      >
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>Copy your SCIM token</DialogTitle>
            <DialogDescription>
              This is the only time the token for{" "}
              <span className="font-medium">{revealToken?.name}</span> will be shown.
              Paste it into your identity provider now.
            </DialogDescription>
          </DialogHeader>
          <div className="flex items-center gap-2 rounded-md border bg-muted/40 p-3">
            <code className="min-w-0 flex-1 break-all font-mono text-sm">
              {revealToken?.token}
            </code>
            <CopyButton
              value={revealToken?.token ?? ""}
              variant="outline"
              toastMessage="Token copied"
            />
          </div>
          <div className="flex items-start gap-2 text-xs text-warning">
            <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" />
            <span>
              We only store a hash of this token - it can&apos;t be recovered later. If
              you lose it, revoke it and create a new one.
            </span>
          </div>
          <DialogFooter>
            <Button onClick={() => setRevealToken(null)}>Done</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Delete connection confirm */}
      <Dialog
        open={deletingConn !== null}
        onOpenChange={(o) => !o && setDeletingConn(null)}
      >
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Delete connection?</DialogTitle>
            <DialogDescription>
              <span className="font-medium">{deletingConn?.name}</span> will be removed
              and members will no longer be able to sign in through it.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setDeletingConn(null)}
              disabled={deleteConnection.isPending}
            >
              Cancel
            </Button>
            <Button
              variant="destructive"
              disabled={deleteConnection.isPending}
              onClick={() => deletingConn && deleteConnection.mutate(deletingConn.id)}
            >
              {deleteConnection.isPending ? "Deleting…" : "Delete"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Revoke token confirm */}
      <Dialog
        open={revokingToken !== null}
        onOpenChange={(o) => !o && setRevokingToken(null)}
      >
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Revoke token?</DialogTitle>
            <DialogDescription>
              <span className="font-medium">{revokingToken?.name}</span> will stop working
              immediately and any provisioning that relies on it will fail.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setRevokingToken(null)}
              disabled={revokeToken.isPending}
            >
              Cancel
            </Button>
            <Button
              variant="destructive"
              disabled={revokeToken.isPending}
              onClick={() => revokingToken && revokeToken.mutate(revokingToken.id)}
            >
              {revokeToken.isPending ? "Revoking…" : "Revoke token"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function SsoConnectionDialog({
  open,
  onOpenChange,
  onSaved,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSaved: () => void;
}) {
  const [protocol, setProtocol] = React.useState<SsoProtocol>("oidc");
  const [name, setName] = React.useState("");
  const [emailDomain, setEmailDomain] = React.useState("");
  const [configText, setConfigText] = React.useState("");
  const [clientSecret, setClientSecret] = React.useState("");
  const [defaultRole, setDefaultRole] = React.useState<OrgRole>("viewer");
  const [enabled, setEnabled] = React.useState(true);

  React.useEffect(() => {
    if (!open) return;
    setProtocol("oidc");
    setName("");
    setEmailDomain("");
    setConfigText("");
    setClientSecret("");
    setDefaultRole("viewer");
    setEnabled(true);
  }, [open]);

  const save = useMutation({
    mutationFn: () => {
      let config: Record<string, unknown> = {};
      if (configText.trim()) {
        config = JSON.parse(configText) as Record<string, unknown>;
      }
      const body: Record<string, unknown> = {
        protocol,
        name: name.trim(),
        enabled,
        config,
        default_role: defaultRole,
      };
      if (emailDomain.trim()) body.email_domain = emailDomain.trim();
      if (protocol === "oidc" && clientSecret.trim()) {
        body.client_secret = clientSecret.trim();
      }
      return api.post<SsoConnection>("/sso-connections", body);
    },
    onSuccess: () => {
      toast.success("Connection created");
      onOpenChange(false);
      onSaved();
    },
    onError: (e) => {
      if (e instanceof SyntaxError) {
        toast.error("Config must be valid JSON");
        return;
      }
      toast.error(errMsg(e, "Couldn't save connection"));
    },
  });

  function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    save.mutate();
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] max-w-lg overflow-y-auto">
        <form onSubmit={submit}>
          <DialogHeader>
            <DialogTitle>Add SSO connection</DialogTitle>
            <DialogDescription>
              Client secrets are encrypted at rest and never returned.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-4">
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label htmlFor="sso-protocol">Protocol</Label>
                <Select
                  value={protocol}
                  onValueChange={(v) => setProtocol(v as SsoProtocol)}
                >
                  <SelectTrigger id="sso-protocol">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {PROTOCOLS.map((p) => (
                      <SelectItem key={p} value={p}>
                        {PROTOCOL_LABELS[p]}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="sso-role">Default role</Label>
                <Select
                  value={defaultRole}
                  onValueChange={(v) => setDefaultRole(v as OrgRole)}
                >
                  <SelectTrigger id="sso-role" className="capitalize">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {ROLES.map((r) => (
                      <SelectItem key={r} value={r} className="capitalize">
                        {r}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="sso-name">Name</Label>
              <Input
                id="sso-name"
                required
                placeholder="Acme Okta"
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="sso-domain">Email domain</Label>
              <Input
                id="sso-domain"
                placeholder="acme.com"
                value={emailDomain}
                onChange={(e) => setEmailDomain(e.target.value)}
              />
              <p className="text-xs text-muted-foreground">
                Optional. Users with this email domain are routed to this provider.
              </p>
            </div>

            {protocol === "oidc" ? (
              <div className="space-y-1.5">
                <Label htmlFor="sso-secret">Client secret</Label>
                <Input
                  id="sso-secret"
                  type="password"
                  autoComplete="off"
                  placeholder="Provided by your identity provider"
                  value={clientSecret}
                  onChange={(e) => setClientSecret(e.target.value)}
                />
              </div>
            ) : null}

            <div className="space-y-1.5">
              <Label htmlFor="sso-config">Config (JSON)</Label>
              <Textarea
                id="sso-config"
                className="min-h-[9rem] font-mono text-xs"
                placeholder={CONFIG_PLACEHOLDER[protocol]}
                value={configText}
                onChange={(e) => setConfigText(e.target.value)}
              />
              <p className="text-xs text-muted-foreground">
                {protocol === "oidc"
                  ? "Endpoints and client_id from your OIDC provider."
                  : "IdP SSO URL and signing certificate from your SAML provider."}
              </p>
            </div>

            <div className="flex items-center justify-between rounded-md border p-3">
              <div>
                <p className="text-sm font-medium">Enabled</p>
                <p className="text-xs text-muted-foreground">
                  Disabled connections can&apos;t be used to sign in.
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
            <Button type="submit" disabled={save.isPending || !name.trim()}>
              {save.isPending ? "Saving…" : "Create connection"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function CreateScimTokenDialog({
  open,
  onOpenChange,
  onCreated,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreated: (token: ScimTokenCreated) => void;
}) {
  const [name, setName] = React.useState("");

  React.useEffect(() => {
    if (open) setName("");
  }, [open]);

  const create = useMutation({
    mutationFn: () => api.post<ScimTokenCreated>("/scim-tokens", { name: name.trim() }),
    onSuccess: (token) => {
      onOpenChange(false);
      onCreated(token);
    },
    onError: (e) => toast.error(errMsg(e, "Couldn't create token")),
  });

  function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    create.mutate();
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <form onSubmit={submit}>
          <DialogHeader>
            <DialogTitle>Create SCIM token</DialogTitle>
            <DialogDescription>
              Name the token so you can recognize it later. The token is shown once.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-4">
            <div className="space-y-1.5">
              <Label htmlFor="scim-name">Name</Label>
              <Input
                id="scim-name"
                required
                placeholder="Okta production"
                value={name}
                onChange={(e) => setName(e.target.value)}
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
            <Button type="submit" disabled={create.isPending || !name.trim()}>
              {create.isPending ? "Creating…" : "Create token"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
