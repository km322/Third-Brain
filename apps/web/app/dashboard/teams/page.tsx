"use client";

import * as React from "react";
import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Crown,
  FolderPlus,
  MoreHorizontal,
  Pencil,
  Plus,
  Trash2,
  UserPlus,
  UserRound,
  UsersRound,
  X,
} from "lucide-react";
import { toast } from "sonner";

import { CardGridSkeleton } from "@/components/dashboard/loading";
import { EmptyState } from "@/components/dashboard/empty-state";
import { PageHeader } from "@/components/dashboard/page-header";
import { isOrgAdmin, orgRoleAtLeast } from "@/components/governance/role-badge";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
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
import { Textarea } from "@/components/ui/textarea";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import type { Membership, Team, TeamDetail, TeamRole } from "@/lib/types";
import { initials } from "@/lib/utils";

/** Mirror of the backend cap in app/api/routes/teams.py (root inclusive). */
const MAX_TEAM_DEPTH = 6;

/**
 * Sentinel value for the "no parent (root)" option, since a shadcn/Radix
 * <SelectItem> cannot carry an empty-string value.
 */
const ROOT_PARENT = "__root__";

function errMsg(e: unknown, fallback = "Something went wrong") {
  return e instanceof ApiError ? e.message : fallback;
}

/** Every transitive descendant of `id` in the flat team list (excludes itself). */
function descendantsOf(id: string, teams: Team[]): Set<string> {
  const childrenById = new Map<string, string[]>();
  for (const t of teams) {
    if (t.parent_team_id) {
      const arr = childrenById.get(t.parent_team_id) ?? [];
      arr.push(t.id);
      childrenById.set(t.parent_team_id, arr);
    }
  }
  const out = new Set<string>();
  const stack = [...(childrenById.get(id) ?? [])];
  while (stack.length) {
    const node = stack.pop() as string;
    if (out.has(node)) continue;
    out.add(node);
    stack.push(...(childrenById.get(node) ?? []));
  }
  return out;
}

/**
 * Teams page - the org's team tree, its rosters and who may change them.
 *
 * Creating a (root or sub-) team requires at least the editor org role. Management
 * authority mirrors the backend `can_admin_team`: org admins may manage every team;
 * otherwise a user manages a team only when they lead it or lead one of its ancestors.
 * Non-admins therefore need each team's roster to discover which teams they lead, so the
 * per-team detail queries run for them - reusing the same `["team", id]` cache as the
 * manage dialog, so nothing is fetched twice.
 *
 * A team with no parent, or whose parent is not visible, sits at the root of the tree.
 */
