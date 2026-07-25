"use client";

import * as React from "react";
import { PieChart as PieIcon } from "lucide-react";
import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from "recharts";

import { EmptyState } from "@/components/dashboard/empty-state";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

const NUMBER_FORMAT = new Intl.NumberFormat();

export interface DonutSegment {
  name: string;
  value: number;
  /** Segment color - pass a `hsl(var(--chart-N))` string. */
  color: string;
}

interface TooltipEntry {
  payload?: DonutSegment & { _total: number };
}

function DonutTooltip({
  active,
  payload,
  formatValue,
}: {
  active?: boolean;
  payload?: TooltipEntry[];
  formatValue: (n: number) => string;
}) {
  const seg = active ? payload?.[0]?.payload : undefined;
  if (!seg) return null;
  const pct = seg._total > 0 ? (seg.value / seg._total) * 100 : 0;

  return (
    <div className="rounded-lg border bg-popover p-2.5 text-popover-foreground shadow-md">
      <p className="flex items-center gap-1.5 text-xs font-medium">
        <span
          className="h-2 w-2 rounded-[2px]"
          style={{ backgroundColor: seg.color }}
          aria-hidden
        />
        {seg.name}
      </p>
      <p className="mt-1 text-xs tabular-nums text-muted-foreground">
        <span className="font-semibold text-foreground">
          {formatValue(seg.value)}
        </span>{" "}
        · {pct.toFixed(1)}%
      </p>
    </div>
  );
}

interface DonutChartProps {
  data: DonutSegment[];
  /** Override the denominator (defaults to the sum of segment values). */
  total?: number;
  /** Small caption under the center figure (e.g. "Total spend"). */
  centerLabel?: string;
  /** Formats segment values in the tooltip, legend and center figure. */
  formatValue?: (n: number) => string;
  loading?: boolean;
  height?: number;
  emptyMessage?: string;
  className?: string;
}

/**
 * A reusable donut with a center total and a compact legend. Segment identity
 * is carried by color (from the caller) plus a labelled legend, so it never
 * relies on color alone. Renders a friendly empty state when there's no value
 * to divide.
 */
export function DonutChart({
  data,
  total,
  centerLabel,
  formatValue = (n) => NUMBER_FORMAT.format(n),
  loading = false,
  height = 220,
  emptyMessage = "No data for this period.",
  className,
}: DonutChartProps) {
  const sum = total ?? data.reduce((acc, d) => acc + d.value, 0);
  const segments = React.useMemo(
    () =>
      data
        .filter((d) => d.value > 0)
        .map((d) => ({ ...d, _total: sum }))
        .sort((a, b) => b.value - a.value),
    [data, sum],
  );

  if (loading) {
    return (
      <div className={cn("flex items-center justify-center", className)}>
        <Skeleton className="rounded-full" style={{ height, width: height }} />
      </div>
    );
  }

  if (segments.length === 0 || sum <= 0) {
    return (
      <div className={cn("flex items-center", className)} style={{ minHeight: height }}>
        <EmptyState icon={PieIcon} title={emptyMessage} compact className="w-full" />
      </div>
    );
  }

  const inner = Math.round(height * 0.3);
  const outer = Math.round(height * 0.44);

  return (
    <div
      className={cn(
        "flex flex-col items-center gap-5 sm:flex-row sm:items-center",
        className,
      )}
    >
      <div className="relative shrink-0" style={{ height, width: height }}>
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            <Tooltip
              content={<DonutTooltip formatValue={formatValue} />}
              cursor={false}
            />
            <Pie
              data={segments}
              dataKey="value"
              nameKey="name"
              innerRadius={inner}
              outerRadius={outer}
              paddingAngle={2}
              strokeWidth={2}
              stroke="hsl(var(--card))"
              startAngle={90}
              endAngle={-270}
            >
              {segments.map((s) => (
                <Cell key={s.name} fill={s.color} />
              ))}
            </Pie>
          </PieChart>
        </ResponsiveContainer>
        {/* Center figure overlaid on the donut hole. */}
        <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center text-center">
          <span className="text-lg font-semibold tabular-nums tracking-tight">
            {formatValue(sum)}
          </span>
          {centerLabel ? (
            <span className="text-[11px] text-muted-foreground">{centerLabel}</span>
          ) : null}
        </div>
      </div>

      <ul className="w-full flex-1 space-y-2">
        {segments.map((s) => {
          const pct = sum > 0 ? (s.value / sum) * 100 : 0;
          return (
            <li
              key={s.name}
              className="flex items-center justify-between gap-3 text-sm"
            >
              <span className="flex min-w-0 items-center gap-2">
                <span
                  className="h-2.5 w-2.5 shrink-0 rounded-[3px]"
                  style={{ backgroundColor: s.color }}
                  aria-hidden
                />
                <span className="truncate text-muted-foreground">{s.name}</span>
              </span>
              <span className="flex shrink-0 items-center gap-2 tabular-nums">
                <span className="font-medium text-foreground">
                  {formatValue(s.value)}
                </span>
                <span className="w-10 text-right text-xs text-muted-foreground">
                  {pct.toFixed(1)}%
                </span>
              </span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
