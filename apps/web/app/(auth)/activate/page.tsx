"use client";

import * as React from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useMutation, useQuery } from "@tanstack/react-query";
import {
  CheckCircle2,
  Loader2,
  Lock,
  ShieldAlert,
  Terminal,
  XCircle,
} from "lucide-react";
import { toast } from "sonner";

import { isOrgAdmin } from "@/components/governance/role-badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { api, ApiError, auth } from "@/lib/api";
import { useCurrentUser } from "@/lib/hooks";
import type { ApiKey, DeviceAuthPending, Membership } from "@/lib/types";
import { cn } from "@/lib/utils";

// Scopes an admin may grant to a terminal-initiated key (the backend refuses
// manage/* here - see app/schemas/device_auth.DEVICE_GRANTABLE_SCOPES).
const SCOPES: { value: string; label: string; hint: string }[] = [
  { value: "read", label: "read", hint: "Read collections & documents" },
  { value: "write", label: "write", hint: "Create & edit content" },
  { value: "search", label: "search", hint: "Query & retrieve" },
  { value: "ingest", label: "ingest", hint: "Upload & index documents" },
];

const DEFAULT_SCOPES = ["search", "read", "ingest"];

function errMsg(e: unknown, fallback = "Something went wrong") {
  return e instanceof ApiError ? e.message : fallback;
}

/** Canonicalise a typed/pasted code the way the backend does. */
function normalizeCode(raw: string): string {
  const cleaned = raw.trim().toUpperCase().replace(/\s/g, "");
  if (!cleaned.includes("-") && cleaned.length === 8)
    return `${cleaned.slice(0, 4)}-${cleaned.slice(4)}`;
  return cleaned;
}

function CenteredNote({
  icon: Icon,
  title,
  description,
  children,
}: {
  icon: React.ElementType;
  title: string;
  description: React.ReactNode;
  children?: React.ReactNode;
}) {
  return (
    <Card className="border-0 bg-transparent text-center shadow-none">
      <CardHeader className="items-center space-y-3 p-0">
        <span className="flex h-12 w-12 items-center justify-center rounded-full border bg-muted/40">
          <Icon className="h-5 w-5 text-muted-foreground" />
        </span>
        <CardTitle className="text-xl tracking-[-0.02em] text-foreground">
          {title}
        </CardTitle>
        <CardDescription className="text-[15px]">{description}</CardDescription>
      </CardHeader>
      {children ? <CardContent className="p-0 pt-6">{children}</CardContent> : null}
    </Card>
  );
}