export default function TeamsPage() {
  const { role, user } = useAuth();
  const admin = isOrgAdmin(role);
  const canCreate = orgRoleAtLeast(role, "editor");
  const queryClient = useQueryClient();

  const [createOpen, setCreateOpen] = React.useState(false);
  const [subteamParent, setSubteamParent] = React.useState<Team | null>(null);
  const [editing, setEditing] = React.useState<Team | null>(null);
  const [managing, setManaging] = React.useState<Team | null>(null);
  const [deleting, setDeleting] = React.useState<Team | null>(null);

  const teamsQuery = useQuery<Team[]>({
    queryKey: ["teams"],
    queryFn: () => api.get<Team[]>("/teams"),
  });

  const teams = React.useMemo(() => teamsQuery.data ?? [], [teamsQuery.data]);

  const detailQueries = useQueries({
    queries: (admin ? [] : teams).map((t) => ({
      queryKey: ["team", t.id],
      queryFn: () => api.get<TeamDetail>(`/teams/${t.id}`),
    })),
  });

  const leadTeamIds = React.useMemo(() => {
    const s = new Set<string>();
    for (const q of detailQueries) {
      const d = q.data;
      if (d?.members.some((m) => m.user_id === user?.id && m.role === "lead")) {
        s.add(d.id);
      }
    }
    return s;
  }, [detailQueries, user?.id]);

  const parentById = React.useMemo(
    () => new Map(teams.map((t) => [t.id, t.parent_team_id ?? null])),
    [teams],
  );

  const canAdminTeam = React.useCallback(
    (team: Team): boolean => {
      if (admin) return true;
      let current: string | null | undefined = team.id;
      const seen = new Set<string>();
      while (current && !seen.has(current)) {
        if (leadTeamIds.has(current)) return true;
        seen.add(current);
        current = parentById.get(current) ?? null;
      }
      return false;
    },
    [admin, leadTeamIds, parentById],
  );

  const { childrenById, roots } = React.useMemo(() => {
    const ids = new Set(teams.map((t) => t.id));
    const byParent = new Map<string, Team[]>();
    for (const t of teams) {
      if (t.parent_team_id && ids.has(t.parent_team_id)) {
        const arr = byParent.get(t.parent_team_id) ?? [];
        arr.push(t);
        byParent.set(t.parent_team_id, arr);
      }
    }
    const top = teams.filter((t) => !t.parent_team_id || !ids.has(t.parent_team_id));
    return { childrenById: byParent, roots: top };
  }, [teams]);

  const invalidate = React.useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: ["teams"] });
    void queryClient.invalidateQueries({ queryKey: ["team"] });
  }, [queryClient]);

  const deleteTeam = useMutation({
    mutationFn: (id: string) => api.delete(`/teams/${id}`),
    onSuccess: () => {
      toast.success("Team deleted");
      setDeleting(null);
      invalidate();
    },
    onError: (e) => toast.error(errMsg(e, "Couldn't delete team")),
  });

  function renderNode(team: Team, depth: number): React.ReactNode {
    const kids = childrenById.get(team.id) ?? [];
    const manageable = canAdminTeam(team);
    const count = team.member_count ?? 0;
    return (
      <div key={team.id} className="space-y-3">
        <Card className="p-4">
          <div className="flex items-start gap-3">
            <span className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-primary/10 text-primary">
              <UsersRound className="h-5 w-5" />
            </span>
            <div className="min-w-0 flex-1">
              <p className="truncate font-semibold leading-tight">{team.name}</p>
              <p className="mt-0.5 text-xs text-muted-foreground">
                {count} {count === 1 ? "member" : "members"}
                {kids.length > 0
                  ? ` · ${kids.length} sub-team${kids.length === 1 ? "" : "s"}`
                  : ""}
              </p>
              {team.description ? (
                <p className="mt-1 line-clamp-1 text-sm text-muted-foreground">
                  {team.description}
                </p>
              ) : null}
            </div>
            <div className="flex shrink-0 items-center gap-1.5">
              <Button variant="outline" size="sm" onClick={() => setManaging(team)}>
                <UserPlus className="h-4 w-4" />
                Members
              </Button>
              {manageable ? (
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button variant="ghost" size="icon" aria-label="Team actions">
                      <MoreHorizontal className="h-4 w-4" />
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="end" className="w-44">
                    <DropdownMenuLabel>Manage</DropdownMenuLabel>
                    <DropdownMenuSeparator />
                    {canCreate ? (
                      <DropdownMenuItem
                        disabled={depth >= MAX_TEAM_DEPTH}
                        onClick={() => setSubteamParent(team)}
                      >
                        <FolderPlus className="h-4 w-4" />
                        Add sub-team
                      </DropdownMenuItem>
                    ) : null}
                    <DropdownMenuItem onClick={() => setEditing(team)}>
                      <Pencil className="h-4 w-4" />
                      Edit
                    </DropdownMenuItem>
                    <DropdownMenuSeparator />
                    <DropdownMenuItem
                      className="text-destructive focus:text-destructive"
                      onClick={() => setDeleting(team)}
                    >
                      <Trash2 className="h-4 w-4" />
                      Delete
                    </DropdownMenuItem>
                  </DropdownMenuContent>
                </DropdownMenu>
              ) : null}
            </div>
          </div>
        </Card>
        {kids.length > 0 ? (
          <div className="ml-4 space-y-3 border-l pl-4">
            {kids.map((k) => renderNode(k, depth + 1))}
          </div>
        ) : null}
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Teams"
        description="Group members into a hierarchy so you can grant access to knowledge bases in one move."
        actions={
          canCreate ? (
            <Button onClick={() => setCreateOpen(true)}>
              <Plus className="h-4 w-4" />
              New team
            </Button>
          ) : null
        }
      />

      {teamsQuery.isLoading ? (
        <CardGridSkeleton count={3} />
      ) : teamsQuery.isError ? (
        <EmptyState
          icon={UsersRound}
          title="Couldn't load teams"
          description="Something went wrong fetching your teams. Try again in a moment."
        />
      ) : roots.length === 0 ? (
        <EmptyState
          icon={UsersRound}
          title="No teams yet"
          description="Create a team to bundle members together for shared access. Teams can nest into sub-teams."
          actions={
            canCreate ? (
              <Button onClick={() => setCreateOpen(true)}>
                <Plus className="h-4 w-4" />
                New team
              </Button>
            ) : null
          }
        />
      ) : (
        <div className="space-y-3">{roots.map((t) => renderNode(t, 1))}</div>
      )}

      <TeamFormDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        teams={teams}
        onSaved={invalidate}
      />
      <TeamFormDialog
        open={subteamParent !== null}
        onOpenChange={(o) => !o && setSubteamParent(null)}
        parentTeam={subteamParent}
        teams={teams}
        onSaved={invalidate}
      />
      <TeamFormDialog
        open={editing !== null}
        onOpenChange={(o) => !o && setEditing(null)}
        team={editing ?? undefined}
        teams={teams}
        onSaved={invalidate}
      />

      {managing ? (
        <ManageMembersDialog
          team={managing}
          canManage={canAdminTeam(managing)}
          open={managing !== null}
          onOpenChange={(o) => !o && setManaging(null)}
          onChanged={invalidate}
        />
      ) : null}

      <Dialog open={deleting !== null} onOpenChange={(o) => !o && setDeleting(null)}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>Delete team?</DialogTitle>
            <DialogDescription>
              Deleting <span className="font-medium">{deleting?.name}</span> removes it
              and any access grants made to it. Its sub-teams are kept and moved to the
              top level; members keep their accounts.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setDeleting(null)}
              disabled={deleteTeam.isPending}
            >
              Cancel
            </Button>
            <Button
              variant="destructive"
              disabled={deleteTeam.isPending}
              onClick={() => deleting && deleteTeam.mutate(deleting.id)}
            >
              {deleteTeam.isPending ? "Deleting…" : "Delete team"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

/**
 * Create/rename a team, and re-parent it when editing an existing one.
 *
 * The parent picker is only surfaced when editing, and its eligible destinations are any
 * team that is not the team itself and not one of its descendants (which would form a
 * cycle). `parent_team_id` is only sent when it actually changed: the backend treats a
 * present value as a re-parent and requires admin rights on the destination, so always
 * sending the current parent would 403 a sub-team lead just renaming their own team.
 */
function TeamFormDialog({
  open,
  onOpenChange,
  team,
  parentTeam,
  teams,
  onSaved,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  team?: Team;
  parentTeam?: Team | null;
  teams: Team[];
  onSaved: () => void;
}) {
  const editing = Boolean(team);
  const [name, setName] = React.useState("");
  const [description, setDescription] = React.useState("");
  const [parentId, setParentId] = React.useState("");

  React.useEffect(() => {
    if (open) {
      setName(team?.name ?? "");
      setDescription(team?.description ?? "");
      setParentId(team?.parent_team_id ?? "");
    }
  }, [open, team]);

  const parentOptions = React.useMemo(() => {
    if (!team) return [];
    const blocked = descendantsOf(team.id, teams);
    return teams.filter((t) => t.id !== team.id && !blocked.has(t.id));
  }, [team, teams]);

  const save = useMutation({
    mutationFn: () => {
      const base = {
        name: name.trim(),
        description: description.trim() || null,
      };
      if (editing) {
        const currentParent = team!.parent_team_id ?? "";
        const body: Record<string, unknown> = { ...base };
        if (parentId !== currentParent) body.parent_team_id = parentId || null;
        return api.patch<Team>(`/teams/${team!.id}`, body);
      }
      return api.post<Team>("/teams", {
        ...base,
        parent_team_id: parentTeam?.id ?? null,
      });
    },
    onSuccess: () => {
      toast.success(
        editing ? "Team updated" : parentTeam ? "Sub-team created" : "Team created",
      );
      onOpenChange(false);
      onSaved();
    },
    onError: (e) => toast.error(errMsg(e, "Couldn't save team")),
  });

  function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim()) return;
    save.mutate();
  }

  const title = editing ? "Edit team" : parentTeam ? "New sub-team" : "New team";

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-md">
        <form onSubmit={submit}>
          <DialogHeader>
            <DialogTitle>{title}</DialogTitle>
            <DialogDescription>
              {parentTeam
                ? `A sub-team of ${parentTeam.name}. It can be granted its own access to knowledge bases.`
                : "Teams can be granted access to knowledge bases and documents."}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-4">
            <div className="space-y-1.5">
              <Label htmlFor="team-name">Name</Label>
              <Input
                id="team-name"
                required
                placeholder="Engineering"
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="team-description">Description</Label>
              <Textarea
                id="team-description"
                placeholder="What this team is for (optional)."
                value={description}
                onChange={(e) => setDescription(e.target.value)}
              />
            </div>
            {editing ? (
              <div className="space-y-1.5">
                <Label htmlFor="team-parent">Parent team</Label>
                <Select
                  value={parentId || ROOT_PARENT}
                  onValueChange={(v) => setParentId(v === ROOT_PARENT ? "" : v)}
                >
                  <SelectTrigger id="team-parent">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value={ROOT_PARENT}>No parent (top level)</SelectItem>
                    {parentOptions.map((t) => (
                      <SelectItem key={t.id} value={t.id}>
                        {t.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            ) : parentTeam ? (
              <div className="space-y-1.5">
                <Label>Parent team</Label>
                <Input value={parentTeam.name} disabled readOnly />
              </div>
            ) : null}
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
              {save.isPending
                ? "Saving…"
                : editing
                  ? "Save changes"
                  : parentTeam
                    ? "Create sub-team"
                    : "Create team"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

/**
 * Team roster dialog - add, remove and re-role members.
 *
 * Org members are only listable by admins, so the directory query is gated on that: a
 * team lead can still manage the roster they can already see but cannot browse the full
 * directory to add from.
 */
function ManageMembersDialog({
  team,
  canManage,
  open,
  onOpenChange,
  onChanged,
}: {
  team: Team;
  canManage: boolean;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onChanged: () => void;
}) {
  const { role, user } = useAuth();
  const canListOrg = isOrgAdmin(role);
  const queryClient = useQueryClient();
  const [toAdd, setToAdd] = React.useState<string>("");

  const detailKey = ["team", team.id];
  const detailQuery = useQuery<TeamDetail>({
    queryKey: detailKey,
    queryFn: () => api.get<TeamDetail>(`/teams/${team.id}`),
    enabled: open,
  });

  const orgMembersQuery = useQuery<Membership[]>({
    queryKey: ["org-members"],
    queryFn: () => api.get<Membership[]>("/orgs/members"),
    enabled: open && canManage && canListOrg,
  });

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: detailKey });
    onChanged();
  };

  const addMember = useMutation({
    mutationFn: (userId: string) =>
      api.post<TeamDetail>(`/teams/${team.id}/members`, { user_id: userId }),
    onSuccess: () => {
      setToAdd("");
      refresh();
    },
    onError: (e) => toast.error(errMsg(e, "Couldn't add member")),
  });

  const setRole = useMutation({
    mutationFn: ({ userId, role: next }: { userId: string; role: TeamRole }) =>
      api.patch<TeamDetail>(`/teams/${team.id}/members/${userId}`, {
        role: next,
      }),
    onSuccess: () => {
      toast.success("Role updated");
      refresh();
    },
    onError: (e) => toast.error(errMsg(e, "Couldn't update role")),
  });

  const removeMember = useMutation({
    mutationFn: (userId: string) => api.delete(`/teams/${team.id}/members/${userId}`),
    onSuccess: refresh,
    onError: (e) => toast.error(errMsg(e, "Couldn't remove member")),
  });

  const members = detailQuery.data?.members ?? [];
  const memberIds = new Set(members.map((m) => m.user_id));
  const addable = (orgMembersQuery.data ?? []).filter((m) => !memberIds.has(m.user_id));
  const busy = setRole.isPending || removeMember.isPending;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>{team.name} · members</DialogTitle>
          <DialogDescription>
            Add or remove members and choose who leads this team.
          </DialogDescription>
        </DialogHeader>

        {canManage ? (
          canListOrg ? (
            <div className="flex items-end gap-2">
              <div className="flex-1 space-y-1.5">
                <Label htmlFor="add-member">Add member</Label>
                <Select value={toAdd} onValueChange={setToAdd}>
                  <SelectTrigger id="add-member">
                    <SelectValue placeholder="Select a member" />
                  </SelectTrigger>
                  <SelectContent>
                    {addable.length === 0 ? (
                      <div className="px-2 py-1.5 text-sm text-muted-foreground">
                        Everyone is already on this team.
                      </div>
                    ) : (
                      addable.map((m) => (
                        <SelectItem key={m.user_id} value={m.user_id}>
                          {m.user?.full_name || m.user?.email || m.user_id}
                        </SelectItem>
                      ))
                    )}
                  </SelectContent>
                </Select>
              </div>
              <Button
                onClick={() => toAdd && addMember.mutate(toAdd)}
                disabled={!toAdd || addMember.isPending}
              >
                <UserPlus className="h-4 w-4" />
                Add
              </Button>
            </div>
          ) : (
            <p className="rounded-md border border-dashed bg-muted/30 px-3 py-2 text-xs text-muted-foreground">
              Adding members requires organization admin access.
            </p>
          )
        ) : null}

        <div className="max-h-72 space-y-1 overflow-y-auto">
          {detailQuery.isLoading ? (
            <p className="py-6 text-center text-sm text-muted-foreground">
              Loading members…
            </p>
          ) : members.length === 0 ? (
            <p className="py-6 text-center text-sm text-muted-foreground">
              This team has no members yet.
            </p>
          ) : (
            members.map((m) => {
              const isLead = m.role === "lead";
              const isSelf = m.user_id === user?.id;
              return (
                <div
                  key={m.id}
                  className="flex items-center gap-3 rounded-md px-2 py-1.5 hover:bg-accent"
                >
                  <Avatar className="h-8 w-8">
                    {m.user?.avatar_url ? (
                      <AvatarImage src={m.user.avatar_url} alt={m.user.full_name ?? ""} />
                    ) : null}
                    <AvatarFallback>
                      {initials(m.user?.full_name || m.user?.email)}
                    </AvatarFallback>
                  </Avatar>
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium">
                      {m.user?.full_name || "-"}
                      {isSelf ? (
                        <span className="ml-1.5 text-xs font-normal text-muted-foreground">
                          (you)
                        </span>
                      ) : null}
                    </p>
                    <p className="truncate text-xs text-muted-foreground">
                      {m.user?.email}
                    </p>
                  </div>
                  {isLead ? (
                    <Badge variant="info" className="gap-1">
                      <Crown className="h-3 w-3" />
                      Lead
                    </Badge>
                  ) : null}
                  {canManage ? (
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <Button
                          variant="ghost"
                          size="icon"
                          aria-label="Member actions"
                          disabled={busy}
                        >
                          <MoreHorizontal className="h-4 w-4" />
                        </Button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end" className="w-44">
                        <DropdownMenuLabel>Role</DropdownMenuLabel>
                        <DropdownMenuSeparator />
                        {isLead ? (
                          <DropdownMenuItem
                            onClick={() =>
                              setRole.mutate({
                                userId: m.user_id,
                                role: "member",
                              })
                            }
                          >
                            <UserRound className="h-4 w-4" />
                            Set as member
                          </DropdownMenuItem>
                        ) : (
                          <DropdownMenuItem
                            onClick={() =>
                              setRole.mutate({
                                userId: m.user_id,
                                role: "lead",
                              })
                            }
                          >
                            <Crown className="h-4 w-4" />
                            Set as lead
                          </DropdownMenuItem>
                        )}
                        <DropdownMenuSeparator />
                        <DropdownMenuItem
                          className="text-destructive focus:text-destructive"
                          onClick={() => removeMember.mutate(m.user_id)}
                        >
                          <X className="h-4 w-4" />
                          Remove from team
                        </DropdownMenuItem>
                      </DropdownMenuContent>
                    </DropdownMenu>
                  ) : null}
                </div>
              );
            })
          )}
        </div>

        <DialogFooter>
          <Badge variant="muted" className="mr-auto">
            {members.length} {members.length === 1 ? "member" : "members"}
          </Badge>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Done
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
