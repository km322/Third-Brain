"use client";

import * as React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, ShieldAlert, ShieldCheck, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { PermissionBadge, PERMISSION_LABELS } from "@/components/governance/permission-select";
import { VisibilityBadge } from "@/components/knowledge/visibility-badge";
import { Badge, type BadgeProps } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { api, ApiError } from "@/lib/api";
import { formatNumber } from "@/lib/utils";
import type {
  DocumentItem,
  QuarantineReview,
  SecretFinding,
  Visibility,
} from "@/lib/types";

const SEVERITY_VARIANT: Record<SecretFinding["severity"], NonNullable<BadgeProps["variant"]>> = {
  high: "destructive",
  medium: "warning",
  low: "muted",
};

/** Label + reach clause for a document-level visibility override, per level. */
const VISIBILITY_OVERRIDE: Record<Visibility, { label: string; reach: string }> = {
  private: { label: "Private", reach: "readable only by people explicitly granted access" },
  team: { label: "Team", reach: "readable by members of the team it is shared with" },
  org: { label: "Organization", reach: "readable by everyone in the organization" },
  public: {
    label: "Public",
    reach: "readable by anyone, including people outside your organization",
  },
};

/**
 * Human-readable label for an audience entry's access source (`via`). Tokens may
 * carry a parenthetical scope suffix (e.g. "visibility:org (document)"); the
 * suffix is preserved and appended to the resolved base label.
 */
function viaLabel(via: string): string {
  const match = via.match(/^(.*?)\s*(\([^)]*\))$/);
  const base = match ? match[1] : via;
  const suffix = match ? ` ${match[2]}` : "";
  return `${viaBaseLabel(base)}${suffix}`;
}

function viaBaseLabel(via: string): string {
  if (via === "org-admin") return "Org admin";
  if (via === "collection-owner") return "Collection owner";
  if (via === "grant") return "Direct grant";
  if (via === "document-grant") return "Document grant";
  if (via.startsWith("visibility:team:")) {
    return `Team ${via.slice("visibility:team:".length)}`;
  }
  if (via.startsWith("visibility:")) {
    const scope = via.slice("visibility:".length);
    return `${scope.charAt(0).toUpperCase()}${scope.slice(1)} visibility`;
  }
  if (via.startsWith("team:")) return `Team ${via.slice("team:".length)}`;
  return via;
}

/** "+N more people with access." line shown when the audience list was capped. */
function audienceOverflow(review: QuarantineReview): string {
  const more = Math.max(0, review.audience.total_users - review.audience.entries.length);
  return `+${formatNumber(more)} more ${more === 1 ? "person" : "people"} with access.`;
}

/** "1 manager, 2 editors, 3 viewers" from permission_counts, most-privileged first. */
function permissionBreakdown(
  counts: QuarantineReview["audience"]["permission_counts"],
): string {
  const parts: string[] = [];
  for (const [key, noun] of [
    ["manager", "manager"],
    ["editor", "editor"],
    ["viewer", "viewer"],
  ] as const) {
    const n = counts?.[key] ?? 0;
    if (n > 0) parts.push(`${formatNumber(n)} ${n === 1 ? noun : `${noun}s`}`);
  }
  return parts.join(", ");
}

function SectionTitle({ children }: { children: React.ReactNode }) {
  return (
    <h3 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">
      {children}
    </h3>
  );
}

function ReviewSkeleton() {
  return (
    <div className="space-y-4">
      <Skeleton className="h-4 w-3/4" />
      <Skeleton className="h-16 w-full" />
      <Skeleton className="h-24 w-full" />
      <Skeleton className="h-28 w-full" />
    </div>
  );
}

interface QuarantineReviewDialogProps {
  documentId: string | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Called after the document was approved or discarded (dialog closes itself). */
  onResolved?: () => void;
}

/**
 * Review UI for a quarantined document: shows what was detected (redacted),
 * which collection it is headed into and who would be able to see it, then
 * lets an editor either approve indexing or discard the document. Nothing has
 * been indexed while a document sits in quarantine.
 */
