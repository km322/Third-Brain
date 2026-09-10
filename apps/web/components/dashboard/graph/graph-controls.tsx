"use client";

import * as React from "react";
import { Loader2, RotateCcw } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { Collection } from "@/lib/types";

export const ALL_COLLECTIONS = "__all__";

interface GraphControlsProps {
  collections: Collection[];
  collectionId: string;
  onCollectionChange: (value: string) => void;
  similarity: number;
  onSimilarityChange: (value: number) => void;
  onReset: () => void;
  isFetching: boolean;
}

/**
 * Compact toolbar for the graph: a collection filter, a similarity threshold
 * slider (debounced so dragging does not spam the API) and a reset control. The
 * slider tracks its own local value so it stays snappy under the thumb, while the
 * query-driving commit is what gets debounced.
 */
export function GraphControls({
  collections,
  collectionId,
  onCollectionChange,
  similarity,
  onSimilarityChange,
  onReset,
  isFetching,
}: GraphControlsProps) {
  const [localSim, setLocalSim] = React.useState(similarity);
  React.useEffect(() => setLocalSim(similarity), [similarity]);

  React.useEffect(() => {
    if (localSim === similarity) return;
    const id = setTimeout(() => onSimilarityChange(localSim), 300);
    return () => clearTimeout(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [localSim]);

  return (
    <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
      <Select value={collectionId} onValueChange={onCollectionChange}>
        <SelectTrigger className="sm:w-56">
          <SelectValue placeholder="All knowledge bases" />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={ALL_COLLECTIONS}>All knowledge bases</SelectItem>
          {collections.map((c) => (
            <SelectItem key={c.id} value={c.id}>
              {c.name}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      <div className="flex min-w-[15rem] items-center gap-3 rounded-md border bg-card px-3 py-2">
        <span className="whitespace-nowrap text-xs font-medium text-muted-foreground">
          Similarity
        </span>
        <input
          type="range"
          min={0.05}
          max={0.6}
          step={0.05}
          value={localSim}
          onChange={(e) => setLocalSim(Number(e.target.value))}
          aria-label="Minimum similarity"
          className="h-1.5 flex-1 cursor-pointer appearance-none rounded-full bg-muted accent-primary"
        />
        <span className="w-9 shrink-0 text-right text-xs tabular-nums text-foreground">
          {localSim.toFixed(2)}
        </span>
      </div>

      <Button variant="outline" size="sm" onClick={onReset} className="shrink-0">
        {isFetching ? (
          <Loader2 className="h-4 w-4 animate-spin" />
        ) : (
          <RotateCcw className="h-4 w-4" />
        )}
        Reset
      </Button>
    </div>
  );
}
