"use client";

import * as React from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import {
  ArrowUpRight,
  FileText,
  Globe,
  Loader2,
  Network,
  Plug,
  Type,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api";
import type { SourceType } from "@/lib/types";
import { formatDate, formatNumber } from "@/lib/utils";

import type { Cluster, VizNode } from "./types";

const SOURCE_META: Record<SourceType, { label: string; icon: typeof FileText }> = {
  file: { label: "File", icon: FileText },
  text: { label: "Text", icon: Type },
  url: { label: "URL", icon: Globe },
  connector: { label: "Connector", icon: Plug },
};

interface DocumentChunk {
  id: string;
  chunk_index: number;
  content: string;
}

interface NodeDetailSheetProps {
  node: VizNode | null;
  cluster?: Cluster;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onExpand: (id: string) => void;
  expanding: boolean;
}

function MetaRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3 py-2">
      <span className="text-sm text-muted-foreground">{label}</span>
      <span className="text-sm font-medium text-foreground">{children}</span>
    </div>
  );
}

/**
 * Slide-over showing the exact document behind a node, plus a jumping-off point
 * to expand its local web or open it in the documents view. The first indexed
 * chunk is pulled for a real content preview, best-effort.
 */
export function NodeDetailSheet({
  node,
  cluster,
  open,
  onOpenChange,
  onExpand,
  expanding,
}: NodeDetailSheetProps) {
  const { data: chunks, isLoading } = useQuery<DocumentChunk[]>({
    queryKey: ["document-chunks", node?.id],
    queryFn: () => api.get<DocumentChunk[]>(`/documents/${node!.id}/chunks`),
    enabled: open && !!node,
    staleTime: 60_000,
  });

  const snippet = chunks?.[0]?.content?.trim();
  const source = node ? (SOURCE_META[node.source_type] ?? SOURCE_META.file) : null;
  const SourceIcon = source?.icon ?? FileText;

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="flex w-full flex-col gap-0 p-0 sm:max-w-md">
        {node ? (
          <>
            <SheetHeader className="space-y-3 border-b p-6">
              {cluster ? (
                <div className="flex items-center gap-2">
                  <span
                    className="h-2.5 w-2.5 rounded-full"
                    style={{
                      backgroundColor: cluster.color,
                      boxShadow: `0 0 8px ${cluster.color}`,
                    }}
                  />
                  <span className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
                    {cluster.label}
                  </span>
                </div>
              ) : null}
              <SheetTitle className="pr-8 text-left leading-snug">
                {node.title}
              </SheetTitle>
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant="secondary">{node.collection_name}</Badge>
                <Badge variant="outline" className="gap-1">
                  <SourceIcon className="h-3 w-3" />
                  {source?.label}
                </Badge>
              </div>
            </SheetHeader>

            <ScrollArea className="flex-1">
              <div className="space-y-6 p-6">
                <div className="divide-y">
                  <MetaRow label="Connections">
                    <span className="tabular-nums">{formatNumber(node.degree)}</span>
                  </MetaRow>
                  <MetaRow label="Chunks">
                    <span className="tabular-nums">{formatNumber(node.chunk_count)}</span>
                  </MetaRow>
                  <MetaRow label="Added">{formatDate(node.created_at)}</MetaRow>
                </div>

                <div className="space-y-2">
                  <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                    Preview
                  </p>
                  {isLoading ? (
                    <div className="space-y-2">
                      <Skeleton className="h-3 w-full" />
                      <Skeleton className="h-3 w-full" />
                      <Skeleton className="h-3 w-3/4" />
                    </div>
                  ) : snippet ? (
                    <p className="line-clamp-6 whitespace-pre-wrap break-words text-sm leading-relaxed text-muted-foreground">
                      {snippet}
                    </p>
                  ) : (
                    <p className="text-sm text-muted-foreground">
                      No preview available for this document.
                    </p>
                  )}
                </div>
              </div>
            </ScrollArea>

            <div className="flex flex-col gap-2 border-t p-6">
              <Button
                variant="secondary"
                onClick={() => onExpand(node.id)}
                disabled={expanding}
              >
                {expanding ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Network className="h-4 w-4" />
                )}
                Expand connections
              </Button>
              <Button asChild>
                <Link href={`/dashboard/documents?doc=${node.id}`}>
                  Open document
                  <ArrowUpRight className="h-4 w-4" />
                </Link>
              </Button>
            </div>
          </>
        ) : null}
      </SheetContent>
    </Sheet>
  );
}
