import * as React from "react";
import Graph from "graphology";
import louvain from "graphology-communities-louvain";

import type { GraphEdge, GraphNode } from "@/lib/types";

import { clusterColor } from "./graph-theme";
import type { Cluster } from "./types";

export interface ClusterResult {
  /** node id -> compact cluster id (0 = largest community). */
  communityByNode: Map<string, number>;
  /** node id -> render color. */
  colorByNode: Map<string, string>;
  /** clusters sorted by member count, descending. */
  clusters: Cluster[];
}

const EMPTY: ClusterResult = {
  communityByNode: new Map(),
  colorByNode: new Map(),
  clusters: [],
};

// Common words plus generic document/file noise that should never become a
// topic label.
const STOPWORDS = new Set([
  "the", "a", "an", "and", "or", "but", "of", "to", "in", "on", "for", "with",
  "at", "by", "from", "up", "as", "is", "are", "was", "were", "be", "been",
  "being", "it", "its", "this", "that", "these", "those", "which", "who",
  "what", "when", "where", "how", "why", "our", "your", "their", "his", "her",
  "we", "you", "they", "i", "me", "my", "not", "no", "yes", "can", "will",
  "would", "should", "could", "may", "might", "must", "do", "does", "did",
  "has", "have", "had", "into", "out", "over", "about", "than", "then", "so",
  "if", "else", "new", "old", "get", "set", "use", "using", "via", "per",
  "doc", "docs", "document", "documents", "file", "files", "note", "notes",
  "draft", "final", "copy", "version", "untitled", "readme", "page", "pages",
  "report", "overview", "summary", "guide", "intro", "introduction",
]);

function mulberry32(seed: number): () => number {
  let a = seed;
  return function () {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function significantWords(title: string): string[] {
  return title
    .toLowerCase()
    .split(/[^a-z0-9]+/)
    .filter((w) => w.length >= 3 && !STOPWORDS.has(w) && !/^\d+$/.test(w));
}

const titleCase = (w: string) =>
  w.charAt(0).toUpperCase() + w.slice(1);

/**
 * Derive a short topic label for a community: the most frequent significant
 * words across its document titles, falling back to the highest-degree member's
 * title when the titles share no common vocabulary.
 */
function clusterLabel(members: GraphNode[], isWholeGraph = false): string {
  const freq = new Map<string, number>();
  for (const m of members) {
    // Count each significant word once per document so a single long title
    // cannot dominate the topic.
    for (const w of new Set(significantWords(m.title))) {
      freq.set(w, (freq.get(w) ?? 0) + 1);
    }
  }
  const ranked = [...freq.entries()].sort(
    (a, b) => b[1] - a[1] || a[0].localeCompare(b[0]),
  );

  // Only trust a keyword label when two significant words genuinely recur across
  // the topic; a single dominant word (e.g. "call" from several on-call docs)
  // makes a weak label, so fall back to the most-connected document's title.
  const top = ranked.filter(([, c]) => c >= 2).slice(0, 2);
  if (top.length >= 2) {
    return top.map(([w]) => titleCase(w)).join(" ");
  }

  // A single community that spans the whole graph has no distinguishing topic, so
  // naming it after one member document reads as if the entire knowledge base were
  // about that file. Use a neutral label instead.
  if (isWholeGraph) return "All documents";

  const hub = members.reduce((best, m) =>
    m.degree > best.degree ? m : best,
  );
  const words = hub.title.trim().split(/\s+/).slice(0, 3).join(" ");
  const label = words || hub.title.trim() || "Untitled";
  return label.length > 26 ? `${label.slice(0, 25)}…` : label;
}

function computeClusters(
  nodes: GraphNode[],
  edges: GraphEdge[],
): ClusterResult {
  if (nodes.length === 0) return EMPTY;

  const graph = new Graph({ type: "undirected" });
  const byId = new Map<string, GraphNode>();
  for (const n of nodes) {
    byId.set(n.id, n);
    if (!graph.hasNode(n.id)) graph.addNode(n.id);
  }
  for (const e of edges) {
    if (!graph.hasNode(e.source) || !graph.hasNode(e.target)) continue;
    if (e.source === e.target) continue;
    if (graph.hasEdge(e.source, e.target)) continue;
    graph.addEdge(e.source, e.target, { weight: e.weight });
  }

  // Raw community id per node. Louvain needs edges to work; with none, every
  // document is its own island - group them all together instead.
  const rawByNode = new Map<string, number>();
  if (graph.size > 0) {
    const partition = louvain(graph, {
      getEdgeWeight: "weight",
      rng: mulberry32(0x5eed),
    }) as Record<string, number>;
    for (const [id, c] of Object.entries(partition)) rawByNode.set(id, c);
  } else {
    for (const n of nodes) rawByNode.set(n.id, 0);
  }

  // Group members by raw community, then re-index by size so the largest topic
  // is cluster 0 and gets the first palette color (stable, readable ordering).
  const groups = new Map<number, GraphNode[]>();
  for (const n of nodes) {
    const raw = rawByNode.get(n.id) ?? 0;
    const bucket = groups.get(raw);
    if (bucket) bucket.push(n);
    else groups.set(raw, [n]);
  }

  const ordered = [...groups.values()].sort(
    (a, b) => b.length - a.length || a[0].id.localeCompare(b[0].id),
  );

  const communityByNode = new Map<string, number>();
  const colorByNode = new Map<string, string>();
  const singleWholeGraph = ordered.length === 1;
  const clusters: Cluster[] = ordered.map((members, index) => {
    const color = clusterColor(index);
    for (const m of members) {
      communityByNode.set(m.id, index);
      colorByNode.set(m.id, color);
    }
    return {
      id: index,
      label: clusterLabel(members, singleWholeGraph && members.length === nodes.length),
      color,
      count: members.length,
      nodeIds: members.map((m) => m.id),
    };
  });

  return { communityByNode, colorByNode, clusters };
}

/** Memoized Louvain clustering over the current node/edge set. */
export function useGraphClusters(
  nodes: GraphNode[],
  edges: GraphEdge[],
): ClusterResult {
  return React.useMemo(() => computeClusters(nodes, edges), [nodes, edges]);
}
