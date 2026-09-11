"use client";

import * as React from "react";
import Link from "next/link";
import { Loader2, Network, Waypoints } from "lucide-react";

import { EmptyState } from "@/components/dashboard/empty-state";
import { PageHeader } from "@/components/dashboard/page-header";
import { Button } from "@/components/ui/button";
import { useApiQuery, useDocumentNeighbors, useGraph } from "@/lib/hooks";
import type { Collection, GraphEdge, GraphNode } from "@/lib/types";
import { formatNumber } from "@/lib/utils";

import { GraphCanvas, type GraphCanvasHandle } from "./graph-canvas";
import { ALL_COLLECTIONS, GraphControls } from "./graph-controls";
import { GraphLegend } from "./graph-legend";
import { NodeDetailSheet } from "./node-detail-sheet";
import { DIM_RGB } from "./graph-theme";
import type { VizGraphData, VizLink, VizNode } from "./types";
import { useGraphClusters } from "./use-graph-clusters";

const DEFAULT_SIMILARITY = 0.15;

interface Expansions {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

const EMPTY_EXPANSIONS: Expansions = { nodes: [], edges: [] };

const edgeKey = (a: string, b: string) => (a < b ? `${a}|${b}` : `${b}|${a}`);

/** Loading placeholder that previews the dark, glowing canvas. */
function GraphSkeleton() {
  return (
    <div className="relative flex h-[calc(100vh-16rem)] min-h-[520px] items-center justify-center overflow-hidden rounded-xl border bg-[#070912]">
      <div className="absolute inset-0 bg-[radial-gradient(ellipse_at_center,rgba(124,58,237,0.16),transparent_60%)]" />
      <div className="flex flex-col items-center gap-3 text-slate-400">
        <Loader2 className="h-6 w-6 animate-spin" />
        <p className="text-sm">Mapping your knowledge…</p>
      </div>
    </div>
  );
}

/**
 * The Knowledge Graph page: controls, the canvas itself, its legend and the node
 * detail sheet.
 *
 * Expanding a node fetches its neighbors, merges them into the expansion set and
 * then clears the trigger; a change of query scope (collection or similarity)
 * invalidates any manual expansions and focus. The server graph and those
 * expansions are combined into one drawn set - deduped, with canonicalized edges
 * and degree recomputed from the drawn edges - so node size and the "Connections"
 * count reflect exactly what is on screen. That set is reconciled into stable
 * per-id viz objects so the force layout keeps node positions across similarity
 * tweaks and expansions instead of restarting.
 *
 * A vignette is painted over the canvas for depth; it never intercepts pointer
 * events.
 */
export function KnowledgeGraph() {
  const [collectionId, setCollectionId] = React.useState<string>(ALL_COLLECTIONS);
  const [minSimilarity, setMinSimilarity] = React.useState<number>(DEFAULT_SIMILARITY);
  const [selectedId, setSelectedId] = React.useState<string | null>(null);
  const [hoverId, setHoverId] = React.useState<string | null>(null);
  const [activeClusterId, setActiveClusterId] = React.useState<number | null>(null);
  const [expansions, setExpansions] = React.useState<Expansions>(EMPTY_EXPANSIONS);
  const [expandId, setExpandId] = React.useState<string | null>(null);

  const canvasRef = React.useRef<GraphCanvasHandle>(null);

  const { data: collections } = useApiQuery<Collection[]>(
    ["collections"],
    "/collections",
  );

  const graphQuery = useGraph({
    collection_id: collectionId === ALL_COLLECTIONS ? undefined : collectionId,
    min_similarity: minSimilarity,
  });

  const neighborsQuery = useDocumentNeighbors(expandId, 16);

  React.useEffect(() => {
    if (!expandId || !neighborsQuery.data) return;
    const { nodes, edges } = neighborsQuery.data;
    setExpansions((prev) => ({
      nodes: [...prev.nodes, ...nodes],
      edges: [...prev.edges, ...edges],
    }));
    setExpandId(null);
  }, [expandId, neighborsQuery.data]);

  React.useEffect(() => {
    setExpansions(EMPTY_EXPANSIONS);
    setSelectedId(null);
    setActiveClusterId(null);
    setExpandId(null);
  }, [collectionId, minSimilarity]);

  const { mergedNodes, mergedEdges, adjacency } = React.useMemo(() => {
    const nodeMap = new Map<string, GraphNode>();
    for (const n of graphQuery.data?.nodes ?? []) nodeMap.set(n.id, n);
    for (const n of expansions.nodes) if (!nodeMap.has(n.id)) nodeMap.set(n.id, n);

    const edgeMap = new Map<string, GraphEdge>();
    const consider = (e: GraphEdge) => {
      if (e.source === e.target) return;
      if (!nodeMap.has(e.source) || !nodeMap.has(e.target)) return;
      const key = edgeKey(e.source, e.target);
      if (edgeMap.has(key)) return;
      const [source, target] =
        e.source < e.target ? [e.source, e.target] : [e.target, e.source];
      edgeMap.set(key, { source, target, weight: e.weight });
    };
    for (const e of graphQuery.data?.edges ?? []) consider(e);
    for (const e of expansions.edges) consider(e);

    const adjacency = new Map<string, Set<string>>();
    const degree = new Map<string, number>();
    for (const id of nodeMap.keys()) {
      adjacency.set(id, new Set());
      degree.set(id, 0);
    }
    for (const e of edgeMap.values()) {
      adjacency.get(e.source)!.add(e.target);
      adjacency.get(e.target)!.add(e.source);
      degree.set(e.source, (degree.get(e.source) ?? 0) + 1);
      degree.set(e.target, (degree.get(e.target) ?? 0) + 1);
    }

    const mergedNodes = [...nodeMap.values()].map((n) => ({
      ...n,
      degree: degree.get(n.id) ?? 0,
    }));

    return {
      mergedNodes,
      mergedEdges: [...edgeMap.values()],
      adjacency,
    };
  }, [graphQuery.data, expansions]);

  const { communityByNode, colorByNode, clusters } = useGraphClusters(
    mergedNodes,
    mergedEdges,
  );

  const registryRef = React.useRef<{
    nodes: Map<string, VizNode>;
    links: Map<string, VizLink>;
  }>({ nodes: new Map(), links: new Map() });

  const graphData = React.useMemo<VizGraphData>(() => {
    const reg = registryRef.current;
    const seenNodes = new Set<string>();
    const nodes: VizNode[] = [];
    for (const n of mergedNodes) {
      seenNodes.add(n.id);
      const community = communityByNode.get(n.id) ?? 0;
      const color = colorByNode.get(n.id) ?? `rgb(${DIM_RGB})`;
      let obj = reg.nodes.get(n.id);
      if (!obj) {
        obj = { ...n, community, color };
        reg.nodes.set(n.id, obj);
      } else {
        obj.title = n.title;
        obj.degree = n.degree;
        obj.chunk_count = n.chunk_count;
        obj.collection_id = n.collection_id;
        obj.collection_name = n.collection_name;
        obj.source_type = n.source_type;
        obj.created_at = n.created_at;
        obj.community = community;
        obj.color = color;
      }
      nodes.push(obj);
    }
    for (const id of [...reg.nodes.keys()]) {
      if (!seenNodes.has(id)) reg.nodes.delete(id);
    }

    const seenLinks = new Set<string>();
    const links: VizLink[] = [];
    for (const e of mergedEdges) {
      const key = edgeKey(e.source, e.target);
      seenLinks.add(key);
      let link = reg.links.get(key);
      if (!link) {
        link = { source: e.source, target: e.target, weight: e.weight };
        reg.links.set(key, link);
      } else {
        link.weight = e.weight;
      }
      links.push(link);
    }
    for (const key of [...reg.links.keys()]) {
      if (!seenLinks.has(key)) reg.links.delete(key);
    }

    return { nodes, links };
  }, [mergedNodes, mergedEdges, communityByNode, colorByNode]);

  const selectedNode = selectedId
    ? (graphData.nodes.find((n) => n.id === selectedId) ?? null)
    : null;
  const selectedCluster =
    selectedNode != null
      ? clusters.find((c) => c.id === selectedNode.community)
      : undefined;

  const handleSelectNode = React.useCallback((node: VizNode | null) => {
    if (!node) return;
    setSelectedId(node.id);
    setActiveClusterId(null);
    canvasRef.current?.focusNode(node.id);
  }, []);

  const handleSelectCluster = React.useCallback((id: number | null) => {
    setActiveClusterId(id);
    setSelectedId(null);
    if (id == null) canvasRef.current?.fitAll();
    else canvasRef.current?.focusCluster(id);
  }, []);

  const handleBackgroundClick = React.useCallback(() => {
    setSelectedId(null);
    setActiveClusterId(null);
    setHoverId(null);
    canvasRef.current?.fitAll();
  }, []);

  const handleReset = React.useCallback(() => {
    setCollectionId(ALL_COLLECTIONS);
    setMinSimilarity(DEFAULT_SIMILARITY);
    setSelectedId(null);
    setActiveClusterId(null);
    setHoverId(null);
    setExpansions(EMPTY_EXPANSIONS);
    canvasRef.current?.fitAll();
  }, []);

  const total = graphQuery.data;
  const truncated = total?.truncated ?? false;

  return (
    <div className="space-y-4">
      <PageHeader
        title="Knowledge Graph"
        description="Every document you can see, wired together by meaning. Clusters are topics your knowledge naturally forms; zoom in to reach the exact files."
      />

      <GraphControls
        collections={collections ?? []}
        collectionId={collectionId}
        onCollectionChange={setCollectionId}
        similarity={minSimilarity}
        onSimilarityChange={setMinSimilarity}
        onReset={handleReset}
        isFetching={graphQuery.isFetching}
      />

      {graphQuery.isLoading ? (
        <GraphSkeleton />
      ) : graphQuery.isError ? (
        <EmptyState
          icon={Waypoints}
          title="Couldn't load the graph"
          description="Something went wrong building your knowledge map. Try again in a moment."
        />
      ) : mergedNodes.length === 0 ? (
        <EmptyState
          icon={Waypoints}
          title="Nothing to map yet"
          description="Once documents finish indexing, they'll appear here as a living map of your knowledge."
          actions={
            <Button asChild>
              <Link href="/dashboard/documents">Add documents</Link>
            </Button>
          }
        />
      ) : (
        <div className="relative h-[calc(100vh-16rem)] min-h-[520px] overflow-hidden rounded-xl border bg-[#070912] ring-1 ring-inset ring-white/5">
          <GraphCanvas
            ref={canvasRef}
            data={graphData}
            clusters={clusters}
            adjacency={adjacency}
            hoverId={hoverId}
            selectedId={selectedId}
            activeClusterId={activeClusterId}
            onHoverNode={setHoverId}
            onSelectNode={handleSelectNode}
            onBackgroundClick={handleBackgroundClick}
          />

          <div className="pointer-events-none absolute inset-0 bg-[radial-gradient(ellipse_at_center,transparent_55%,rgba(3,4,10,0.65))]" />

          <div className="pointer-events-none absolute left-3 top-3">
            <GraphLegend
              clusters={clusters}
              activeClusterId={activeClusterId}
              onSelectCluster={handleSelectCluster}
            />
          </div>

          <div className="pointer-events-none absolute right-3 top-3 flex items-center gap-1.5 rounded-md border border-white/10 bg-slate-950/60 px-2.5 py-1.5 text-xs text-slate-300 backdrop-blur-md">
            <Network className="h-3.5 w-3.5 text-slate-400" />
            <span className="tabular-nums">{formatNumber(graphData.nodes.length)}</span>
            <span className="text-slate-500">docs</span>
            <span className="text-slate-600">·</span>
            <span className="tabular-nums">{formatNumber(graphData.links.length)}</span>
            <span className="text-slate-500">links</span>
          </div>

          {truncated && total ? (
            <div className="pointer-events-none absolute bottom-3 left-3 rounded-md border border-white/10 bg-slate-950/60 px-2.5 py-1.5 text-xs text-slate-400 backdrop-blur-md">
              Showing {formatNumber(graphData.nodes.length)} of{" "}
              {formatNumber(total.total_visible)} documents
            </div>
          ) : null}

          <div className="pointer-events-none absolute bottom-3 right-3 hidden text-xs text-slate-500 sm:block">
            Scroll to zoom · drag to pan · click a node
          </div>
        </div>
      )}

      <NodeDetailSheet
        node={selectedNode}
        cluster={selectedCluster}
        open={selectedNode != null}
        onOpenChange={(open) => {
          if (!open) setSelectedId(null);
        }}
        onExpand={setExpandId}
        expanding={neighborsQuery.isFetching && expandId != null}
      />
    </div>
  );
}
