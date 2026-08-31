"use client";

import * as React from "react";
import Link from "next/link";
import { formatDistanceToNow } from "date-fns";
import {
  Activity,
  AlertCircle,
  ArrowRight,
  Bot,
  Coins,
  DollarSign,
  FileText,
  KeyRound,
  Library,
  ScrollText,
  Search,
  Users,
} from "lucide-react";

import { AreaUsageChart } from "@/components/dashboard/charts/lazy";
import { EmptyState } from "@/components/dashboard/empty-state";
import { PageHeader } from "@/components/dashboard/page-header";
import { StatCard } from "@/components/dashboard/stat-card";
import { orgRoleAtLeast } from "@/components/governance/role-badge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useAuth } from "@/lib/auth-context";
import { useApiQuery } from "@/lib/hooks";
import type {
  AuditLogEntry,
  Collection,
  DocumentItem,
  Page,
  UsageSummary,
} from "@/lib/types";
import { formatCurrency, formatNumber } from "@/lib/utils";

/** Shape of `GET /analytics/overview` (see app/schemas/analytics.py::AnalyticsOverview). */
interface OverviewStats {
  documents: number;
  collections: number;
  members: number;
  api_keys: number;
  searches_7d: number;
  tokens_30d: number;
  cost_30d: number;
}