export function QuarantineReviewDialog({
  documentId,
  open,
  onOpenChange,
  onResolved,
}: QuarantineReviewDialogProps) {
  const queryClient = useQueryClient();
  const [confirmingDiscard, setConfirmingDiscard] = React.useState(false);
  const discardButtonRef = React.useRef<HTMLButtonElement>(null);
  const confirmButtonRef = React.useRef<HTMLButtonElement>(null);
  const wasConfirming = React.useRef(false);

  React.useEffect(() => {
    if (!open) setConfirmingDiscard(false);
  }, [open]);

  // Keep keyboard focus with the discard flow as it swaps in and out: land on
  // the confirm button when it appears, and return to the trigger on "Keep".
  React.useEffect(() => {
    if (confirmingDiscard && !wasConfirming.current) {
      confirmButtonRef.current?.focus();
    } else if (!confirmingDiscard && wasConfirming.current) {
      discardButtonRef.current?.focus();
    }
    wasConfirming.current = confirmingDiscard;
  }, [confirmingDiscard]);

  const { data, isLoading, error } = useQuery<QuarantineReview>({
    queryKey: ["document-review", documentId],
    queryFn: () => api.get<QuarantineReview>(`/documents/${documentId}/review`),
    enabled: open && !!documentId,
  });

  function invalidateResolved() {
    queryClient.invalidateQueries({ queryKey: ["documents"] });
    queryClient.invalidateQueries({ queryKey: ["collection"] });
    queryClient.invalidateQueries({ queryKey: ["collections"] });
    queryClient.invalidateQueries({ queryKey: ["document-chunks", documentId] });
    queryClient.removeQueries({ queryKey: ["document-review", documentId] });
  }

  function settle(message: string, description: string) {
    toast.success(message, { description });
    invalidateResolved();
    onOpenChange(false);
    onResolved?.();
  }

  // A concurrent approve/discard by someone else resolves the document out from
  // under us (409/404). Refresh, close, and say so instead of leaving a stale
  // dialog with a dead action.
  function handleMutationError(err: unknown, fallback: string) {
    if (err instanceof ApiError && (err.status === 409 || err.status === 404)) {
      invalidateResolved();
      onOpenChange(false);
      onResolved?.();
      toast.info("Already resolved", {
        description: "Someone else already reviewed this document. The list has been refreshed.",
      });
      return;
    }
    toast.error(err instanceof ApiError ? err.message : fallback);
  }

  const approve = useMutation({
    mutationFn: () => api.post<DocumentItem>(`/documents/${documentId}/approve`),
    onSuccess: () =>
      settle("Document approved", "Indexing has started - it will be searchable shortly."),
    onError: (err) => handleMutationError(err, "Failed to approve"),
  });

  const discard = useMutation({
    mutationFn: () => api.delete(`/documents/${documentId}`),
    onSuccess: () =>
      settle("Document discarded", "The quarantined document was permanently deleted."),
    onError: (err) => handleMutationError(err, "Failed to discard"),
  });

  const busy = approve.isPending || discard.isPending;
  const review = data;
  const reviewFailed = !!error;

  return (
    <Dialog open={open} onOpenChange={(next) => !busy && onOpenChange(next)}>
      <DialogContent className="flex max-h-[85vh] max-w-2xl flex-col gap-0 p-0">
        <DialogHeader className="border-b p-6 pb-4">
          <DialogTitle className="flex items-center gap-2">
            <ShieldAlert className="h-5 w-5 text-warning" />
            Review quarantined document
          </DialogTitle>
          <DialogDescription>
            {review ? (
              <>
                <span className="font-medium text-foreground">
                  {review.document.title}
                </span>{" "}
                was quarantined before indexing because it appears to contain
                secrets. Nothing has been indexed or made searchable.
              </>
            ) : (
              "This document was quarantined before indexing because it appears " +
              "to contain secrets. Nothing has been indexed or made searchable."
            )}
          </DialogDescription>
        </DialogHeader>

        <ScrollArea className="min-h-0 flex-1">
          <div className="space-y-6 p-6">
            {isLoading ? (
              <ReviewSkeleton />
            ) : error ? (
              <p className="text-sm text-destructive">
                {error instanceof ApiError
                  ? error.message
                  : "Couldn't load the review details. Try reopening this document."}
              </p>
            ) : review ? (
              <>
                <section className="space-y-2">
                  <SectionTitle>Where it is going</SectionTitle>
                  <div className="flex flex-wrap items-center gap-2 rounded-lg border bg-card p-3">
                    <span className="text-sm font-medium text-foreground">
                      {review.collection.name}
                    </span>
                    <VisibilityBadge visibility={review.collection.visibility} />
                    <span className="text-xs text-muted-foreground">
                      Default permission:{" "}
                      {PERMISSION_LABELS[review.collection.default_permission]}
                    </span>
                  </div>
                  {review.document.visibility &&
                  review.document.visibility !== review.collection.visibility ? (
                    <div className="flex items-start gap-2 rounded-lg border border-warning/40 bg-warning/10 p-3 text-xs text-warning">
                      <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" />
                      <span>
                        This document overrides the collection visibility to{" "}
                        {VISIBILITY_OVERRIDE[review.document.visibility].label} - it will be{" "}
                        {VISIBILITY_OVERRIDE[review.document.visibility].reach} once indexed.
                      </span>
                    </div>
                  ) : null}
                </section>

                <section className="space-y-2">
                  <SectionTitle>Who will be able to see it</SectionTitle>
                  {review.audience.entries.length > 0 ? (
                    <>
                      {permissionBreakdown(review.audience.permission_counts) ? (
                        <p className="text-xs text-muted-foreground">
                          {formatNumber(review.audience.total_users)}{" "}
                          {review.audience.total_users === 1 ? "person" : "people"} with
                          access: {permissionBreakdown(review.audience.permission_counts)}.
                        </p>
                      ) : null}
                      <ul className="divide-y rounded-lg border bg-card">
                        {review.audience.entries.map((entry) => (
                          <li
                            key={entry.user_id}
                            className="flex items-center justify-between gap-3 px-3 py-2"
                          >
                            <div className="min-w-0">
                              <p className="truncate text-sm font-medium text-foreground">
                                {entry.name || entry.email}
                              </p>
                              {entry.name ? (
                                <p className="truncate text-xs text-muted-foreground">
                                  {entry.email}
                                </p>
                              ) : null}
                            </div>
                            <div className="flex shrink-0 items-center gap-2">
                              <PermissionBadge level={entry.permission} />
                              <span className="text-xs text-muted-foreground">
                                {viaLabel(entry.via)}
                              </span>
                            </div>
                          </li>
                        ))}
                      </ul>
                      {review.audience.truncated ? (
                        <p className="text-xs text-muted-foreground">
                          {audienceOverflow(review)}
                        </p>
                      ) : null}
                    </>
                  ) : review.audience.total_users > 0 ? (
                    <div className="rounded-lg border bg-card p-3">
                      <p className="text-sm font-medium text-foreground">
                        {formatNumber(review.audience.total_users)}{" "}
                        {review.audience.total_users === 1 ? "person" : "people"} will be
                        able to see it once indexed.
                      </p>
                      {permissionBreakdown(review.audience.permission_counts) ? (
                        <p className="mt-1 text-xs text-muted-foreground">
                          {permissionBreakdown(review.audience.permission_counts)}
                        </p>
                      ) : null}
                    </div>
                  ) : (
                    <p className="text-sm text-muted-foreground">
                      No one else currently has access to this collection.
                    </p>
                  )}
                  {review.audience.note ? (
                    <p className="text-xs text-muted-foreground">
                      {review.audience.note}
                    </p>
                  ) : null}
                </section>

                <section className="space-y-2">
                  <SectionTitle>What was detected</SectionTitle>
                  <div className="overflow-hidden rounded-lg border bg-card">
                    <Table>
                      <TableHeader>
                        <TableRow className="hover:bg-transparent">
                          <TableHead>Type</TableHead>
                          <TableHead>Severity</TableHead>
                          <TableHead>Sample</TableHead>
                          <TableHead className="text-right">Matches</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {review.findings.map((finding) => (
                          <TableRow
                            key={finding.detector}
                            className="hover:bg-transparent"
                          >
                            <TableCell className="text-sm font-medium text-foreground">
                              {finding.label}
                            </TableCell>
                            <TableCell>
                              <Badge
                                variant={SEVERITY_VARIANT[finding.severity] ?? "muted"}
                                className="capitalize"
                              >
                                {finding.severity}
                              </Badge>
                            </TableCell>
                            <TableCell>
                              <div className="space-y-1">
                                {finding.samples.map((sample, i) => (
                                  <div
                                    key={`${finding.detector}-${i}`}
                                    className="flex items-baseline gap-2"
                                  >
                                    <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-xs">
                                      {sample.redacted}
                                    </code>
                                    <span className="text-xs text-muted-foreground">
                                      line {formatNumber(sample.line)}
                                    </span>
                                  </div>
                                ))}
                              </div>
                            </TableCell>
                            <TableCell className="text-right tabular-nums text-muted-foreground">
                              {formatNumber(finding.occurrences)}
                            </TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </div>
                  {review.truncated ? (
                    <p className="text-xs text-muted-foreground">
                      The scan stopped early - there may be more matches than shown here.
                    </p>
                  ) : null}
                </section>
              </>
            ) : null}
          </div>
        </ScrollArea>

        <div className="flex flex-col-reverse gap-2 border-t p-6 pt-4 sm:flex-row sm:items-center sm:justify-between">
          {confirmingDiscard ? (
            <div className="flex flex-wrap items-center gap-2">
              <span role="alert" className="text-sm text-muted-foreground">
                Permanently delete this document?
              </span>
              <Button
                ref={confirmButtonRef}
                variant="destructive"
                size="sm"
                onClick={() => discard.mutate()}
                disabled={busy || !documentId || reviewFailed}
              >
                {discard.isPending ? (
                  <>
                    <Loader2 className="h-4 w-4 animate-spin" />
                    Discarding…
                  </>
                ) : (
                  "Yes, discard"
                )}
              </Button>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setConfirmingDiscard(false)}
                disabled={busy}
              >
                Keep
              </Button>
            </div>
          ) : (
            <Button
              ref={discardButtonRef}
              variant="outline"
              className="text-destructive hover:text-destructive"
              onClick={() => setConfirmingDiscard(true)}
              disabled={busy || !review || reviewFailed}
            >
              <Trash2 className="h-4 w-4" />
              Discard document
            </Button>
          )}
          <Button
            onClick={() => approve.mutate()}
            disabled={busy || !review || confirmingDiscard || !documentId || reviewFailed}
          >
            {approve.isPending ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" />
                Approving…
              </>
            ) : (
              <>
                <ShieldCheck className="h-4 w-4" />
                Index anyway
              </>
            )}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
