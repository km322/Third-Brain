"use client";

import * as React from "react";
import { useMutation } from "@tanstack/react-query";
import { useTheme } from "next-themes";
import { Building2, Monitor, Moon, Plus, Sun, TriangleAlert } from "lucide-react";
import { toast } from "sonner";

import { PageHeader } from "@/components/dashboard/page-header";
import { isOrgAdmin } from "@/components/governance/role-badge";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
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
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api, ApiError, auth } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import type {
  AuthTokens,
  ChangePasswordRequest,
  Organization,
  PlanTier,
  User,
} from "@/lib/types";
import { cn, initials } from "@/lib/utils";

const PLAN_VARIANT: Record<PlanTier, "muted" | "info" | "default"> = {
  free: "muted",
  pro: "info",
  enterprise: "default",
};

function errMsg(e: unknown, fallback = "Something went wrong") {
  return e instanceof ApiError ? e.message : fallback;
}

export default function SettingsPage() {
  const { user, org, orgs, role, refetch, switchOrg } = useAuth();
  const admin = isOrgAdmin(role);
  const isOwner = role === "owner";

  return (
    <div className="space-y-6">
      <PageHeader
        title="Settings"
        description="Manage your profile, appearance and organization."
      />

      <ProfileCard user={user} onSaved={refetch} />
      <SecurityCard />
      <AppearanceCard />
      <OrgProfileCard org={org} admin={admin} onSaved={refetch} />
      <OrganizationsCard
        orgs={orgs}
        activeId={org?.id}
        onCreated={refetch}
        onSwitch={switchOrg}
      />
      {isOwner ? <DangerZoneCard orgName={org?.name} /> : null}
    </div>
  );
}

function ProfileCard({
  user,
  onSaved,
}: {
  user: User | null;
  onSaved: () => void;
}) {
  const [fullName, setFullName] = React.useState("");
  const [avatarUrl, setAvatarUrl] = React.useState("");

  React.useEffect(() => {
    setFullName(user?.full_name ?? "");
    setAvatarUrl(user?.avatar_url ?? "");
  }, [user?.full_name, user?.avatar_url]);

  const save = useMutation({
    mutationFn: () =>
      api.patch<User>("/users/me", {
        full_name: fullName.trim(),
        avatar_url: avatarUrl.trim() || null,
      }),
    onSuccess: () => {
      toast.success("Profile updated");
      onSaved();
    },
    onError: (e) => toast.error(errMsg(e, "Couldn't update profile")),
  });

  const dirty =
    fullName.trim() !== (user?.full_name ?? "") ||
    avatarUrl.trim() !== (user?.avatar_url ?? "");

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Your profile</CardTitle>
        <CardDescription>
          Update how you appear to teammates across Third Brain.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault();
            if (dirty) save.mutate();
          }}
        >
          <div className="flex items-center gap-4">
            <Avatar className="h-14 w-14">
              {avatarUrl ? <AvatarImage src={avatarUrl} alt={fullName} /> : null}
              <AvatarFallback className="text-lg">
                {initials(fullName || user?.email)}
              </AvatarFallback>
            </Avatar>
            <div className="text-sm">
              <p className="font-medium">{user?.email}</p>
              <p className="text-muted-foreground">
                Signed in as {user?.full_name || user?.email}
              </p>
            </div>
          </div>
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="full-name">Full name</Label>
              <Input
                id="full-name"
                value={fullName}
                onChange={(e) => setFullName(e.target.value)}
                placeholder="Ada Lovelace"
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="avatar-url">Avatar URL</Label>
              <Input
                id="avatar-url"
                type="url"
                value={avatarUrl}
                onChange={(e) => setAvatarUrl(e.target.value)}
                placeholder="https://…"
              />
            </div>
          </div>
          <div className="flex justify-end">
            <Button type="submit" disabled={!dirty || save.isPending}>
              {save.isPending ? "Saving…" : "Save profile"}
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  );
}

