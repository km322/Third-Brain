"use client";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

/**
 * Small environment indicator for the dashboard shell. Reads the deployment
 * environment from `NEXT_PUBLIC_APP_ENV` (falling back to `NODE_ENV`) and renders
 * a subtle badge for anything that is not production. In production - or in an
 * environment it does not recognise - it renders nothing, so it stays out of the
 * way for real users.
 */
const ENV = process.env.NEXT_PUBLIC_APP_ENV || process.env.NODE_ENV || "development";

const LABELS: Record<string, { label: string; variant: "warning" | "info" }> = {
  development: { label: "Dev", variant: "warning" },
  test: { label: "Test", variant: "info" },
  staging: { label: "Staging", variant: "info" },
  preview: { label: "Preview", variant: "info" },
};

export function ModeBadge({ className }: { className?: string }) {
  const config = LABELS[ENV];
  if (!config) return null;

  return (
    <Badge
      variant={config.variant}
      className={cn("gap-1 uppercase tracking-wide", className)}
      title={`Environment: ${ENV}`}
    >
      <span className="relative flex h-1.5 w-1.5">
        <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-current opacity-60" />
        <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-current" />
      </span>
      {config.label}
    </Badge>
  );
}
