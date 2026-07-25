"use client";

import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  KeyRound,
  Lock,
  MailPlus,
  MoreHorizontal,
  ShieldAlert,
  ShieldCheck,
  UserRoundX,
} from "lucide-react";
import { toast } from "sonner";

import { CopyButton } from "@/components/copy-button";
import { DataTable, type Column } from "@/components/dashboard/data-table";
import { EmptyState } from "@/components/dashboard/empty-state";
import { PageHeader } from "@/components/dashboard/page-header";
import {
  isOrgAdmin,
  MembershipStatusBadge,
  RoleBadge,
  ROLE_DESCRIPTIONS,
} from "@/components/governance/role-badge";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
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
  DropdownMenuLabel,
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
import { Separator } from "@/components/ui/separator";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import type {
  Membership,
  MembershipStatus,
  MemberPasswordReset,
  OrgRole,
} from "@/lib/types";
import { cn, initials } from "@/lib/utils";

const ROLE_OPTIONS: OrgRole[] = ["viewer", "editor", "admin", "owner"];

// Most → least privileged, for the "Roles and access" reference card.
const ROLE_REFERENCE: OrgRole[] = ["owner", "admin", "editor", "viewer"];

function errMsg(e: unknown, fallback = "Something went wrong") {
  return e instanceof ApiError ? e.message : fallback;
}

/** A role name stacked over its one-line description, for use in a `<Select>`. */
function RoleOptionLabel({ role }: { role: OrgRole }) {
  return (
    <span className="flex max-w-[15rem] flex-col gap-0.5">
      <span className="capitalize">{role}</span>
      <span className="whitespace-normal text-xs font-normal text-muted-foreground">
        {ROLE_DESCRIPTIONS[role]}
      </span>
    </span>
  );
}