function SecurityCard() {
  const [currentPassword, setCurrentPassword] = React.useState("");
  const [newPassword, setNewPassword] = React.useState("");
  const [confirmPassword, setConfirmPassword] = React.useState("");
  const [fieldErrors, setFieldErrors] = React.useState<{
    newPassword?: string;
    confirmPassword?: string;
  }>({});

  const change = useMutation({
    mutationFn: () => {
      const body: ChangePasswordRequest = {
        current_password: currentPassword,
        new_password: newPassword,
      };
      return api.post<AuthTokens>("/auth/change-password", body);
    },
    onSuccess: (tokens) => {
      // The server revokes every other session; store the fresh pair so this
      // one stays signed in.
      auth.setSession(tokens.access_token, tokens.refresh_token);
      setCurrentPassword("");
      setNewPassword("");
      setConfirmPassword("");
      toast.success("Password changed");
    },
    onError: (e) => toast.error(errMsg(e, "Couldn't change password")),
  });

  function submit(e: React.FormEvent) {
    e.preventDefault();
    const errors: typeof fieldErrors = {};
    if (newPassword.length < 8) {
      errors.newPassword = "Password must be at least 8 characters";
    }
    if (confirmPassword !== newPassword) {
      errors.confirmPassword = "Passwords do not match";
    }
    setFieldErrors(errors);
    if (Object.keys(errors).length > 0) return;
    change.mutate();
  }

  const ready = !!currentPassword && !!newPassword && !!confirmPassword;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Security</CardTitle>
        <CardDescription>
          Change the password you use to sign in. Every other session is signed
          out.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form className="space-y-4" onSubmit={submit}>
          <div className="grid gap-4 sm:grid-cols-3">
            <div className="space-y-1.5">
              <Label htmlFor="current-password">Current password</Label>
              <Input
                id="current-password"
                type="password"
                autoComplete="current-password"
                value={currentPassword}
                onChange={(e) => setCurrentPassword(e.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="new-password">New password</Label>
              <Input
                id="new-password"
                type="password"
                autoComplete="new-password"
                placeholder="At least 8 characters"
                aria-invalid={!!fieldErrors.newPassword}
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
              />
              {fieldErrors.newPassword && (
                <p className="text-sm text-destructive">
                  {fieldErrors.newPassword}
                </p>
              )}
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="confirm-password">Confirm new password</Label>
              <Input
                id="confirm-password"
                type="password"
                autoComplete="new-password"
                aria-invalid={!!fieldErrors.confirmPassword}
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
              />
              {fieldErrors.confirmPassword && (
                <p className="text-sm text-destructive">
                  {fieldErrors.confirmPassword}
                </p>
              )}
            </div>
          </div>
          <div className="flex justify-end">
            <Button type="submit" disabled={!ready || change.isPending}>
              {change.isPending ? "Changing…" : "Change password"}
            </Button>
          </div>
        </form>
      </CardContent>
    </Card>
  );
}

function AppearanceCard() {
  const { theme, setTheme } = useTheme();
  const [mounted, setMounted] = React.useState(false);
  React.useEffect(() => setMounted(true), []);

  const options = [
    { value: "light", label: "Light", icon: Sun },
    { value: "dark", label: "Dark", icon: Moon },
    { value: "system", label: "System", icon: Monitor },
  ] as const;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Appearance</CardTitle>
        <CardDescription>Choose how Third Brain looks for you.</CardDescription>
      </CardHeader>
      <CardContent>
        <div className="grid max-w-md grid-cols-3 gap-3">
          {options.map(({ value, label, icon: Icon }) => {
            const active = mounted && theme === value;
            return (
              <button
                key={value}
                type="button"
                onClick={() => setTheme(value)}
                className={cn(
                  "flex flex-col items-center gap-2 rounded-lg border p-4 text-sm font-medium transition-colors",
                  active
                    ? "border-primary bg-primary/10 text-primary"
                    : "hover:bg-accent",
                )}
              >
                <Icon className="h-5 w-5" />
                {label}
              </button>
            );
          })}
        </div>
      </CardContent>
    </Card>
  );
}

