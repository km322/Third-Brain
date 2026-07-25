"use client";

import * as React from "react";
import { keepPreviousData } from "@tanstack/react-query";
import { AlertCircle, Coins, DollarSign, Gauge, Layers } from "lucide-react";

import { kindColor, kindLabel } from "@/components/dashboard/charts/kinds";
import {
  AreaUsageChart,
  BarByKind,
  DonutChart,
} from "@/components/dashboard/charts/lazy";
import type { DonutSegment } from "@/components/dashboard/charts/donut";
import { DataTable, type Column } from "@/components/dashboard/data-table";
import { PageHeader } from "@/components/dashboard/page-header";
import { StatCard } from "@/components/dashboard/stat-card";
import { Card } from "@/components/ui/card";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useApiQuery } from "@/lib/hooks";
import type { UsageSummary } from "@/lib/types";
import { formatCurrency, formatNumber } from "@/lib/utils";

type KindRow = UsageSummary["by_kind"][number];

const RANGES = [
  { value: 7, label: "7 days" },
  { value: 30, label: "30 days" },
  { value: 90, label: "90 days" },
] as const;

export default function UsagePage() {
  const [days, setDays] = React.useState<number>(30);

  const usage = useApiQuery<UsageSummary>(
    ["analytics", "usage", days],
    "/analytics/usage",
    { days },
    { placeholderData: keepPreviousData },
  );

  const data = usage.data;
  const loading = usage.isLoading;

  const avgTokens =
    data && data.total_requests > 0
      ? Math.round(data.total_tokens / data.total_requests)
      : 0;

  const costSegments: DonutSegment[] = React.useMemo(
    () =>
      (data?.by_kind ?? []).map((row) => ({
        name: kindLabel(row.kind),
        value: row.cost_usd,
        color: kindColor(row.kind),
      })),
    [data?.by_kind],
  );

  const columns: Column<KindRow>[] = [
    {
      id: "kind",
      header: "Type",
      cell: (row) => (
        <span className="flex items-center gap-2 font-medium">
          <span
            className="h-2.5 w-2.5 shrink-0 rounded-[3px]"
            style={{ backgroundColor: kindColor(row.kind) }}
            aria-hidden
          />
          {kindLabel(row.kind)}
        </span>
      ),
    },
    {
      id: "requests",
      header: "Requests",
      align: "right",
      cell: (row) => (
        <span className="tabular-nums">{formatNumber(row.requests)}</span>
      ),
    },
    {
      id: "tokens",
      header: "Tokens",
      align: "right",
      cell: (row) => (
        <span className="tabular-nums">{formatNumber(row.tokens)}</span>
      ),
    },
    {
      id: "cost",
      header: "Cost",
      align: "right",
      cell: (row) => (
        <span className="tabular-nums">{formatCurrency(row.cost_usd)}</span>
      ),
    },
  ];

  const rangeLabel = RANGES.find((r) => r.value === days)?.label ?? `${days} days`;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Usage"
        description="Requests, tokens and estimated cost across your organization."
        actions={
          <Tabs
            value={String(days)}
            onValueChange={(v) => setDays(Number(v))}
          >
            <TabsList>
              {RANGES.map((r) => (
                <TabsTrigger key={r.value} value={String(r.value)}>
                  {r.label}
                </TabsTrigger>
              ))}
            </TabsList>
          </Tabs>
        }
      />

      {usage.isError ? (
        <Card className="flex items-center gap-3 border-destructive/40 p-4 text-sm text-destructive">
          <AlertCircle className="h-5 w-5 shrink-0" />
          <span>Couldn&apos;t load usage data. Please try again.</span>
        </Card>
      ) : (
        <>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <StatCard
              label="Total requests"
              value={data ? formatNumber(data.total_requests) : "-"}
              icon={Layers}
              hint={`last ${rangeLabel}`}
              loading={loading}
            />
            <StatCard
              label="Total tokens"
              value={data ? formatNumber(data.total_tokens) : "-"}
              icon={Coins}
              hint={`last ${rangeLabel}`}
              loading={loading}
            />
            <StatCard
              label="Estimated cost"
              value={data ? formatCurrency(data.total_cost_usd) : "-"}
              icon={DollarSign}
              hint={`last ${rangeLabel}`}
              loading={loading}
            />
            <StatCard
              label="Avg tokens / request"
              value={data ? formatNumber(avgTokens) : "-"}
              icon={Gauge}
              hint="across all activity"
              loading={loading}
            />
          </div>

          <AreaUsageChart
            title="Activity trend"
            description={`Requests, tokens and cost over the last ${rangeLabel}`}
            data={data?.by_day ?? []}
            loading={loading}
            height={300}
          />

          <div className="grid gap-6 lg:grid-cols-2">
            <Card className="flex flex-col p-5">
              <div className="mb-4 space-y-0.5">
                <h3 className="text-sm font-semibold tracking-tight">
                  Requests by type
                </h3>
                <p className="text-xs text-muted-foreground">
                  Where your request volume goes
                </p>
              </div>
              <BarByKind
                data={data?.by_kind ?? []}
                metric="requests"
                loading={loading}
              />
            </Card>

            <Card className="flex flex-col p-5">
              <div className="mb-4 space-y-0.5">
                <h3 className="text-sm font-semibold tracking-tight">
                  Cost by type
                </h3>
                <p className="text-xs text-muted-foreground">
                  Estimated spend distribution
                </p>
              </div>
              <DonutChart
                data={costSegments}
                centerLabel="Total spend"
                formatValue={formatCurrency}
                loading={loading}
                emptyMessage="No spend for this period."
                className="flex-1"
              />
            </Card>
          </div>

          <div className="space-y-3">
            <div className="space-y-0.5">
              <h3 className="text-sm font-semibold tracking-tight">
                Breakdown by type
              </h3>
              <p className="text-xs text-muted-foreground">
                Per-type requests, tokens and cost over the last {rangeLabel}
              </p>
            </div>
            <DataTable
              columns={columns}
              data={data?.by_kind ?? []}
              rowKey={(row) => row.kind}
              isLoading={loading}
              loadingRows={4}
              empty={
                <span className="text-sm text-muted-foreground">
                  No usage recorded in this period.
                </span>
              }
            />
          </div>
        </>
      )}
    </div>
  );
}
