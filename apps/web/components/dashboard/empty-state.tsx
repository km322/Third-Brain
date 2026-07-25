import * as React from "react";
import type { LucideIcon } from "lucide-react";

import { cn } from "@/lib/utils";

interface EmptyStateProps {
  icon?: LucideIcon;
  title: React.ReactNode;
  description?: React.ReactNode;
  /** Primary/secondary actions (e.g. a "Create" button). */
  actions?: React.ReactNode;
  className?: string;
  /** Compact variant for use inside tables and small panels. */
  compact?: boolean;
}

/**
 * Friendly placeholder for zero-data states: an icon, a headline, supporting
 * copy and optional actions. Wrapped in a dashed card so it reads as "nothing
 * here yet" rather than an error.
 */
export function EmptyState({
  icon: Icon,
  title,
  description,
  actions,
  className,
  compact = false,
}: EmptyStateProps) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center rounded-lg border border-dashed bg-card/40 text-center",
        compact ? "gap-2 p-6" : "gap-3 p-10",
        className,
      )}
    >
      {Icon ? (
        <span
          className={cn(
            "flex items-center justify-center rounded-full bg-muted text-muted-foreground",
            compact ? "h-9 w-9" : "h-12 w-12",
          )}
        >
          <Icon className={compact ? "h-4 w-4" : "h-6 w-6"} />
        </span>
      ) : null}
      <div className="space-y-1">
        <h3
          className={cn(
            "font-semibold text-foreground",
            compact ? "text-sm" : "text-base",
          )}
        >
          {title}
        </h3>
        {description ? (
          <p className="mx-auto max-w-sm text-sm text-muted-foreground">
            {description}
          </p>
        ) : null}
      </div>
      {actions ? <div className="mt-2 flex items-center gap-2">{actions}</div> : null}
    </div>
  );
}
