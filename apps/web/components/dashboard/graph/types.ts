import type { NodeObject, LinkObject } from "react-force-graph-2d";

import type { GraphNode } from "@/lib/types";

/**
 * A document node enriched with its detected community and render color. This is
 * the shape react-force-graph mutates in place (adding `x`/`y`/`vx`/`vy`), so we
 * keep our own copies rather than the objects held by the React Query cache.
 */
export type VizNodeData = GraphNode & {
  community: number;
  color: string;
};

export type VizLinkData = {
  weight: number;
};

export type VizNode = NodeObject<VizNodeData>;
export type VizLink = LinkObject<VizNodeData, VizLinkData>;

export interface VizGraphData {
  nodes: VizNode[];
  links: VizLink[];
}

/**
 * A topic cluster: a Louvain community re-indexed by size (0 = largest) with a
 * derived human label, a palette color and its members.
 */
export interface Cluster {
  id: number;
  label: string;
  color: string;
  count: number;
  nodeIds: string[];
}

/**
 * The subset of react-force-graph's imperative API we drive. Declared locally so
 * the camera controls never depend on the library's deeply-generic method types.
 */
export interface GraphInstance {
  zoom(): number;
  zoom(scale: number, durationMs?: number): unknown;
  centerAt(x?: number, y?: number, durationMs?: number): unknown;
  zoomToFit(
    durationMs?: number,
    padding?: number,
    nodeFilter?: (node: VizNode) => boolean,
  ): unknown;
  d3Force(name: string): D3Force | undefined;
}

interface D3Force {
  strength?: (value: number | ((link: VizLink) => number)) => D3Force;
  distance?: (value: number | ((link: VizLink) => number)) => D3Force;
  distanceMax?: (value: number) => D3Force;
}
