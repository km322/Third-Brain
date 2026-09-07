"use client";

import * as React from "react";
import {
  AlertTriangle,
  Archive,
  CheckCircle2,
  Clock,
  Loader2,
  ShieldAlert,
  type LucideIcon,
} from "lucide-react";

import { Badge, type BadgeProps } from "@/components/ui/badge";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import type { DocumentStatus } from "@/lib/types";

type StatusConfig = {
  label: string;
  variant: NonNullable<BadgeProps["variant"]>;
  icon: LucideIcon;
  /** Spin/pulse the icon for in-flight states. */
  animate?: boolean;
};

const STATUS: Record<DocumentStatus, StatusConfig> = {
  pending: { label: "Pending", variant: "muted", icon: Clock, animate: true },
  processing: {
    label: "Processing",
    variant: "info",
    icon: Loader2,
    animate: true,
  },
  indexed: { label: "Indexed", variant: "success", icon: CheckCircle2 },
  failed: { label: "Failed", variant: "destructive", icon: AlertTriangle },
  archived: { label: "Archived", variant: "muted", icon: Archive },
  quarantined: { label: "Quarantined", variant: "warning", icon: ShieldAlert },
};

const QUARANTINE_TOOLTIP =
  "Possible secrets were detected in this document, so it was held from " +
  "indexing. Nothing is searchable until an editor reviews it and decides " +
  "to index or discard it.";

/**
 * True while a document is still being ingested - used to drive list polling.
 * Quarantined is a terminal state (it waits on a human), so it is excluded.
 */
export function isProcessingStatus(status: DocumentStatus): boolean {
  return status === "pending" || status === "processing";
}

interface DocumentStatusBadgeProps {
  status: DocumentStatus;
  /** Failure detail shown in a tooltip when `status === "failed"`. */
  error?: string | null;
  className?: string;
}

/**
 * Compact pill communicating a document's ingestion state. In-flight states
 * (pending / processing) animate their icon; failures expose the error in a
 * tooltip and quarantined explains the hold, so the table stays scannable.
 */
export function DocumentStatusBadge({
  status,
  error,
  className,
}: DocumentStatusBadgeProps) {
  const config = STATUS[status] ?? STATUS.pending;
  const Icon = config.icon;

  const badge = (
    <Badge variant={config.variant} className={cn("gap-1", className)}>
      <Icon
        className={cn(
          "h-3 w-3",
          config.animate && (status === "processing" ? "animate-spin" : "animate-pulse"),
        )}
      />
      {config.label}
    </Badge>
  );

  const tooltip =
    status === "failed" && error
      ? error
      : status === "quarantined"
        ? QUARANTINE_TOOLTIP
        : null;

  if (tooltip) {
    return (
      <TooltipProvider delayDuration={150}>
        <Tooltip>
          <TooltipTrigger asChild>
            <span className="inline-flex cursor-help">{badge}</span>
          </TooltipTrigger>
          <TooltipContent className="max-w-xs break-words">{tooltip}</TooltipContent>
        </Tooltip>
      </TooltipProvider>
    );
  }

  return badge;
}
