"use client";

import * as React from "react";
import { toast } from "sonner";

import { isProcessingStatus } from "@/components/knowledge/document-status-badge";
import type { DocumentItem, DocumentStatus } from "@/lib/types";

/**
 * Fires a warning toast when a visible document transitions from an in-flight
 * status (pending / processing) to quarantined, so the person who just added
 * it learns immediately that it was held for review. Pass `onReview` to give
 * the toast a "Review" action that opens the quarantine review dialog.
 *
 * Only transitions observed within this mount trigger the toast - documents
 * that are already quarantined when a page loads stay silent (their badge and
 * row action cover that case). Each document fires at most once: once it has
 * been seen quarantined, a later stale snapshot that appears to rewind its
 * status is ignored, so the toast never repeats.
 */
export function useQuarantineAlert(
  documents: DocumentItem[] | undefined,
  onReview?: (doc: DocumentItem) => void,
): void {
  const previous = React.useRef<Map<string, DocumentStatus>>(new Map());
  const seenQuarantined = React.useRef<Set<string>>(new Set());
  const onReviewRef = React.useRef(onReview);
  onReviewRef.current = onReview;

  React.useEffect(() => {
    if (!documents) return;
    for (const doc of documents) {
      // Quarantine is terminal for this hook. Once a document has been recorded
      // as quarantined, skip it entirely: a stale cached snapshot (placeholderData
      // or a pagination switch) can momentarily show it back in an in-flight
      // status, which would otherwise rewind the tracked status and re-fire the
      // toast. Only forward transitions update the ref map.
      if (seenQuarantined.current.has(doc.id)) continue;

      const prev = previous.current.get(doc.id);
      if (
        doc.status === "quarantined" &&
        prev !== undefined &&
        isProcessingStatus(prev)
      ) {
        const review = onReviewRef.current;
        toast.warning("Possible secrets detected", {
          description: `"${doc.title}" was quarantined before indexing. Review it to decide what happens.`,
          action: review
            ? { label: "Review", onClick: () => review(doc) }
            : undefined,
        });
      }
      if (doc.status === "quarantined") {
        seenQuarantined.current.add(doc.id);
      }
      previous.current.set(doc.id, doc.status);
    }
  }, [documents]);
}