export default function MembersPage() {
  const { org, role, user } = useAuth();
  const admin = isOrgAdmin(role);
  const isOwner = role === "owner";
  const queryClient = useQueryClient();
  const membersKey = React.useMemo(() => ["org-members", org?.id], [org?.id]);

  const [inviteOpen, setInviteOpen] = React.useState(false);
  const [removing, setRemoving] = React.useState<Membership | null>(null);
  const [resetting, setResetting] = React.useState<Membership | null>(null);
  const [tempReset, setTempReset] = React.useState<{
    member: Membership;
    password: string;
  } | null>(null);

  const membersQuery = useQuery<Membership[]>({
    queryKey: membersKey,
    queryFn: () => api.get<Membership[]>("/orgs/members"),
    enabled: admin,
  });

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: membersKey });

  const updateMember = useMutation({
    mutationFn: ({
      id,
      body,
    }: {
      id: string;
      body: { role?: OrgRole; status?: MembershipStatus };
    }) => api.patch<Membership>(`/orgs/members/${id}`, body),
    onSuccess: () => {
      toast.success("Member updated");
      void invalidate();
    },
    onError: (e) => toast.error(errMsg(e, "Couldn't update member")),
  });

  const removeMember = useMutation({
    mutationFn: (id: string) => api.delete(`/orgs/members/${id}`),
    onSuccess: () => {
      toast.success("Member removed");
      setRemoving(null);
      void invalidate();
    },
    onError: (e) => toast.error(errMsg(e, "Couldn't remove member")),
  });

  const resetPassword = useMutation({
    mutationFn: (m: Membership) =>
      api.post<MemberPasswordReset>(`/orgs/members/${m.id}/reset-password`),
    onSuccess: (reset, m) => {
      setResetting(null);
      setTempReset({ member: m, password: reset.temporary_password });
    },
    onError: (e) => toast.error(errMsg(e, "Couldn't reset password")),
  });

  if (!admin) {
    return (
      <div className="space-y-6">
        <PageHeader
          title="Members"
          description="Manage who belongs to this organization and what they can do."
        />
        <EmptyState
          icon={Lock}
          title="Admin access required"
          description="Only organization owners and admins can view and manage members."
        />
      </div>
    );
  }

  const columns: Column<Membership>[] = [
    {
      id: "member",
      header: "Member",
      cell: (m) => (
        <div className="flex items-center gap-3">
          <Avatar className="h-8 w-8">
            {m.user?.avatar_url ? (
              <AvatarImage src={m.user.avatar_url} alt={m.user.full_name ?? ""} />
            ) : null}
            <AvatarFallback>
              {initials(m.user?.full_name || m.user?.email)}
            </AvatarFallback>
          </Avatar>
          <div className="min-w-0">
            <p className="truncate text-sm font-medium">
              {m.user?.full_name || "-"}
              {m.user_id === user?.id ? (
                <span className="ml-1.5 text-xs text-muted-foreground">
                  (you)
                </span>
              ) : null}
            </p>
            <p className="truncate text-xs text-muted-foreground">
              {m.user?.email}
            </p>
          </div>
        </div>
      ),
    },
    {
      id: "role",
      header: "Role",
      cell: (m) => {
        // Owner-level transitions are owner-only; disable the whole control on
        // an owner row (and the owner option elsewhere) for non-owners.
        const locked = !isOwner && m.role === "owner";
        return locked ? (
          <RoleBadge role={m.role} />
        ) : (
          <Select
            value={m.role}
            onValueChange={(value) =>
              updateMember.mutate({ id: m.id, body: { role: value as OrgRole } })
            }
            disabled={updateMember.isPending}
          >
            <SelectTrigger className="h-8 w-[130px]">
              <SelectValue className="capitalize">{m.role}</SelectValue>
            </SelectTrigger>
            <SelectContent>
              {ROLE_OPTIONS.map((r) => (
                <SelectItem
                  key={r}
                  value={r}
                  disabled={r === "owner" && !isOwner}
                >
                  <RoleOptionLabel role={r} />
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        );
      },
    },
    {
      id: "status",
      header: "Status",
      hideOnMobile: true,
      cell: (m) => <MembershipStatusBadge status={m.status} />,
    },
    {
      id: "actions",
      header: "",
      align: "right",
      cell: (m) => {
        const isSelf = m.user_id === user?.id;
        // Only an owner may change an owner's role, status or membership; mirror the backend
        // owner-only gate so a non-owner admin isn't offered actions that always 403.
        const ownerLocked = m.role === "owner" && !isOwner;
        const canRemoveOwner = m.role !== "owner" || isOwner;
        return (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="icon" aria-label="Member actions">
                <MoreHorizontal className="h-4 w-4" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-44">
              <DropdownMenuLabel>Manage</DropdownMenuLabel>
              <DropdownMenuSeparator />
              {m.status !== "active" ? (
                <DropdownMenuItem
                  disabled={ownerLocked}
                  onClick={() =>
                    updateMember.mutate({ id: m.id, body: { status: "active" } })
                  }
                >
                  <ShieldCheck className="h-4 w-4" />
                  Activate
                </DropdownMenuItem>
              ) : (
                <DropdownMenuItem
                  disabled={isSelf || ownerLocked}
                  onClick={() =>
                    updateMember.mutate({
                      id: m.id,
                      body: { status: "suspended" },
                    })
                  }
                >
                  <Lock className="h-4 w-4" />
                  Suspend
                </DropdownMenuItem>
              )}
              <DropdownMenuItem
                disabled={isSelf || ownerLocked}
                onClick={() => setResetting(m)}
              >
                <KeyRound className="h-4 w-4" />
                Reset password
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem
                className="text-destructive focus:text-destructive"
                disabled={isSelf || !canRemoveOwner}
                onClick={() => setRemoving(m)}
              >
                <UserRoundX className="h-4 w-4" />
                Remove
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        );
      },
    },
  ];

  return (
    <div className="space-y-6">
      <PageHeader
        title="Members"
        description="Manage who belongs to this organization and what they can do."
        actions={
          <Button onClick={() => setInviteOpen(true)}>
            <MailPlus className="h-4 w-4" />
            Invite member
          </Button>
        }
      />

      <DataTable
        columns={columns}
        data={membersQuery.data ?? []}
        rowKey={(m) => m.id}
        isLoading={membersQuery.isLoading}
        empty={
          <EmptyState
            compact
            icon={MailPlus}
            title="No members yet"
            description="Invite teammates who already have a Third Brain account."
          />
        }
      />

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Roles and access</CardTitle>
          <CardDescription>
            How each organization role maps to what a member can see and do.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-3">
            {ROLE_REFERENCE.map((r) => (
              <div
                key={r}
                className="grid grid-cols-[76px_1fr] items-start gap-3"
              >
                <RoleBadge role={r} />
                <p className="text-sm text-muted-foreground">
                  {ROLE_DESCRIPTIONS[r]}
                </p>
              </div>
            ))}
          </div>
          <Separator />
          <p className="text-sm text-muted-foreground">
            Make someone an{" "}
            <span className="font-medium text-foreground">admin</span> to grant
            them visibility of every document in the organization.{" "}
            <span className="font-medium text-foreground">Editor</span> is the
            role for members who manage their own knowledge.
          </p>
        </CardContent>
      </Card>

      <InviteDialog
        open={inviteOpen}
        onOpenChange={setInviteOpen}
        isOwner={isOwner}
        onInvited={() => void invalidate()}
      />

      {/* Reset-password confirm */}
      <Dialog
        open={resetting !== null}
        onOpenChange={(o) => !o && setResetting(null)}
      >
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Reset password?</DialogTitle>
            <DialogDescription>
              {resetting?.user?.full_name || resetting?.user?.email} will get a
              temporary password and every session they have will be signed
              out. Share it with them securely; they should change it right
              away in Settings.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setResetting(null)}
              disabled={resetPassword.isPending}
            >
              Cancel
            </Button>
            <Button
              disabled={resetPassword.isPending}
              onClick={() => resetting && resetPassword.mutate(resetting)}
            >
              {resetPassword.isPending ? "Resetting…" : "Reset password"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* One-time temporary-password reveal */}
      <Dialog
        open={tempReset !== null}
        onOpenChange={(o) => !o && setTempReset(null)}
      >
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle>Temporary password</DialogTitle>
            <DialogDescription>
              Share this with{" "}
              <span className="font-medium">
                {tempReset?.member.user?.full_name ||
                  tempReset?.member.user?.email}
              </span>{" "}
              securely. They should sign in with it and set a new password
              under Settings.
            </DialogDescription>
          </DialogHeader>
          <div className="flex items-center gap-2 rounded-md border bg-muted/40 p-3">
            <code className="min-w-0 flex-1 break-all font-mono text-sm">
              {tempReset?.password}
            </code>
            <CopyButton
              value={tempReset?.password ?? ""}
              variant="outline"
              toastMessage="Temporary password copied"
            />
          </div>
          <div className="flex items-start gap-2 text-xs text-warning">
            <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" />
            <span>
              This is the only time it will be shown. Only a hash is stored, so
              if it is lost you&apos;ll need to reset the password again.
            </span>
          </div>
          <DialogFooter>
            <Button onClick={() => setTempReset(null)}>Done</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={removing !== null}
        onOpenChange={(o) => !o && setRemoving(null)}
      >
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Remove member?</DialogTitle>
            <DialogDescription>
              {removing?.user?.full_name || removing?.user?.email} will lose
              access to <span className="font-medium">{org?.name}</span>. This
              can be undone by inviting them again.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setRemoving(null)}
              disabled={removeMember.isPending}
            >
              Cancel
            </Button>
            <Button
              variant="destructive"
              disabled={removeMember.isPending}
              onClick={() => removing && removeMember.mutate(removing.id)}
            >
              {removeMember.isPending ? "Removing…" : "Remove"}
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
      api.post<Membership>("/orgs/members/invite", {
        email: email.trim(),
        role: inviteRole,
      }),
    onSuccess: (m) => {
      toast.success(`Invited ${m.user?.email ?? email}`);
      setEmail("");
      setInviteRole("viewer");
      onOpenChange(false);
      onInvited();
    },
    onError: (e) => toast.error(errMsg(e, "Couldn't send invite")),
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
              The person must already have a Third Brain account. They&apos;ll
              be added with the role you choose.
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
                    <SelectItem
                      key={r}
                      value={r}
                      disabled={r === "owner" && !isOwner}
                    >
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
            <Button
              type="submit"
              disabled={invite.isPending || !email.trim()}
              className={cn(invite.isPending && "opacity-80")}
            >
              {invite.isPending ? "Inviting…" : "Send invite"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
