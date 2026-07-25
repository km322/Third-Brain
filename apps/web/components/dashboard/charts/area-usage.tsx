"use client";

import * as React from "react";
import { Activity } from "lucide-react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { EmptyState } from "@/components/dashboard/empty-state";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import type { UsagePoint } from "@/lib/types";
import { cn, formatCurrency, formatNumber } from "@/lib/utils";

export type UsageMetric = "requests" | "tokens" | "cost_usd";

interface MetricConfig {
  label: string;
  /** CSS-var driven series color (chart-1..6). */
  color: string;
  /** Full-precision formatter for tooltips. */
  format: (n: number) => string;
  /** Compact formatter for axis ticks. */
  axis: (n: number) => string;
}

const METRICS: Record<UsageMetric, MetricConfig> = {
  requests: {
    label: "Requests",
    color: "hsl(var(--chart-1))",
    format: (n) => formatNumber(n),
    axis: (n) => compactNumber(n),
  },
  tokens: {
    label: "Tokens",
    color: "hsl(var(--chart-2))",
    format: (n) => formatNumber(n),
    axis: (n) => compactNumber(n),
  },
  cost_usd: {
    label: "Cost",
    color: "hsl(var(--chart-3))",
    format: (n) => formatCurrency(n),
    axis: (n) => compactCurrency(n),
  },
};

const COMPACT_NUMBER = new Intl.NumberFormat(undefined, {
  notation: "compact",
  maximumFractionDigits: 1,
});

const COMPACT_CURRENCY = new Intl.NumberFormat(undefined, {
  style: "currency",
  currency: "USD",
  notation: "compact",
  maximumFractionDigits: 1,
});

function compactNumber(n: number): string {
  return COMPACT_NUMBER.format(n);
}

function compactCurrency(n: number): string {
  return COMPACT_CURRENCY.format(n);
}

/** ISO `YYYY-MM-DD` -> "Jul 1" without timezone drift. */
function shortDate(iso: string): string {
  const d = new Date(`${iso}T00:00:00`);
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function longDate(iso: string): string {
  const d = new Date(`${iso}T00:00:00`);
  return d.toLocaleDateString(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
  });
}

interface TooltipEntry {
  payload?: UsagePoint;
}

/** Rich tooltip: names the day and lists every metric, highlighting the plotted one. */
function UsageTooltip({
  active,
  payload,
  activeMetric,
}: {
  active?: boolean;
  payload?: TooltipEntry[];
  activeMetric: UsageMetric;
}) {
  const point = active ? payload?.[0]?.payload : undefined;
  if (!point) return null;

  return (
    <div className="min-w-[9rem] rounded-lg border bg-popover p-2.5 text-popover-foreground shadow-md">
      <p className="mb-1.5 text-xs font-medium text-muted-foreground">
        {longDate(point.date)}
      </p>
      <ul className="space-y-1">
        {(Object.keys(METRICS) as UsageMetric[]).map((key) => {
          const cfg = METRICS[key];
          const isActive = key === activeMetric;
          return (
            <li
              key={key}
              className="flex items-center justify-between gap-4 text-xs"
            >
              <span className="flex items-center gap-1.5">
                <span
                  className="h-2 w-2 rounded-[2px]"
                  style={{ backgroundColor: cfg.color }}
                  aria-hidden
                />
                <span
                  className={cn(
                    isActive ? "font-medium text-foreground" : "text-muted-foreground",
                  )}
                >
                  {cfg.label}
                </span>
              </span>
              <span
                className={cn(
                  "tabular-nums",
                  isActive ? "font-semibold text-foreground" : "text-muted-foreground",
                )}
              >
                {cfg.format(point[key])}
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

interface AreaUsageChartProps {
  data: UsagePoint[];
  loading?: boolean;
  title?: React.ReactNode;
  description?: React.ReactNode;
  /** Which metrics the toggle offers (default: all three). */
  metrics?: UsageMetric[];
  defaultMetric?: UsageMetric;
  height?: number;
  className?: string;
}

/**
 * Primary usage time-series. Plots a single metric at a time on one Y-axis
 * (requests, tokens and cost differ by orders of magnitude, so they never share
 * a scale) with an inline segmented toggle to switch series. The tooltip still
 * surfaces every metric for the hovered day. Colors come from the `--chart-*`
 * CSS variables so the chart tracks light/dark themes.
 */
export function AreaUsageChart({
  data,
  loading = false,
  title = "Usage over time",
  description,
  metrics = ["requests", "tokens", "cost_usd"],
  defaultMetric = "requests",
  height = 288,
  className,
}: AreaUsageChartProps) {
  const [metric, setMetric] = React.useState<UsageMetric>(defaultMetric);
  const gradientId = React.useId();
  const cfg = METRICS[metric];

  const hasData = data.some((d) => d.requests || d.tokens || d.cost_usd);

  return (
    <Card className={cn("flex flex-col p-5", className)}>
      <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div className="space-y-0.5">
          <h3 className="text-sm font-semibold tracking-tight">{title}</h3>
          {description ? (
            <p className="text-xs text-muted-foreground">{description}</p>
          ) : null}
        </div>
        <Tabs
          value={metric}
          onValueChange={(v) => setMetric(v as UsageMetric)}
        >
          <TabsList className="h-8">
            {metrics.map((key) => (
              <TabsTrigger
                key={key}
                value={key}
                className="px-2.5 py-1 text-xs"
              >
                {METRICS[key].label}
              </TabsTrigger>
            ))}
          </TabsList>
        </Tabs>
      </div>

      {loading ? (
        <Skeleton className="w-full" style={{ height }} />
      ) : !hasData ? (
        <div className="flex flex-1 items-center" style={{ minHeight: height }}>
          <EmptyState
            icon={Activity}
            title="No usage yet"
            description="Run searches, ingest documents or call the API to see activity here."
            compact
            className="w-full"
          />
        </div>
      ) : (
        <ResponsiveContainer width="100%" height={height}>
          <AreaChart
            data={data}
            margin={{ top: 8, right: 8, left: 0, bottom: 0 }}
          >
            <defs>
              <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor={cfg.color} stopOpacity={0.35} />
                <stop offset="95%" stopColor={cfg.color} stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid
              vertical={false}
              stroke="hsl(var(--border))"
              strokeOpacity={0.7}
            />
            <XAxis
              dataKey="date"
              tickFormatter={shortDate}
              tickLine={false}
              axisLine={false}
              minTickGap={28}
              tick={{ fontSize: 12, fill: "hsl(var(--muted-foreground))" }}
            />
            <YAxis
              tickFormatter={cfg.axis}
              width={52}
              tickLine={false}
              axisLine={false}
              allowDecimals={false}
              tick={{ fontSize: 12, fill: "hsl(var(--muted-foreground))" }}
            />
            <Tooltip
              cursor={{ stroke: "hsl(var(--border))", strokeWidth: 1 }}
              content={<UsageTooltip activeMetric={metric} />}
            />
            <Area
              type="monotone"
              dataKey={metric}
              name={cfg.label}
              stroke={cfg.color}
              strokeWidth={2}
              fill={`url(#${gradientId})`}
              activeDot={{
                r: 4,
                strokeWidth: 2,
                stroke: "hsl(var(--background))",
              }}
            />
          </AreaChart>
        </ResponsiveContainer>
      )}
    </Card>
  );
}