function OrgProfileCard({
  org,
  admin,
  onSaved,
}: {
  org: Organization | null;
  admin: boolean;
  onSaved: () => void;
}) {
  const [name, setName] = React.useState("");
  React.useEffect(() => setName(org?.name ?? ""), [org?.name]);

  const save = useMutation({
    mutationFn: () => api.patch<Organization>("/orgs/current", { name: name.trim() }),
    onSuccess: () => {
      toast.success("Organization updated");
      onSaved();
    },
    onError: (e) => toast.error(errMsg(e, "Couldn't update organization")),
  });

  const dirty = name.trim() !== (org?.name ?? "") && name.trim().length > 0;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Organization</CardTitle>
        <CardDescription>
          {admin
            ? "Update your organization's profile."
            : "Your organization's profile. Only admins can make changes."}
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form
          className="space-y-4"
          onSubmit={(e) => {
            e.preventDefault();
            if (dirty) save.mutate();
          }}
        >
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="org-name">Name</Label>
              <Input
                id="org-name"
                value={name}
                disabled={!admin}
                onChange={(e) => setName(e.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="org-slug">Slug</Label>
              <Input id="org-slug" value={org?.slug ?? ""} disabled readOnly />
            </div>
          </div>
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2 text-sm">
              <span className="text-muted-foreground">Plan</span>
              {org ? (
                <Badge
                  variant={PLAN_VARIANT[org.plan] ?? "muted"}
                  className="capitalize"
                >
                  {org.plan}
                </Badge>
              ) : null}
            </div>
            {admin ? (
              <Button type="submit" disabled={!dirty || save.isPending}>
                {save.isPending ? "Saving…" : "Save changes"}
              </Button>
            ) : null}
          </div>
        </form>
      </CardContent>
    </Card>
  );
}

function OrganizationsCard({
  orgs,
  activeId,
  onCreated,
  onSwitch,
}: {
  orgs: Organization[];
  activeId?: string;
  onCreated: () => void;
  onSwitch: (orgId: string) => Promise<void>;
}) {
  const [createOpen, setCreateOpen] = React.useState(false);
  const [switching, setSwitching] = React.useState<string | null>(null);

  async function handleSwitch(id: string) {
    setSwitching(id);
    try {
      await onSwitch(id);
      toast.success("Switched organization");
    } catch (e) {
      toast.error(errMsg(e, "Couldn't switch organization"));
    } finally {
      setSwitching(null);
    }
  }

  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between space-y-0">
        <div className="space-y-1.5">
          <CardTitle className="text-base">Your organizations</CardTitle>
          <CardDescription>Switch between orgs or create a new one.</CardDescription>
        </div>
        <Button variant="outline" size="sm" onClick={() => setCreateOpen(true)}>
          <Plus className="h-4 w-4" />
          New
        </Button>
      </CardHeader>
      <CardContent className="space-y-2">
        {orgs.map((o) => {
          const active = o.id === activeId;
          return (
            <div
              key={o.id}
              className="flex items-center gap-3 rounded-md border p-3"
            >
              <span className="flex h-8 w-8 items-center justify-center rounded bg-primary/10 text-xs font-bold text-primary">
                {initials(o.name)}
              </span>
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium">{o.name}</p>
                <p className="truncate text-xs capitalize text-muted-foreground">
                  {o.plan} plan
                </p>
              </div>
              {active ? (
                <Badge variant="success">Active</Badge>
              ) : (
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={switching !== null}
                  onClick={() => handleSwitch(o.id)}
                >
                  {switching === o.id ? "Switching…" : "Switch"}
                </Button>
              )}
            </div>
          );
        })}
      </CardContent>

      <CreateOrgDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        onCreated={onCreated}
      />
    </Card>
  );
}

function CreateOrgDialog({
  open,
  onOpenChange,
  onCreated,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreated: () => void;
}) {
  const [name, setName] = React.useState("");

  React.useEffect(() => {
    if (open) setName("");
  }, [open]);

  const create = useMutation({
    mutationFn: () => api.post<Organization>("/orgs", { name: name.trim() }),
    onSuccess: () => {
      toast.success("Organization created");
      onOpenChange(false);
      onCreated();
    },
    onError: (e) => toast.error(errMsg(e, "Couldn't create organization")),
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (name.trim()) create.mutate();
          }}
        >
          <DialogHeader>
            <DialogTitle>New organization</DialogTitle>
            <DialogDescription>
              You&apos;ll become its owner. Switch to it any time from here.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-1.5 py-4">
            <Label htmlFor="new-org-name">Name</Label>
            <Input
              id="new-org-name"
              required
              placeholder="Acme Inc."
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
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
              {create.isPending ? "Creating…" : "Create"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function DangerZoneCard({ orgName }: { orgName?: string }) {
  return (
    <Card className="border-destructive/40">
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base text-destructive">
          <TriangleAlert className="h-4 w-4" />
          Danger zone
        </CardTitle>
        <CardDescription>Irreversible and destructive actions.</CardDescription>
      </CardHeader>
      <CardContent>
        <div className="flex flex-col gap-3 rounded-md border border-destructive/40 p-4 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex items-start gap-3">
            <Building2 className="mt-0.5 h-4 w-4 shrink-0 text-destructive" />
            <div>
              <p className="text-sm font-medium">Delete organization</p>
              <p className="text-sm text-muted-foreground">
                Permanently delete{" "}
                <span className="font-medium">{orgName}</span> and all of its
                knowledge bases, documents and keys.
              </p>
            </div>
          </div>
          <Button variant="destructive" disabled title="Contact support to delete an organization">
            Delete
          </Button>
        </div>
        <p className="mt-2 text-xs text-muted-foreground">
          Organization deletion is handled by support to prevent accidental data
          loss. Contact us to proceed.
        </p>
      </CardContent>
    </Card>
  );
}