function ActivateInner() {
  const router = useRouter();
  const params = useSearchParams();
  const initialCode = normalizeCode(params.get("code") ?? "");

  const [codeInput, setCodeInput] = React.useState(initialCode);
  const [code, setCode] = React.useState<string | null>(initialCode || null);
  const [decision, setDecision] = React.useState<"approved" | "denied" | null>(null);

  // The approval requires a signed-in admin; bounce to login preserving this deep link.
  const [authed, setAuthed] = React.useState(false);
  React.useEffect(() => {
    if (auth.isAuthenticated) {
      setAuthed(true);
      return;
    }
    const next = initialCode ? `/activate?code=${initialCode}` : "/activate";
    router.replace(`/login?next=${encodeURIComponent(next)}`);
  }, [router, initialCode]);

  const meQuery = useCurrentUser({ enabled: authed });
  const admin = isOrgAdmin(meQuery.data?.role);

  const pendingQuery = useQuery<DeviceAuthPending>({
    queryKey: ["device-auth-pending", code],
    queryFn: () => api.get<DeviceAuthPending>(`/device-auth/pending/${code}`),
    enabled: authed && admin && !!code,
    retry: false,
  });

  if (!authed || meQuery.isLoading) {
    return (
      <div className="flex justify-center py-16">
        <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
      </div>
    );
  }

  // A failed identity fetch (non-401: network blip, 500) must never fall through to
  // the "Code not found" screen below - the code may be perfectly valid.
  if (meQuery.isError) {
    return (
      <CenteredNote
        icon={XCircle}
        title="Couldn't load your session"
        description="We couldn't verify who is signed in. Check your connection and try again."
      >
        <button
          type="button"
          className="text-sm font-medium text-primary hover:underline"
          onClick={() => void meQuery.refetch()}
        >
          Try again
        </button>
      </CenteredNote>
    );
  }

  if (meQuery.data && !admin) {
    return (
      <CenteredNote
        icon={Lock}
        title="Admin access required"
        description="Only organization owners and admins can approve a device connection. Ask an admin to open this link."
      >
        <Link
          href="/dashboard"
          className="text-sm font-medium text-primary hover:underline"
        >
          Go to dashboard
        </Link>
      </CenteredNote>
    );
  }

  if (decision === "approved") {
    return (
      <CenteredNote
        icon={CheckCircle2}
        title="Device connected"
        description="The API key has been issued. Return to your terminal - the CLI will pick it up automatically."
      >
        <Link
          href="/dashboard/api-keys"
          className="text-sm font-medium text-primary hover:underline"
        >
          Manage API keys
        </Link>
      </CenteredNote>
    );
  }

  if (decision === "denied") {
    return (
      <CenteredNote
        icon={XCircle}
        title="Request denied"
        description="No key was issued. The terminal that requested access has been told the request was denied."
      >
        <Link
          href="/dashboard"
          className="text-sm font-medium text-primary hover:underline"
        >
          Go to dashboard
        </Link>
      </CenteredNote>
    );
  }

  if (!code) {
    return (
      <Card className="border-0 bg-transparent shadow-none">
        <CardHeader className="space-y-1.5 p-0">
          <CardTitle className="text-[28px] tracking-[-0.02em] text-foreground">
            Connect a device
          </CardTitle>
          <CardDescription className="text-[15px]">
            Enter the code shown in your terminal to review the connection request.
          </CardDescription>
        </CardHeader>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            const normalized = normalizeCode(codeInput);
            if (normalized) setCode(normalized);
          }}
        >
          <CardContent className="space-y-5 p-0 pt-8">
            <div className="space-y-2">
              <Label htmlFor="device-code">Device code</Label>
              <Input
                id="device-code"
                placeholder="XXXX-XXXX"
                autoComplete="off"
                className="h-11 rounded-lg bg-background font-mono uppercase tracking-widest"
                value={codeInput}
                onChange={(e) => setCodeInput(e.target.value.toUpperCase())}
              />
            </div>
            <Button type="submit" className="h-11 w-full" disabled={!codeInput.trim()}>
              Continue
            </Button>
          </CardContent>
        </form>
      </Card>
    );
  }

  if (pendingQuery.isLoading) {
    return (
      <div className="flex justify-center py-16">
        <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
      </div>
    );
  }

  // The backend 404s for an unknown/expired/handled code; anything else (500, network)
  // is transient and deserves a retry, not a claim that the code is bad.
  if (
    pendingQuery.isError &&
    !(pendingQuery.error instanceof ApiError && pendingQuery.error.status === 404)
  ) {
    return (
      <CenteredNote
        icon={XCircle}
        title="Couldn't check the code"
        description="Something went wrong looking up this device code. Check your connection and try again."
      >
        <button
          type="button"
          className="text-sm font-medium text-primary hover:underline"
          onClick={() => void pendingQuery.refetch()}
        >
          Try again
        </button>
      </CenteredNote>
    );
  }

  if (pendingQuery.isError || !pendingQuery.data) {
    return (
      <CenteredNote
        icon={XCircle}
        title="Code not found"
        description="This device code is invalid, expired, or already handled. Run the connect command again for a fresh code."
      >
        <button
          type="button"
          className="text-sm font-medium text-primary hover:underline"
          onClick={() => {
            setCodeInput("");
            setCode(null);
          }}
        >
          Enter a different code
        </button>
      </CenteredNote>
    );
  }

  return (
    <ApprovalForm
      code={code}
      pending={pendingQuery.data}
      approverId={meQuery.data?.user.id ?? null}
      onDecided={setDecision}
    />
  );
}

