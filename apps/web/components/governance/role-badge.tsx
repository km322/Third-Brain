import * as React from "react";

import { Badge, type BadgeProps } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { MembershipStatus, OrgRole } from "@/lib/types";

/**
 * Governance role helpers + badges. Colocated here so the Members, Teams,
 * Access, API-key and Settings pages all agree on how org roles map to colors
 * and how to gate admin-only UI without re-deriving the role hierarchy.
 */

/** Least → most privileged. Mirrors `OrgRole` in the backend enums. */
const ROLE_ORDER: OrgRole[] = ["viewer", "editor", "admin", "owner"];

/** True when `role` is at least as privileged as `min`. */
export function orgRoleAtLeast(role: OrgRole | null | undefined, min: OrgRole): boolean {
  if (!role) return false;
  return ROLE_ORDER.indexOf(role) >= ROLE_ORDER.indexOf(min);
}

/** Owner or admin - the roles the backend treats as org administrators. */
export function isOrgAdmin(role: OrgRole | null | undefined): boolean {
  return orgRoleAtLeast(role, "admin");
}

/**
 * Short, plain-language explanation of what each org role can see and do.
 * Surfaced wherever a role is picked (invite dialog, per-member dropdown) so
 * admins understand that admin sees everything and editor manages its own.
 */
export const ROLE_DESCRIPTIONS: Record<OrgRole, string> = {
  owner:
    "Full control of the workspace, including settings, teams, and deleting the organization.",
  admin: "Sees and manages every document across the whole organization.",
  editor:
    "Can create and manage their own documents and knowledge bases; sees what is shared or granted to them.",
  viewer: "Can read and search, but cannot create or change content.",
};

const ROLE_VARIANT: Record<OrgRole, BadgeProps["variant"]> = {
  owner: "default",
  admin: "info",
  editor: "secondary",
  viewer: "muted",
};

/** Colored badge for an organization role. */
export function RoleBadge({ role, className }: { role: OrgRole; className?: string }) {
  return (
    <Badge
      variant={ROLE_VARIANT[role] ?? "muted"}
      className={cn("capitalize", className)}
    >
      {role}
    </Badge>
  );
}

const STATUS_VARIANT: Record<MembershipStatus, BadgeProps["variant"]> = {
  active: "success",
  invited: "warning",
  suspended: "destructive",
};

/** Colored badge for a membership status (active / invited / suspended). */
export function MembershipStatusBadge({
  status,
  className,
}: {
  status: MembershipStatus;
  className?: string;
}) {
  return (
    <Badge
      variant={STATUS_VARIANT[status] ?? "muted"}
      className={cn("capitalize", className)}
    >
      {status}
    </Badge>
  );
}
