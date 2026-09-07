"use client";

import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { formatDistanceToNow } from "date-fns";
import { Lock, MailPlus, UserPlus, X } from "lucide-react";
import { toast } from "sonner";

import { EmptyState } from "@/components/dashboard/empty-state";
import { TableSkeleton } from "@/components/dashboard/loading";
import { PageHeader } from "@/components/dashboard/page-header";
import {
  isOrgAdmin,
  RoleBadge,
  ROLE_DESCRIPTIONS,
} from "@/components/governance/role-badge";
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
import type { Invite, InviteStatus, OrgRole } from "@/lib/types";

const ROLE_OPTIONS: OrgRole[] = ["viewer", "editor", "admin", "owner"];

const STATUS_VARIANT: Record<InviteStatus, "info" | "success" | "muted" | "destructive"> =
  {
    pending: "info",
    accepted: "success",
    revoked: "muted",
    expired: "destructive",
  };

const errMsg = (e: unknown, f = "Something went wrong") =>
  e instanceof ApiError ? e.message : f;

/** A role name stacked over its one-line description, for use in a `<Select>`. */
function RoleOptionLabel({ role }: { role: OrgRole }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="capitalize">{role}</span>
      <span className="text-xs text-muted-foreground">{ROLE_DESCRIPTIONS[role]}</span>
    </div>
  );
}

export default function InvitesPage() {
  const { role } = useAuth();
  const admin = isOrgAdmin(role);
  const isOwner = role === "owner";
  const queryClient = useQueryClient();

  const [inviteOpen, setInviteOpen] = React.useState(false);
  const [revoking, setRevoking] = React.useState<Invite | null>(null);

  const invitesQuery = useQuery<Invite[]>({
    queryKey: ["invites"],
    queryFn: () => api.get<Invite[]>("/invites"),
    enabled: admin,
  });

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["invites"] });

  const revokeInvite = useMutation({
    mutationFn: (id: string) => api.delete(`/invites/${id}`),
    onSuccess: () => {
      toast.success("Invitation revoked");
      setRevoking(null);
      void invalidate();
    },
    onError: (e) => toast.error(errMsg(e, "Couldn't revoke invitation")),
  });

  if (!admin) {
    return (
      <div className="space-y-6">
        <PageHeader
          title="Invites"
          description="Pending invitations to join this organization."
        />
        <EmptyState
          icon={Lock}
          title="Admin access required"
          description="Only organization owners and admins can invite members and manage pending invitations."
        />
      </div>
    );
  }

  const invites = invitesQuery.data ?? [];

  return (
    <div className="space-y-6">
      <PageHeader
        title="Invites"
        description="Invite people who don't yet have a Third Brain account. They join this organization with the role you choose once they accept."
        actions={
          <Button onClick={() => setInviteOpen(true)}>
            <MailPlus className="h-4 w-4" />
            Invite member
          </Button>
        }
      />

      {invitesQuery.isLoading ? (
        <TableSkeleton rows={4} columns={5} />
      ) : invitesQuery.isError ? (
        <EmptyState
          icon={UserPlus}
          title="Couldn't load invites"
          description="Something went wrong fetching pending invitations. Try again in a moment."
        />
      ) : invites.length === 0 ? (
        <EmptyState
          icon={UserPlus}
          title="No pending invites"
          description="Invite someone new to join this organization. People who already have a Third Brain account should be added from Members instead."
          actions={
            <Button onClick={() => setInviteOpen(true)}>
              <MailPlus className="h-4 w-4" />
              Invite member
            </Button>
          }
        />
      ) : (
        <Card className="overflow-hidden">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Email</TableHead>
                <TableHead>Role</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Expires</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {invites.map((inv) => (
                <TableRow key={inv.id}>
                  <TableCell className="font-medium">{inv.email}</TableCell>
                  <TableCell>
                    <RoleBadge role={inv.role} />
                  </TableCell>
                  <TableCell>
                    <Badge
                      variant={STATUS_VARIANT[inv.status] ?? "muted"}
                      className="capitalize"
                    >
                      {inv.status}
                    </Badge>
                  </TableCell>
                  <TableCell>
                    <span
                      className="text-sm text-muted-foreground"
                      title={new Date(inv.expires_at).toLocaleString()}
                    >
                      {formatDistanceToNow(new Date(inv.expires_at), {
                        addSuffix: true,
                      })}
                    </span>
                  </TableCell>
                  <TableCell className="text-right">
                    <Button
                      variant="ghost"
                      size="sm"
                      className="text-destructive hover:text-destructive"
                      onClick={() => setRevoking(inv)}
                    >
                      <X className="h-4 w-4" />
                      Revoke
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Card>
      )}

      <InviteDialog
        open={inviteOpen}
        onOpenChange={setInviteOpen}
        isOwner={isOwner}
        onInvited={invalidate}
      />

      <Dialog open={revoking !== null} onOpenChange={(o) => !o && setRevoking(null)}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Revoke invitation?</DialogTitle>
            <DialogDescription>
              The invitation for <span className="font-medium">{revoking?.email}</span>{" "}
              will be cancelled, and any link they received will stop working.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setRevoking(null)}
              disabled={revokeInvite.isPending}
            >
              Cancel
            </Button>
            <Button
              variant="destructive"
              disabled={revokeInvite.isPending}
              onClick={() => revoking && revokeInvite.mutate(revoking.id)}
            >
              {revokeInvite.isPending ? "Revoking…" : "Revoke"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function InviteDialog({
  open,
  onOpenChange,
  isOwner,
  onInvited,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  isOwner: boolean;
  onInvited: () => void;
}) {
  const [email, setEmail] = React.useState("");
  const [inviteRole, setInviteRole] = React.useState<OrgRole>("viewer");

  const invite = useMutation({
    mutationFn: () =>
      api.post<Invite>("/invites", {
        email: email.trim(),
        role: inviteRole,
      }),
    onSuccess: (inv) => {
      toast.success(`Invitation sent to ${inv.email}`);
      setEmail("");
      setInviteRole("viewer");
      onOpenChange(false);
      onInvited();
    },
    // A 409 means the email already belongs to a member; surface the backend
    // message so the admin knows to add them from Members instead.
    onError: (e) => toast.error(errMsg(e, "Couldn't send invitation")),
  });

  function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!email.trim()) return;
    invite.mutate();
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <form onSubmit={submit}>
          <DialogHeader>
            <DialogTitle>Invite member</DialogTitle>
            <DialogDescription>
              We&apos;ll email an invitation link. Someone who already has a Third Brain
              account should be added from Members instead.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-4">
            <div className="space-y-1.5">
              <Label htmlFor="invite-email">Email</Label>
              <Input
                id="invite-email"
                type="email"
                required
                placeholder="teammate@company.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="invite-role">Role</Label>
              <Select
                value={inviteRole}
                onValueChange={(v) => setInviteRole(v as OrgRole)}
              >
                <SelectTrigger id="invite-role">
                  <SelectValue className="capitalize">{inviteRole}</SelectValue>
                </SelectTrigger>
                <SelectContent>
                  {ROLE_OPTIONS.map((r) => (
                    <SelectItem key={r} value={r} disabled={r === "owner" && !isOwner}>
                      <RoleOptionLabel role={r} />
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <p className="text-xs text-muted-foreground">
                {ROLE_DESCRIPTIONS[inviteRole]}
              </p>
            </div>
          </div>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => onOpenChange(false)}
              disabled={invite.isPending}
            >
              Cancel
            </Button>
            <Button type="submit" disabled={invite.isPending || !email.trim()}>
              {invite.isPending ? "Sending…" : "Send invitation"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
