"use client";

import * as React from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
  Check,
  ChevronsUpDown,
  LogOut,
  Menu,
  Plus,
  Search,
  Settings,
  UserRound,
} from "lucide-react";

import { toast } from "sonner";

import { ApiError } from "@/lib/api";
import { ModeBadge } from "@/components/mode-badge";
import { ThemeToggle } from "@/components/theme-toggle";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { findNavItem } from "@/components/dashboard/sidebar";
import { useAuth } from "@/lib/auth-context";
import { cn, initials } from "@/lib/utils";

/** Organization switcher - lists the user's orgs and mints new tokens on switch. */
function OrgSwitcher() {
  const { org, orgs, switchOrg } = useAuth();
  const router = useRouter();
  const [pending, setPending] = React.useState<string | null>(null);

  async function handleSwitch(orgId: string) {
    if (orgId === org?.id) return;
    setPending(orgId);
    try {
      await switchOrg(orgId);
    } catch (e) {
      // e.g. a suspended membership 403s - surface it instead of failing silently.
      toast.error(
        e instanceof ApiError ? e.message : "Couldn't switch organization",
      );
    } finally {
      setPending(null);
    }
  }

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="outline"
          className="h-9 max-w-[220px] justify-between gap-2 px-3"
        >
          <span className="flex min-w-0 items-center gap-2">
            <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded bg-primary/10 text-[10px] font-bold text-primary">
              {initials(org?.name)}
            </span>
            <span className="truncate text-sm font-medium">
              {org?.name ?? "Select org"}
            </span>
          </span>
          <ChevronsUpDown className="h-4 w-4 shrink-0 text-muted-foreground" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="w-[240px]">
        <DropdownMenuLabel className="text-xs text-muted-foreground">
          Organizations
        </DropdownMenuLabel>
        {orgs.map((o) => (
          <DropdownMenuItem
            key={o.id}
            className="cursor-pointer"
            disabled={pending !== null}
            onClick={() => handleSwitch(o.id)}
          >
            <span className="flex h-5 w-5 items-center justify-center rounded bg-muted text-[10px] font-bold">
              {initials(o.name)}
            </span>
            <span className="truncate">{o.name}</span>
            {o.id === org?.id && (
              <Check className="ml-auto h-4 w-4 text-primary" />
            )}
          </DropdownMenuItem>
        ))}
        <DropdownMenuSeparator />
        <DropdownMenuItem
          className="cursor-pointer"
          onClick={() => router.push("/dashboard/settings")}
        >
          <Plus className="h-4 w-4" />
          New organization
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

/** Compact search affordance that routes to the Ask page. */
function SearchHint() {
  return (
    <Link
      href="/dashboard/search"
      className="group hidden h-9 items-center gap-2 rounded-md border bg-muted/40 px-3 text-sm text-muted-foreground transition-colors hover:bg-muted md:flex"
    >
      <Search className="h-4 w-4" />
      <span>Ask your knowledge…</span>
      <kbd className="ml-2 hidden rounded border bg-background px-1.5 font-mono text-[10px] font-medium text-muted-foreground lg:inline-block">
        /search
      </kbd>
    </Link>
  );
}

/** Avatar dropdown with profile, settings and sign-out. */
function UserMenu() {
  const { user, role, logout } = useAuth();
  const router = useRouter();

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          variant="ghost"
          className="h-9 gap-2 px-1.5"
          aria-label="Account menu"
        >
          <Avatar className="h-7 w-7">
            {user?.avatar_url ? (
              <AvatarImage src={user.avatar_url} alt={user.full_name ?? ""} />
            ) : null}
            <AvatarFallback>{initials(user?.full_name || user?.email)}</AvatarFallback>
          </Avatar>
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-60">
        <div className="flex items-center gap-3 px-2 py-1.5">
          <Avatar className="h-9 w-9">
            {user?.avatar_url ? (
              <AvatarImage src={user.avatar_url} alt={user.full_name ?? ""} />
            ) : null}
            <AvatarFallback>{initials(user?.full_name || user?.email)}</AvatarFallback>
          </Avatar>
          <div className="min-w-0">
            <p className="truncate text-sm font-medium">
              {user?.full_name || "-"}
            </p>
            <p className="truncate text-xs text-muted-foreground">
              {user?.email}
            </p>
          </div>
        </div>
        {role && (
          <p className="px-2 pb-1 text-xs capitalize text-muted-foreground">
            Role: {role}
          </p>
        )}
        <DropdownMenuSeparator />
        <DropdownMenuItem
          className="cursor-pointer"
          onClick={() => router.push("/dashboard/settings")}
        >
          <UserRound className="h-4 w-4" />
          Profile
        </DropdownMenuItem>
        <DropdownMenuItem
          className="cursor-pointer"
          onClick={() => router.push("/dashboard/settings")}
        >
          <Settings className="h-4 w-4" />
          Settings
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem
          className="cursor-pointer text-destructive focus:text-destructive"
          onClick={() => void logout()}
        >
          <LogOut className="h-4 w-4" />
          Sign out
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

/**
 * Sticky dashboard header: mobile menu trigger, current-page title, org
 * switcher, search hint, environment badge, theme toggle and the user menu.
 */
export function Topbar({ onMenuClick }: { onMenuClick?: () => void }) {
  const pathname = usePathname() || "/dashboard";
  const active = findNavItem(pathname);

  return (
    <header className="sticky top-0 z-30 flex h-14 items-center gap-2 border-b bg-background/80 px-4 backdrop-blur supports-[backdrop-filter]:bg-background/60">
      <Button
        variant="ghost"
        size="icon"
        className="lg:hidden"
        onClick={onMenuClick}
        aria-label="Open navigation"
      >
        <Menu className="h-5 w-5" />
      </Button>

      <div className="flex min-w-0 items-center gap-3">
        <span className="hidden truncate text-sm font-semibold lg:inline">
          {active?.label ?? "Dashboard"}
        </span>
        <span className="hidden h-5 w-px bg-border lg:inline-block" />
        <OrgSwitcher />
      </div>

      <div className={cn("ml-auto flex items-center gap-1.5 sm:gap-2")}>
        <SearchHint />
        <ModeBadge className="hidden sm:inline-flex" />
        <ThemeToggle />
        <UserMenu />
      </div>
    </header>
  );
}