/** "document.create" / "api_key.revoke" -> "Document create" / "Api key revoke". */
function humanizeAction(action: string): string {
  const words = action.replace(/[._]/g, " ").trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

export default function OverviewPage() {
  const { user, org, role } = useAuth();
  const isAdmin = role === "admin" || role === "owner";

  const overview = useApiQuery<OverviewStats>(
    ["analytics", "overview"],
    "/analytics/overview",
  );
  const usage = useApiQuery<UsageSummary>(
    ["analytics", "usage", 30],
    "/analytics/usage",
    { days: 30 },
  );
  // The audit log is admin/owner-only; don't fire the request for other roles.
  const audit = useApiQuery<Page<AuditLogEntry>>(
    ["analytics", "audit", 1, 8],
    "/analytics/audit",
    { page: 1, page_size: 8 },
    { enabled: isAdmin, retry: false },
  );
  // Documents connected agents wrote via MCP. Uses the ACL-scoped documents endpoint, so
  // it's safe for all roles (unlike the admin-only audit log above).
  const agentDocs = useApiQuery<Page<DocumentItem>>(
    ["documents", "via", "mcp"],
    "/documents",
    { via: "mcp", page_size: 5 },
  );
  // Resolve collection names for the agent-docs list context (best-effort; the list still
  // renders if this hasn't loaded).
  const collections = useApiQuery<Collection[]>(["collections"], "/collections");

  const stats = overview.data;
  const loading = overview.isLoading;
  const firstName = user?.full_name?.split(" ")[0];
  // A brand-new workspace has no knowledge yet: greet as a first visit and point at
  // the core loop (create a knowledge base) instead of a bare all-zeros dashboard.
  const isFirstRun = !!stats && stats.documents === 0 && stats.collections === 0;
  const canCreate = orgRoleAtLeast(role, "editor");
  const greeting = firstName
    ? isFirstRun
      ? `Welcome to Third Brain, ${firstName}`
      : `Welcome back, ${firstName}`
    : "Overview";
  // Strip any trailing period on the org name so a name like "Acme Inc." doesn't
  // render a double period in the sentence below.
  const orgName = org?.name?.replace(/\.\s*$/, "");

  const cards = [
    {
      label: "Documents",
      value: stats ? formatNumber(stats.documents) : "-",
      icon: FileText,
    },
    {
      label: "Knowledge bases",
      value: stats ? formatNumber(stats.collections) : "-",
      icon: Library,
    },
    {
      label: "Members",
      value: stats ? formatNumber(stats.members) : "-",
      icon: Users,
    },
    {
      label: "Active API keys",
      value: stats ? formatNumber(stats.api_keys) : "-",
      icon: KeyRound,
    },
    {
      label: "Searches",
      value: stats ? formatNumber(stats.searches_7d) : "-",
      icon: Search,
      hint: "last 7 days",
    },
    {
      label: "Tokens",
      value: stats ? formatNumber(stats.tokens_30d) : "-",
      icon: Coins,
      hint: "last 30 days",
    },
    {
      label: "Estimated provider cost",
      value: stats ? formatCurrency(stats.cost_30d) : "-",
      icon: DollarSign,
      hint: "last 30 days",
    },
  ];

  return (
    <div className="space-y-6">
      <PageHeader
        title={greeting}
        description={
          orgName
            ? `Here's what's happening across ${orgName}.`
            : "Your organization's knowledge at a glance."
        }
      />

      {isFirstRun ? (
        <Card className="flex flex-col items-start gap-4 border-primary/30 bg-primary/5 p-6 sm:flex-row sm:items-center sm:justify-between">
          <div className="space-y-1">
            <h3 className="text-base font-semibold">
              {canCreate ? "Set up your first knowledge base" : "No knowledge yet"}
            </h3>
            <p className="text-sm text-muted-foreground">
              {canCreate
                ? "Create a knowledge base and add a few documents - then ask questions and get grounded, cited answers."
                : "Once an admin or editor adds documents, they'll appear here and you can search and ask across them."}
            </p>
          </div>
          {canCreate ? (
            <Button asChild className="shrink-0">
              <Link href="/dashboard/collections">
                Create a knowledge base
                <ArrowRight className="h-4 w-4" />
              </Link>
            </Button>
          ) : null}
        </Card>
      ) : null}

      {overview.isError ? (
        <Card className="flex items-center gap-3 border-destructive/40 p-4 text-sm text-destructive">
          <AlertCircle className="h-5 w-5 shrink-0" />
          <span>Couldn&apos;t load overview metrics. Please try again.</span>
        </Card>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {cards.map((c) => (
            <StatCard
              key={c.label}
              label={c.label}
              value={c.value}
              icon={c.icon}
              hint={c.hint}
              loading={loading}
            />
          ))}
        </div>
      )}

      <div className="grid gap-6 xl:grid-cols-3">
        <div className="flex flex-col xl:col-span-2">
          <AreaUsageChart
            className="flex-1"
            title="Usage over time"
            description="Requests, tokens and cost over the last 30 days"
            data={usage.data?.by_day ?? []}
            loading={usage.isLoading}
          />
        </div>

        <Card className="flex flex-col p-5 xl:col-span-1">
          <div className="mb-3 flex items-center justify-between gap-2">
            <div className="space-y-0.5">
              <h3 className="text-sm font-semibold tracking-tight">Recent activity</h3>
              <p className="text-xs text-muted-foreground">
                Latest changes in your workspace
              </p>
            </div>
            {isAdmin ? (
              <Link
                href="/dashboard/audit"
                className="inline-flex items-center gap-1 text-xs font-medium text-primary hover:underline"
              >
                View all
                <ArrowRight className="h-3.5 w-3.5" />
              </Link>
            ) : null}
          </div>

          <RecentActivity
            isAdmin={isAdmin}
            loading={audit.isLoading}
            entries={audit.data?.items ?? []}
          />
        </Card>

        <Card className="flex flex-col p-5 xl:col-span-3">
          <div className="mb-3 flex items-center justify-between gap-2">
            <div className="space-y-0.5">
              <h3 className="text-sm font-semibold tracking-tight">Written by agents</h3>
              <p className="text-xs text-muted-foreground">
                Documentation your connected agents captured as they worked
              </p>
            </div>
            <Link
              href="/dashboard/documents?via=mcp"
              className="inline-flex items-center gap-1 text-xs font-medium text-primary hover:underline"
            >
              View all
              <ArrowRight className="h-3.5 w-3.5" />
            </Link>
          </div>

          <AgentDocs
            loading={agentDocs.isLoading}
            error={agentDocs.isError}
            docs={agentDocs.data?.items ?? []}
            collections={collections.data ?? []}
          />
        </Card>
      </div>
    </div>
  );
}

function AgentDocs({
  loading,
  error,
  docs,
  collections,
}: {
  loading: boolean;
  error: boolean;
  docs: DocumentItem[];
  collections: Collection[];
}) {
  if (loading) {
    return (
      <ul className="flex-1 space-y-3">
        {Array.from({ length: 3 }).map((_, i) => (
          <li key={i} className="flex items-center gap-3">
            <Skeleton className="h-8 w-8 shrink-0 rounded-full" />
            <div className="flex-1 space-y-1.5">
              <Skeleton className="h-3.5 w-2/3" />
              <Skeleton className="h-3 w-1/3" />
            </div>
          </li>
        ))}
      </ul>
    );
  }

  // A fetch failure must not masquerade as the "no agent docs yet" onboarding state.
  if (error) {
    return (
      <div className="flex flex-1 items-center gap-2 text-sm text-destructive">
        <AlertCircle className="h-4 w-4 shrink-0" />
        <span>Couldn&apos;t load agent-written docs. Please try again.</span>
      </div>
    );
  }

  if (docs.length === 0) {
    return (
      <div className="flex flex-1 items-center">
        <EmptyState
          icon={Bot}
          title="No agent-written docs yet"
          description="Connect an agent with npx third-brain-mcp connect and it will capture decisions and answers here as it works."
          compact
          className="w-full"
        />
      </div>
    );
  }

  const nameById = new Map(collections.map((c) => [c.id, c.name]));

  return (
    <ul className="-mx-1 flex-1 divide-y">
      {docs.map((doc) => (
        <li key={doc.id}>
          <Link
            href={`/dashboard/documents?doc=${doc.id}`}
            className="flex items-center gap-3 rounded-md px-1 py-2.5 hover:bg-muted/50"
          >
            <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary">
              <Bot className="h-4 w-4" />
            </span>
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-medium">{doc.title}</p>
              <p className="truncate text-xs text-muted-foreground">
                {nameById.get(doc.collection_id) ?? "Knowledge base"}
              </p>
            </div>
            <time
              className="shrink-0 text-xs text-muted-foreground"
              dateTime={doc.created_at}
              title={new Date(doc.created_at).toLocaleString()}
            >
              {formatDistanceToNow(new Date(doc.created_at), {
                addSuffix: true,
              })}
            </time>
          </Link>
        </li>
      ))}
    </ul>
  );
}

function RecentActivity({
  isAdmin,
  loading,
  entries,
}: {
  isAdmin: boolean;
  loading: boolean;
  entries: AuditLogEntry[];
}) {
  if (!isAdmin) {
    return (
      <div className="flex flex-1 items-center">
        <EmptyState
          icon={ScrollText}
          title="Restricted"
          description="Only admins and owners can view the activity log."
          compact
          className="w-full"
        />
      </div>
    );
  }

  if (loading) {
    return (
      <ul className="flex-1 space-y-3">
        {Array.from({ length: 6 }).map((_, i) => (
          <li key={i} className="flex items-center gap-3">
            <Skeleton className="h-8 w-8 shrink-0 rounded-full" />
            <div className="flex-1 space-y-1.5">
              <Skeleton className="h-3.5 w-2/3" />
              <Skeleton className="h-3 w-1/3" />
            </div>
          </li>
        ))}
      </ul>
    );
  }

  if (entries.length === 0) {
    return (
      <div className="flex flex-1 items-center">
        <EmptyState
          icon={Activity}
          title="No activity yet"
          description="Actions like creating documents or inviting members will show up here."
          compact
          className="w-full"
        />
      </div>
    );
  }

  return (
    <ul className="-mx-1 flex-1 divide-y">
      {entries.map((entry) => (
        <li key={entry.id} className="flex items-center gap-3 px-1 py-2.5">
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary">
            <ScrollText className="h-4 w-4" />
          </span>
          <div className="min-w-0 flex-1">
            <p className="flex items-center gap-2 text-sm font-medium">
              <span className="truncate">{humanizeAction(entry.action)}</span>
              {entry.resource_type ? (
                <Badge variant="muted" className="shrink-0 capitalize">
                  {entry.resource_type.replace(/_/g, " ")}
                </Badge>
              ) : null}
            </p>
            <p className="truncate text-xs text-muted-foreground">
              {entry.actor_email ?? "System"}
            </p>
          </div>
          <time
            className="shrink-0 text-xs text-muted-foreground"
            dateTime={entry.created_at}
            title={new Date(entry.created_at).toLocaleString()}
          >
            {formatDistanceToNow(new Date(entry.created_at), {
              addSuffix: true,
            })}
          </time>
        </li>
      ))}
    </ul>
  );
}
