"use client";

import * as React from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  BarChart3,
  Boxes,
  DatabaseZap,
  FileText,
  Fingerprint,
  KeyRound,
  LayoutDashboard,
  Library,
  Lightbulb,
  MessageSquareText,
  Plug,
  ScrollText,
  Settings,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  UserPlus,
  Users,
  UsersRound,
  Waypoints,
  type LucideIcon,
} from "lucide-react";

import { LogoMark } from "@/components/brand/logo";
import { ScrollArea } from "@/components/ui/scroll-area";
import { cn } from "@/lib/utils";

export interface NavItem {
  label: string;
  href: string;
  icon: LucideIcon;
}

export interface NavGroup {
  label: string;
  items: NavItem[];
}

/**
 * Canonical dashboard navigation. Kept in one place so the sidebar and the
 * topbar (breadcrumb / page title) agree on routes and labels.
 */
export const NAV_GROUPS: NavGroup[] = [
  {
    label: "Knowledge",
    items: [
      { label: "Overview", href: "/dashboard", icon: LayoutDashboard },
      { label: "Ask", href: "/dashboard/search", icon: Sparkles },
      { label: "Knowledge Bases", href: "/dashboard/collections", icon: Library },
      { label: "Documents", href: "/dashboard/documents", icon: FileText },
      { label: "Data Sources", href: "/dashboard/data-sources", icon: DatabaseZap },
      { label: "Answers", href: "/dashboard/answers", icon: MessageSquareText },
      { label: "Entities", href: "/dashboard/entities", icon: Boxes },
      { label: "Graph", href: "/dashboard/graph", icon: Waypoints },
    ],
  },
  {
    label: "Governance",
    items: [
      { label: "Members", href: "/dashboard/members", icon: Users },
      { label: "Invites", href: "/dashboard/invites", icon: UserPlus },
      { label: "Teams", href: "/dashboard/teams", icon: UsersRound },
      { label: "Access", href: "/dashboard/permissions", icon: ShieldCheck },
      { label: "Oversharing", href: "/dashboard/governance", icon: ShieldAlert },
      { label: "Audit Log", href: "/dashboard/audit", icon: ScrollText },
    ],
  },
  {
    label: "Platform",
    items: [
      { label: "Connectors", href: "/dashboard/connectors", icon: Plug },
      { label: "SSO & SCIM", href: "/dashboard/sso", icon: Fingerprint },
      { label: "API Keys", href: "/dashboard/api-keys", icon: KeyRound },
      { label: "Knowledge Gaps", href: "/dashboard/knowledge-gaps", icon: Lightbulb },
      { label: "Usage", href: "/dashboard/analytics", icon: BarChart3 },
      { label: "Settings", href: "/dashboard/settings", icon: Settings },
    ],
  },
];

const ALL_ITEMS = NAV_GROUPS.flatMap((g) => g.items);

/** True when `href` is the active route for `pathname`. */
export function isActiveRoute(pathname: string, href: string): boolean {
  if (href === "/dashboard") return pathname === "/dashboard";
  return pathname === href || pathname.startsWith(`${href}/`);
}

/**
 * Resolve the nav item that best matches the current pathname, preferring the
 * most specific (longest) matching href.
 */
export function findNavItem(pathname: string): NavItem | undefined {
  return [...ALL_ITEMS]
    .sort((a, b) => b.href.length - a.href.length)
    .find((item) => isActiveRoute(pathname, item.href));
}

function SidebarLink({
  item,
  active,
  onNavigate,
}: {
  item: NavItem;
  active: boolean;
  onNavigate?: () => void;
}) {
  const Icon = item.icon;
  return (
    <Link
      href={item.href}
      onClick={onNavigate}
      aria-current={active ? "page" : undefined}
      className={cn(
        "group flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors",
        active
          ? "bg-primary/10 text-primary"
          : "text-muted-foreground hover:bg-accent hover:text-accent-foreground",
      )}
    >
      <Icon
        className={cn(
          "h-4 w-4 shrink-0 transition-colors",
          active
            ? "text-primary"
            : "text-muted-foreground group-hover:text-accent-foreground",
        )}
      />
      <span className="truncate">{item.label}</span>
    </Link>
  );
}

/** Brand lockup shown at the top of the sidebar. */
export function SidebarBrand({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <Link
      href="/dashboard"
      onClick={onNavigate}
      className="flex items-center gap-2.5 px-2 py-1"
    >
      <span className="flex h-8 w-8 items-center justify-center text-foreground">
        <LogoMark className="h-7 w-7" />
      </span>
      <span className="text-[15px] font-semibold tracking-tight">Third Brain</span>
    </Link>
  );
}

/**
 * The primary dashboard navigation. Renders in the desktop rail and, wrapped in
 * a Sheet, on mobile. `onNavigate` lets the mobile drawer close on selection.
 */
export function Sidebar({
  className,
  onNavigate,
}: {
  className?: string;
  onNavigate?: () => void;
}) {
  const pathname = usePathname() || "/dashboard";

  return (
    <div className={cn("flex h-full flex-col gap-2", className)}>
      <div className="flex h-14 items-center border-b px-4">
        <SidebarBrand onNavigate={onNavigate} />
      </div>
      <ScrollArea className="flex-1 px-3">
        <nav className="flex flex-col gap-6 py-3">
          {NAV_GROUPS.map((group) => (
            <div key={group.label} className="flex flex-col gap-1">
              <p className="px-3 pb-1 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground/70">
                {group.label}
              </p>
              {group.items.map((item) => (
                <SidebarLink
                  key={item.href}
                  item={item}
                  active={isActiveRoute(pathname, item.href)}
                  onNavigate={onNavigate}
                />
              ))}
            </div>
          ))}
        </nav>
      </ScrollArea>
      <div className="border-t px-4 py-3">
        <p className="text-xs text-muted-foreground">The governed knowledge layer</p>
        <p
          className="mt-0.5 text-[11px] tabular-nums text-muted-foreground/60"
          title={
            process.env.NEXT_PUBLIC_GIT_COMMIT
              ? process.env.NEXT_PUBLIC_GIT_COMMIT.slice(0, 12)
              : undefined
          }
        >
          v{process.env.NEXT_PUBLIC_APP_VERSION}
        </p>
      </div>
    </div>
  );
}
