"use client";

import * as React from "react";
import { PieChart as PieIcon } from "lucide-react";
import { Cell, Pie, PieChart, ResponsiveContainer } from "recharts";

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

interface DonutChartProps {
  data: DonutSegment[];
  /** Override the denominator (defaults to the sum of segment values). */
  total?: number;
  /** Small caption under the center figure (e.g. "Total spend"). */
  centerLabel?: string;
  /** Formats segment values in the legend and center figure. */
  formatValue?: (n: number) => string;
  loading?: boolean;
  height?: number;
  emptyMessage?: string;
  className?: string;
}

/**
 * A reusable donut with a center total and a compact legend. Segment identity
 * is carried by color (from the caller) plus a labelled legend, so it never
 * relies on color alone. The center figure is overlaid on the donut hole rather
 * than drawn by recharts. Renders a friendly empty state when there's no value
 * to divide.
 *
 * Hovering a segment - or its legend row - reads that segment out in the center
 * and dims the rest, instead of floating a tooltip: recharts anchors a pie
 * tooltip at the hovered sector's midpoint, which for a donut lands on top of
 * the center figure and makes both unreadable.
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
  const [activeName, setActiveName] = React.useState<string | null>(null);
  const sum = total ?? data.reduce((acc, d) => acc + d.value, 0);
  const segments = React.useMemo(
    () => data.filter((d) => d.value > 0).sort((a, b) => b.value - a.value),
    [data],
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

  const active = segments.find((s) => s.name === activeName) ?? null;
  const activePct = active && sum > 0 ? (active.value / sum) * 100 : 0;

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
              onMouseEnter={(_, index) => setActiveName(segments[index]?.name ?? null)}
              onMouseLeave={() => setActiveName(null)}
            >
              {segments.map((s) => (
                <Cell
                  key={s.name}
                  fill={s.color}
                  fillOpacity={activeName && activeName !== s.name ? 0.3 : 1}
                />
              ))}
            </Pie>
          </PieChart>
        </ResponsiveContainer>
        <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center text-center">
          <span className="text-lg font-semibold tabular-nums tracking-tight">
            {formatValue(active ? active.value : sum)}
          </span>
          {active ? (
            <span
              className="truncate text-[11px] text-muted-foreground"
              style={{ maxWidth: inner * 2 - 12 }}
            >
              {active.name} · {activePct.toFixed(1)}%
            </span>
          ) : centerLabel ? (
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
              onMouseEnter={() => setActiveName(s.name)}
              onMouseLeave={() => setActiveName(null)}
              className={cn(
                "flex items-center justify-between gap-3 text-sm transition-opacity",
                activeName && activeName !== s.name && "opacity-50",
              )}
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