function ApprovalForm({
  code,
  pending,
  approverId,
  onDecided,
}: {
  code: string;
  pending: DeviceAuthPending;
  approverId: string | null;
  onDecided: (d: "approved" | "denied") => void;
}) {
  const [name, setName] = React.useState(`CLI - ${pending.client_name}`);
  // Propose what the terminal actually asked for (narrowed to the grantable set),
  // falling back to the defaults when the request carries nothing usable.
  const [scopes, setScopes] = React.useState<string[]>(() => {
    const requested = pending.requested_scopes.filter((s) =>
      SCOPES.some((o) => o.value === s),
    );
    return requested.length > 0 ? requested : DEFAULT_SCOPES;
  });
  const [actsAs, setActsAs] = React.useState<string | null>(approverId);

  const membersQuery = useQuery<Membership[]>({
    queryKey: ["org-members"],
    queryFn: () => api.get<Membership[]>("/orgs/members"),
  });

  function toggleScope(scope: string) {
    setScopes((prev) =>
      prev.includes(scope) ? prev.filter((s) => s !== scope) : [...prev, scope],
    );
  }

  const approve = useMutation({
    mutationFn: () =>
      api.post<ApiKey>("/device-auth/approve", {
        user_code: code,
        name: name.trim() || undefined,
        scopes,
        acts_as_user_id: actsAs ?? undefined,
      }),
    onSuccess: () => onDecided("approved"),
    onError: (e) => toast.error(errMsg(e, "Couldn't approve the request")),
  });

  const deny = useMutation({
    mutationFn: () => api.post("/device-auth/deny", { user_code: code }),
    onSuccess: () => onDecided("denied"),
    onError: (e) => toast.error(errMsg(e, "Couldn't deny the request")),
  });

  const busy = approve.isPending || deny.isPending;

  return (
    <Card className="border-0 bg-transparent shadow-none">
      <CardHeader className="space-y-1.5 p-0">
        <CardTitle className="text-[28px] tracking-[-0.02em] text-foreground">
          Approve this device?
        </CardTitle>
        <CardDescription className="text-[15px]">
          A terminal is asking for an API key to access your organization&apos;s
          knowledge.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-5 p-0 pt-8">
        <div className="flex items-start gap-3 rounded-lg border border-destructive/30 bg-destructive/5 p-3">
          <ShieldAlert className="mt-0.5 h-5 w-5 shrink-0 text-destructive" />
          <p className="text-sm text-muted-foreground">
            <span className="font-medium text-foreground">
              Only approve a request you started yourself.
            </span>{" "}
            Approving issues an API key that can access your organization&apos;s
            knowledge as the member you choose below. If this code didn&apos;t come
            from your own terminal, press Deny.
          </p>
        </div>

        <div className="flex items-center gap-3 rounded-lg border bg-muted/40 p-3">
          <Terminal className="h-5 w-5 shrink-0 text-muted-foreground" />
          <div className="min-w-0">
            <p className="truncate text-sm font-medium">{pending.client_name}</p>
            <p className="font-mono text-xs text-muted-foreground">Code {code}</p>
          </div>
        </div>

        <div className="space-y-2">
          <Label htmlFor="key-name">Key name</Label>
          <Input
            id="key-name"
            className="h-11 rounded-lg bg-background"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
        </div>

        <div className="space-y-2">
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

        <div className="space-y-2">
          <Label htmlFor="acts-as">Acts as member</Label>
          <Select value={actsAs ?? undefined} onValueChange={setActsAs}>
            <SelectTrigger id="acts-as" className="h-11 rounded-lg bg-background">
              <SelectValue placeholder="Select a member" />
            </SelectTrigger>
            <SelectContent>
              {(membersQuery.data ?? []).map((m) => (
                <SelectItem key={m.user_id} value={m.user_id}>
                  {m.user?.full_name || m.user?.email || m.user_id}
                  {m.user_id === approverId ? " (you)" : ""}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <p className="text-xs text-muted-foreground">
            The key sees exactly what this member is permitted to see.
          </p>
        </div>

        <div className="flex gap-3 pt-1">
          <Button
            type="button"
            variant="outline"
            className="h-11 flex-1"
            disabled={busy}
            onClick={() => deny.mutate()}
          >
            {deny.isPending ? "Denying…" : "Deny"}
          </Button>
          <Button
            type="button"
            className="h-11 flex-1"
            disabled={busy || scopes.length === 0}
            onClick={() => approve.mutate()}
          >
            {approve.isPending && <Loader2 className="h-4 w-4 animate-spin" />}
            {approve.isPending ? "Approving…" : "Approve"}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

export default function ActivatePage() {
  return (
    <React.Suspense fallback={<div className="h-4" />}>
      <ActivateInner />
    </React.Suspense>
  );
}
