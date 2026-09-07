"use client";

import * as React from "react";
import { Layers } from "lucide-react";

import { cn } from "@/lib/utils";
import type { Cluster } from "./types";

interface GraphLegendProps {
  clusters: Cluster[];
  activeClusterId: number | null;
  onSelectCluster: (id: number | null) => void;
}

/**
 * Floating topic legend. Each row is a detected cluster (colored dot, derived
 * label, document count). Clicking a topic dives into it (highlight + zoom);
 * clicking the active topic again clears the focus.
 */
export function GraphLegend({
  clusters,
  activeClusterId,
  onSelectCluster,
}: GraphLegendProps) {
  if (clusters.length === 0) return null;

  return (
    <div className="pointer-events-auto w-56 overflow-hidden rounded-lg border border-white/10 bg-slate-950/70 shadow-xl backdrop-blur-md">
      <div className="flex items-center gap-2 border-b border-white/10 px-3 py-2.5">
        <Layers className="h-3.5 w-3.5 text-slate-400" />
        <span className="text-xs font-semibold uppercase tracking-wider text-slate-300">
          Topics
        </span>
        <span className="ml-auto text-xs tabular-nums text-slate-500">
          {clusters.length}
        </span>
      </div>
      <div className="max-h-[min(22rem,45vh)] overflow-y-auto py-1">
        {clusters.map((cluster) => {
          const active = cluster.id === activeClusterId;
          return (
            <button
              key={cluster.id}
              type="button"
              onClick={() => onSelectCluster(active ? null : cluster.id)}
              className={cn(
                "flex w-full items-center gap-2.5 px-3 py-1.5 text-left transition-colors",
                active ? "bg-white/10" : "hover:bg-white/5",
                activeClusterId != null && !active && "opacity-50",
              )}
            >
              <span
                className="h-2.5 w-2.5 shrink-0 rounded-full"
                style={{
                  backgroundColor: cluster.color,
                  boxShadow: `0 0 8px ${cluster.color}`,
                }}
              />
              <span className="truncate text-sm text-slate-200">{cluster.label}</span>
              <span className="ml-auto text-xs tabular-nums text-slate-500">
                {cluster.count}
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
