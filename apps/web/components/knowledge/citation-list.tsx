"use client";

import * as React from "react";
import Link from "next/link";
import { FileText } from "lucide-react";

import { cn } from "@/lib/utils";
import type { Citation } from "@/lib/types";

/** Route to the document (opens its chunk viewer on the Documents page). */
export function citationHref(citation: Citation): string {
  return `/dashboard/documents?doc=${citation.document_id}`;
}

interface CitationCardProps {
  citation: Citation;
  /** 1-based marker shown in the leading badge (e.g. `[3]`). */
  index: number;
  /** Register the card node so callers can scroll to it from inline markers. */
  registerRef?: (node: HTMLElement | null) => void;
  highlighted?: boolean;
  className?: string;
}

/**
 * A single attributable source: a numbered marker, the document title (linking
 * to the document), a relevance score and a snippet of the matched passage.
 */
export function CitationCard({
  citation,
  index,
  registerRef,
  highlighted,
  className,
}: CitationCardProps) {
  const scorePct = Math.round(Math.max(0, Math.min(1, citation.score)) * 100);

  return (
    <Link
      ref={registerRef}
      href={citationHref(citation)}
      className={cn(
        "group flex gap-3 rounded-lg border bg-card p-3 transition-colors hover:border-primary/40 hover:bg-accent/40",
        highlighted && "border-primary/60 ring-1 ring-primary/30",
        className,
      )}
    >
      <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-primary/10 text-xs font-semibold tabular-nums text-primary">
        {index}
      </span>
      <div className="min-w-0 flex-1 space-y-1">
        <div className="flex items-start justify-between gap-2">
          <span className="flex min-w-0 items-center gap-1.5 text-sm font-medium text-foreground">
            <FileText className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
            <span className="truncate group-hover:text-primary">
              {citation.document_title || "Untitled"}
            </span>
          </span>
          <span className="shrink-0 text-[11px] font-medium tabular-nums text-muted-foreground">
            {scorePct}% match
          </span>
        </div>
        <p className="line-clamp-3 text-xs leading-relaxed text-muted-foreground">
          {citation.snippet}
        </p>
        <p className="text-[11px] text-muted-foreground/70">
          Passage #{citation.chunk_index + 1}
        </p>
      </div>
    </Link>
  );
}

interface CitationListProps {
  citations: Citation[];
  /** Optional map of refs (keyed by 1-based index) to enable marker scrolling. */
  refMap?: React.MutableRefObject<Map<number, HTMLElement>>;
  /** 1-based index of the currently highlighted citation. */
  activeIndex?: number | null;
  className?: string;
}

/**
 * Ordered list of {@link CitationCard}s. Numbering is 1-based so the badges line
 * up with the `[n]` markers a RAG answer emits.
 */
export function CitationList({
  citations,
  refMap,
  activeIndex,
  className,
}: CitationListProps) {
  return (
    <div className={cn("space-y-2", className)}>
      {citations.map((citation, i) => {
        const index = i + 1;
        return (
          <CitationCard
            key={`${citation.document_id}-${citation.chunk_index}-${i}`}
            citation={citation}
            index={index}
            highlighted={activeIndex === index}
            registerRef={(node) => {
              if (!refMap) return;
              if (node) refMap.current.set(index, node);
              else refMap.current.delete(index);
            }}
          />
        );
      })}
    </div>
  );
}
