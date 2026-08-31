import * as React from "react";
import { ArrowDownRight, ArrowUpRight, type LucideIcon } from "lucide-react";

import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

interface StatCardProps {
  label: string;
  value: React.ReactNode;
  /** Signed percentage/point change vs the previous period. */
  delta?: number;
  /** Caption shown beside the delta (e.g. "vs last 30d"). */
  deltaLabel?: string;
  /** For metrics where a decrease is good (e.g. latency), flips delta colors. */
  invertDelta?: boolean;
  icon?: LucideIcon;
  hint?: React.ReactNode;
  loading?: boolean;
  className?: string;
}

/**
 * KPI tile: a label, a large value, an optional trend delta and an icon. Used
 * across the Overview and Usage pages. Set `loading` to render a placeholder.
 */
export function StatCard({
  label,
  value,
  delta,
  deltaLabel,
  invertDelta = false,
  icon: Icon,
  hint,
  loading = false,
  className,
}: StatCardProps) {
  const hasDelta = typeof delta === "number" && Number.isFinite(delta);
  const positive = hasDelta ? (invertDelta ? delta! < 0 : delta! > 0) : false;
  const neutral = hasDelta && delta === 0;

  return (
    <Card className={cn("p-5", className)}>
      <div className="flex items-start justify-between gap-3">
        <p className="text-sm font-medium text-muted-foreground">{label}</p>
        {Icon ? (
          <span className="flex h-8 w-8 items-center justify-center rounded-md bg-primary/10 text-primary">
            <Icon className="h-4 w-4" />
          </span>
        ) : null}
      </div>

      <div className="mt-3">
        {loading ? (
          <Skeleton className="h-8 w-24" />
        ) : (
          <p className="text-2xl font-semibold tabular-nums tracking-tight">{value}</p>
        )}
      </div>

      {(hasDelta || hint) && !loading ? (
        <div className="mt-2 flex items-center gap-2 text-xs">
          {hasDelta ? (
            <span
              className={cn(
                "inline-flex items-center gap-0.5 font-medium",
                neutral
                  ? "text-muted-foreground"
                  : positive
                    ? "text-success"
                    : "text-destructive",
              )}
            >
              {!neutral &&
                (delta! > 0 ? (
                  <ArrowUpRight className="h-3.5 w-3.5" />
                ) : (
                  <ArrowDownRight className="h-3.5 w-3.5" />
                ))}
              {delta! > 0 ? "+" : ""}
              {delta}%
            </span>
          ) : null}
          {deltaLabel || hint ? (
            <span className="text-muted-foreground">{deltaLabel ?? hint}</span>
          ) : null}
        </div>
      ) : null}
    </Card>
  );
}
