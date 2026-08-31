"use client";

import * as React from "react";
import { BarChart3 } from "lucide-react";
import {
  Bar,
  BarChart,
  Cell,
  LabelList,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { EmptyState } from "@/components/dashboard/empty-state";
import { Skeleton } from "@/components/ui/skeleton";
import type { UsageKind, UsageSummary } from "@/lib/types";
import { cn, formatCurrency, formatNumber } from "@/lib/utils";

import { kindColor, kindLabel } from "./kinds";

type KindRow = UsageSummary["by_kind"][number];
type BarMetric = "requests" | "tokens" | "cost_usd";

const METRIC_FORMAT: Record<BarMetric, (n: number) => string> = {
  requests: (n) => formatNumber(n),
  tokens: (n) => formatNumber(n),
  cost_usd: (n) => formatCurrency(n),
};

interface ChartDatum {
  kind: UsageKind;
  label: string;
  color: string;
  value: number;
  row: KindRow;
}

interface TooltipEntry {
  payload?: ChartDatum;
}

function KindTooltip({
  active,
  payload,
}: {
  active?: boolean;
  payload?: TooltipEntry[];
}) {
  const datum = active ? payload?.[0]?.payload : undefined;
  if (!datum) return null;
  const { row } = datum;

  return (
    <div className="min-w-[9rem] rounded-lg border bg-popover p-2.5 text-popover-foreground shadow-md">
      <p className="mb-1.5 flex items-center gap-1.5 text-xs font-medium">
        <span
          className="h-2 w-2 rounded-[2px]"
          style={{ backgroundColor: datum.color }}
          aria-hidden
        />
        {datum.label}
      </p>
      <dl className="space-y-1 text-xs">
        <Line label="Requests" value={formatNumber(row.requests)} />
        <Line label="Tokens" value={formatNumber(row.tokens)} />
        <Line label="Cost" value={formatCurrency(row.cost_usd)} />
      </dl>
    </div>
  );
}

function Line({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-center justify-between gap-4">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="font-medium tabular-nums text-foreground">{value}</dd>
    </div>
  );
}

interface BarByKindProps {
  data: KindRow[];
  /** Which measure to plot per kind (default: requests). */
  metric?: BarMetric;
  loading?: boolean;
  height?: number;
  className?: string;
}

/**
 * Horizontal bar chart of usage broken down by kind, sorted high-to-low. Each
 * bar is colored by its kind's stable categorical slot; the tooltip shows all
 * three measures. Horizontal layout keeps the kind labels readable without
 * rotation.
 */
export function BarByKind({
  data,
  metric = "requests",
  loading = false,
  height = 260,
  className,
}: BarByKindProps) {
  const rows: ChartDatum[] = React.useMemo(
    () =>
      data
        .map((row) => ({
          kind: row.kind,
          label: kindLabel(row.kind),
          color: kindColor(row.kind),
          value: row[metric],
          row,
        }))
        .sort((a, b) => b.value - a.value),
    [data, metric],
  );

  const hasData = rows.some((r) => r.value > 0);
  const format = METRIC_FORMAT[metric];

  if (loading) {
    return <Skeleton className={cn("w-full", className)} style={{ height }} />;
  }

  if (!hasData) {
    return (
      <div className={cn("flex items-center", className)} style={{ minHeight: height }}>
        <EmptyState
          icon={BarChart3}
          title="No usage to break down"
          description="Per-type usage appears once activity is recorded."
          compact
          className="w-full"
        />
      </div>
    );
  }

  return (
    <ResponsiveContainer width="100%" height={height} className={className}>
      <BarChart
        layout="vertical"
        data={rows}
        margin={{ top: 4, right: 44, left: 4, bottom: 4 }}
        barCategoryGap={10}
      >
        <XAxis type="number" dataKey="value" hide />
        <YAxis
          type="category"
          dataKey="label"
          width={92}
          tickLine={false}
          axisLine={false}
          tick={{ fontSize: 12, fill: "hsl(var(--foreground))" }}
        />
        <Tooltip
          cursor={{ fill: "hsl(var(--muted))", opacity: 0.5 }}
          content={<KindTooltip />}
        />
        <Bar dataKey="value" radius={[0, 4, 4, 0]} maxBarSize={26}>
          {rows.map((r) => (
            <Cell key={r.kind} fill={r.color} />
          ))}
          <LabelList
            dataKey="value"
            position="right"
            formatter={(v: number) => format(v)}
            style={{ fontSize: 12, fill: "hsl(var(--muted-foreground))" }}
          />
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}
