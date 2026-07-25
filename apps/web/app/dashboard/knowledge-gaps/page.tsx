"use client";

import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Lightbulb,
  Lock,
  MessageSquare,
  MessageSquareOff,
  Target,
  ThumbsDown,
} from "lucide-react";
import { toast } from "sonner";

import { EmptyState } from "@/components/dashboard/empty-state";
import { PageHeader } from "@/components/dashboard/page-header";
import { StatCard } from "@/components/dashboard/stat-card";
import { isOrgAdmin } from "@/components/governance/role-badge";
import { Card } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
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
import type { KnowledgeGapReport } from "@/lib/types";
import { formatNumber } from "@/lib/utils";

interface RetentionSetting {
  enabled: boolean;
}

const errMsg = (e: unknown, f = "Something went wrong") =>
  e instanceof ApiError ? e.message : f;

export default function KnowledgeGapsPage() {
  const { role } = useAuth();
  const admin = isOrgAdmin(role);
  const queryClient = useQueryClient();

  const gapsQuery = useQuery<KnowledgeGapReport>({
    queryKey: ["knowledge-gaps"],
    queryFn: () => api.get<KnowledgeGapReport>("/feedback/gaps"),
    enabled: admin,
  });

  const retentionQuery = useQuery<RetentionSetting>({
    queryKey: ["gap-retention"],
    queryFn: () => api.get<RetentionSetting>("/feedback/retention"),
    enabled: admin,
  });

  const setRetention = useMutation({
    mutationFn: (enabled: boolean) =>
      api.put<RetentionSetting>("/feedback/retention", { enabled }),
    onSuccess: (res) => {
      queryClient.setQueryData(["gap-retention"], res);
      void queryClient.invalidateQueries({ queryKey: ["knowledge-gaps"] });
      toast.success(
        res.enabled
          ? "Now storing query text for gap analysis"
          : "Query text retention turned off",
      );
    },
    onError: (e) => toast.error(errMsg(e, "Couldn't update retention setting")),
  });

  if (!admin) {
    return (
      <div className="space-y-6">
        <PageHeader
          title="Knowledge Gaps"
          description="See what your organization asks that the knowledge base can't answer yet."
        />
        <EmptyState
          icon={Lock}
          title="Admin access required"
          description="Only organization owners and admins can view knowledge-gap analytics."
        />
      </div>
    );
  }

  const report = gapsQuery.data;
  const loading = gapsQuery.isLoading;
  const windowLabel = report ? `last ${report.window_days} days` : undefined;
  const answeredRate =
    report?.answered_rate != null
      ? `${(report.answered_rate * 100).toFixed(1)}%`
      : "-";
  const retentionOn = retentionQuery.data?.enabled ?? false;
  const showGaps = Boolean(
    report?.query_text_retained && report?.top_gaps.length,
  );

  return (
    <div className="space-y-6">
      <PageHeader
        title="Knowledge Gaps"
        description="What your organization asks that the knowledge base can't answer yet, so you know what to document next."
      />

      {gapsQuery.isError ? (
        <EmptyState
          icon={Lightbulb}
          title="Couldn't load knowledge gaps"
          description="Something went wrong fetching gap analytics. Try again in a moment."
        />
      ) : (
        <>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <StatCard
              label="Total queries"
              value={report ? formatNumber(report.total_queries) : "-"}
              icon={MessageSquare}
              hint={windowLabel}
              loading={loading}
            />
            <StatCard
              label="Answered rate"
              value={answeredRate}
              icon={Target}
              hint={report ? `${formatNumber(report.answered)} answered` : undefined}
              loading={loading}
            />
            <StatCard
              label="Unanswered"
              value={report ? formatNumber(report.unanswered) : "-"}
              icon={MessageSquareOff}
              hint="no matching knowledge"
              loading={loading}
            />
            <StatCard
              label="Thumbs-down"
              value={report ? formatNumber(report.negative) : "-"}
              icon={ThumbsDown}
              hint="answers rated unhelpful"
              loading={loading}
            />
          </div>

          <Card className="flex flex-col gap-4 p-5 sm:flex-row sm:items-center sm:justify-between">
            <div className="space-y-1">
              <Label htmlFor="gap-retention" className="text-sm font-medium">
                Store query text for gap analysis
              </Label>
              <p className="max-w-2xl text-xs text-muted-foreground">
                Off by default for privacy. When enabled, the raw text of
                unanswered and thumbs-down queries is retained so the list below
                can show the actual questions people asked. Aggregate counts are
                always tracked regardless of this setting.
              </p>
            </div>
            <Switch
              id="gap-retention"
              checked={retentionOn}
              disabled={retentionQuery.isLoading || setRetention.isPending}
              onCheckedChange={(v) => setRetention.mutate(v)}
            />
          </Card>

          {!loading && report ? (
            <div className="space-y-3">
              <div className="space-y-0.5">
                <h3 className="text-sm font-semibold tracking-tight">
                  Top unanswered and negatively-rated queries
                </h3>
                <p className="text-xs text-muted-foreground">
                  The questions most worth documenting, {windowLabel}.
                </p>
              </div>

              {showGaps ? (
                <Card className="overflow-hidden p-0">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead className="w-12">#</TableHead>
                        <TableHead>Query</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {report.top_gaps.map((query, i) => (
                        <TableRow key={`${i}-${query}`}>
                          <TableCell className="tabular-nums text-muted-foreground">
                            {i + 1}
                          </TableCell>
                          <TableCell className="font-medium">{query}</TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </Card>
              ) : (
                <EmptyState
                  icon={Lightbulb}
                  title={
                    report.query_text_retained
                      ? "No gaps captured yet"
                      : "Query text isn't being stored"
                  }
                  description={
                    report.query_text_retained
                      ? "No unanswered or thumbs-down queries have been recorded in this window. Check back after more activity."
                      : "Enable “Store query text for gap analysis” above to surface the actual questions behind these gaps. Until then, only the aggregate counts are available."
                  }
                />
              )}
            </div>
          ) : null}
        </>
      )}
    </div>
  );
}
