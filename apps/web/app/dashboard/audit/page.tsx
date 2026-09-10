"use client";

import * as React from "react";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight, Lock, ScrollText } from "lucide-react";

import { DataTable, type Column } from "@/components/dashboard/data-table";
import { EmptyState } from "@/components/dashboard/empty-state";
import { PageHeader } from "@/components/dashboard/page-header";
import { isOrgAdmin } from "@/components/governance/role-badge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import type { AuditLogEntry, Page } from "@/lib/types";
import { formatDate } from "@/lib/utils";

const PAGE_SIZE = 20;
const ALL_ACTIONS = "__all__";

/** Color a family of audit actions (created/updated/deleted/…) consistently. */
function actionVariant(action: string): "success" | "info" | "destructive" | "muted" {
  if (/(delete|revoke|remove)/.test(action)) return "destructive";
  if (/(create|grant|add|invite)/.test(action)) return "success";
  if (/(update|switch|login)/.test(action)) return "info";
  return "muted";
}

/**
 * Audit log - a record of sensitive actions across the org, for owners and admins.
 *
 * The server has no action filter, so the action dropdown and the search box refine the
 * current page client-side.
 */
export default function AuditPage() {
  const { role } = useAuth();
  const admin = isOrgAdmin(role);

  const [page, setPage] = React.useState(1);
  const [actionFilter, setActionFilter] = React.useState(ALL_ACTIONS);
  const [search, setSearch] = React.useState("");

  const logQuery = useQuery<Page<AuditLogEntry>>({
    queryKey: ["audit", page],
    queryFn: () =>
      api.get<Page<AuditLogEntry>>("/analytics/audit", {
        page,
        page_size: PAGE_SIZE,
      }),
    enabled: admin,
    placeholderData: keepPreviousData,
  });

  if (!admin) {
    return (
      <div className="space-y-6">
        <PageHeader
          title="Audit Log"
          description="A record of sensitive actions across your organization."
        />
        <EmptyState
          icon={Lock}
          title="Admin access required"
          description="Only organization owners and admins can view the audit log."
        />
      </div>
    );
  }

  const items = logQuery.data?.items ?? [];
  const total = logQuery.data?.total ?? 0;
  const pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));

  const actionsOnPage = Array.from(new Set(items.map((i) => i.action))).sort();
  const filtered = items.filter((i) => {
    if (actionFilter !== ALL_ACTIONS && i.action !== actionFilter) return false;
    if (search.trim()) {
      const q = search.trim().toLowerCase();
      const hay = `${i.action} ${i.actor_email ?? ""} ${i.resource_type ?? ""} ${
        i.resource_id ?? ""
      } ${i.ip_address ?? ""}`.toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });

  const columns: Column<AuditLogEntry>[] = [
    {
      id: "action",
      header: "Action",
      cell: (e) => (
        <Badge variant={actionVariant(e.action)} className="font-mono text-[11px]">
          {e.action}
        </Badge>
      ),
    },
    {
      id: "actor",
      header: "Actor",
      cell: (e) => (
        <span className="text-sm">
          {e.actor_email ?? (
            <span className="text-muted-foreground">system / API key</span>
          )}
        </span>
      ),
    },
    {
      id: "resource",
      header: "Resource",
      hideOnMobile: true,
      cell: (e) =>
        e.resource_type ? (
          <div className="min-w-0">
            <p className="text-sm capitalize">{e.resource_type.replace(/_/g, " ")}</p>
            {e.resource_id ? (
              <code className="text-xs text-muted-foreground">
                {e.resource_id.slice(0, 8)}…
              </code>
            ) : null}
          </div>
        ) : (
          <span className="text-sm text-muted-foreground">-</span>
        ),
    },
    {
      id: "ip",
      header: "IP",
      hideOnMobile: true,
      cell: (e) => (
        <span className="font-mono text-xs text-muted-foreground">
          {e.ip_address ?? "-"}
        </span>
      ),
    },
    {
      id: "when",
      header: "When",
      align: "right",
      cell: (e) => (
        <span className="whitespace-nowrap text-sm text-muted-foreground">
          {formatDate(e.created_at, {
            month: "short",
            day: "numeric",
            hour: "2-digit",
            minute: "2-digit",
          })}
        </span>
      ),
    },
  ];

  return (
    <div className="space-y-6">
      <PageHeader
        title="Audit Log"
        description="A record of sensitive actions across your organization."
      >
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
          <Input
            placeholder="Search this page…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="sm:max-w-xs"
          />
          <Select value={actionFilter} onValueChange={setActionFilter}>
            <SelectTrigger className="sm:w-56">
              <SelectValue placeholder="All actions" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL_ACTIONS}>All actions</SelectItem>
              {actionsOnPage.map((a) => (
                <SelectItem key={a} value={a} className="font-mono text-xs">
                  {a}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </PageHeader>

      <DataTable
        columns={columns}
        data={filtered}
        rowKey={(e) => e.id}
        isLoading={logQuery.isLoading}
        loadingRows={PAGE_SIZE}
        empty={
          <EmptyState
            compact
            icon={ScrollText}
            title={
              items.length && (actionFilter !== ALL_ACTIONS || search)
                ? "No matching entries on this page"
                : "No audit entries yet"
            }
            description="Sensitive actions like sharing, key creation and member changes are recorded here."
          />
        }
      />

      <div className="flex items-center justify-between">
        <p className="text-sm text-muted-foreground">
          {total > 0 ? (
            <>
              Page {page} of {pageCount} · {total} total
            </>
          ) : null}
        </p>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            disabled={page <= 1 || logQuery.isFetching}
            onClick={() => setPage((p) => Math.max(1, p - 1))}
          >
            <ChevronLeft className="h-4 w-4" />
            Previous
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={page >= pageCount || logQuery.isFetching}
            onClick={() => setPage((p) => p + 1)}
          >
            Next
            <ChevronRight className="h-4 w-4" />
          </Button>
        </div>
      </div>
    </div>
  );
}
