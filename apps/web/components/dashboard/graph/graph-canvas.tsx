"use client";

import * as React from "react";
import dynamic from "next/dynamic";

import {
  CANVAS_BG,
  DIM_RGB,
  EDGE_RGB,
  clusterLabelOpacity,
  nodeLabelOpacity,
  nodeRadius,
  rgba,
} from "./graph-theme";
import type { Cluster, GraphInstance, VizGraphData, VizNode } from "./types";

const ForceGraphClient = dynamic(() => import("./graph-force-graph"), {
  ssr: false,
  loading: () => null,
});

const TAU = Math.PI * 2;

/**
 * How long the render loop keeps running after the last interaction before it
 * parks itself again (see the render-loop notes on {@link GraphCanvasInner}).
 */
const IDLE_PAUSE_MS = 3000;

/** force-graph's animation-loop controls. They live off {@link GraphInstance}
 * (which stays camera-focused) but are always present on the runtime instance. */
type AnimationControls = {
  pauseAnimation?: () => void;
  resumeAnimation?: () => void;
};

export interface GraphCanvasHandle {
  focusNode: (id: string) => void;
  focusCluster: (clusterId: number) => void;
  fitAll: () => void;
}

interface GraphCanvasProps {
  data: VizGraphData;
  clusters: Cluster[];
  adjacency: Map<string, Set<string>>;
  hoverId: string | null;
  selectedId: string | null;
  activeClusterId: number | null;
  onHoverNode: (id: string | null) => void;
  onSelectNode: (node: VizNode | null) => void;
  onBackgroundClick: () => void;
}

const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));

const truncate = (s: string, n: number) => (s.length > n ? `${s.slice(0, n - 1)}…` : s);

/** Resolve a link endpoint to its node id, whether it is still a string or has
 * already been hydrated into a node object by the force engine. */
function endId(end: string | number | VizNode | undefined): string {
  if (end == null) return "";
  if (typeof end === "object") return String(end.id ?? "");
  return String(end);
}

/** Track a container's pixel size so the canvas can fill it responsively. */
function useElementSize() {
  const ref = React.useRef<HTMLDivElement | null>(null);
  const [size, setSize] = React.useState({ width: 0, height: 0 });

  React.useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const observer = new ResizeObserver((entries) => {
      const rect = entries[0]?.contentRect;
      if (rect) {
        setSize({
          width: Math.floor(rect.width),
          height: Math.floor(rect.height),
        });
      }
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  return { ref, ...size };
}

/**
 * The graph canvas: a force-directed rendering of the document web, with semantic
 * zoom, focus highlighting and an imperative camera handle.
 *
 * `dataRef` and its neighbours are live copies of the current props, read by the
 * imperative handle and the per-frame paint callbacks. `baseZoomRef` records the
 * zoom the full graph fits at; the semantic-zoom label thresholds are expressed
 * relative to it, so the default framing always reads as the "topics" view
 * regardless of how many documents (and thus how tight a fit) there are. Focusing
 * a node zooms in past the node-label threshold, relative to that fit baseline, so
 * the selected document and its neighbours read as individual files.
 *
 * Cluster membership sets are kept so a whole topic can be highlighted at once,
 * and the focus geometry is recomputed each render - the paint callbacks close
 * over it, so hovering and selecting repaint without restarting the simulation.
 * The view is reframed whenever the underlying data set changes.
 *
 * The render loop is parked when it has nothing to do, because force-graph@1.51.4
 * re-arms requestAnimationFrame every frame for the whole life of the mounted
 * graph: even a fully settled graph keeps waking the tab ~60x/s. The loop is woken
 * around interaction, camera moves and highlight changes, then idles back to sleep
 * after `IDLE_PAUSE_MS`. `engineStoppedRef` keeps an idle timer from freezing a
 * layout that is still cooling - only a stopped engine is safe to pause - so while
 * the simulation is settling, `onEngineStop` owns scheduling the pause and we
 * never cancel a live layout. New data and node drags both reheat the simulation
 * and clear that flag. `onEngineStop` defers its pause rather than calling it
 * inline, because force-graph re-arms requestAnimationFrame at the end of that
 * very animate() cycle, so a synchronous pause would be immediately undone.
 *
 * The wake listeners sit on the sizing container (it always spans the canvas), so
 * they fire before the graph instance exists and while the loop is paused;
 * `onNodeHover` is dispatched from inside the loop, so resuming on pointermove is
 * what keeps hover highlighting alive after an idle pause. Hover, selection and
 * cluster highlighting can also change from outside the canvas (closing the node
 * sheet, or the legend selecting a cluster), so the loop is woken on any such
 * change and the dimming and labels update even if the graph had parked itself. A
 * background tab shouldn't burn frames either: the loop hard-pauses when the
 * document is hidden and wakes when it is shown again, and the idle timer is
 * dropped on unmount so it never fires against a torn-down instance.
 */
function GraphCanvasInner(
  {
    data,
    clusters,
    adjacency,
    hoverId,
    selectedId,
    activeClusterId,
    onHoverNode,
    onSelectNode,
    onBackgroundClick,
  }: GraphCanvasProps,
  forwardedRef: React.ForwardedRef<GraphCanvasHandle>,
) {
  const { ref: sizeRef, width, height } = useElementSize();
  const fgRef = React.useRef<GraphInstance | undefined>(undefined);

  const dataRef = React.useRef(data);
  dataRef.current = data;
  const fitPendingRef = React.useRef(true);
  const userMovedRef = React.useRef(false);

  const baseZoomRef = React.useRef(1);
  const captureBaseZoom = React.useCallback(() => {
    window.setTimeout(() => {
      const z = fgRef.current?.zoom?.();
      if (typeof z === "number" && z > 0) baseZoomRef.current = z;
    }, 560);
  }, []);
  const relZoom = React.useCallback(
    (scale: number) => scale / (baseZoomRef.current || 1),
    [],
  );

  const idleTimerRef = React.useRef<number | null>(null);
  const engineStoppedRef = React.useRef(false);

  const pauseGraph = React.useCallback(() => {
    const fg = fgRef.current as (GraphInstance & AnimationControls) | undefined;
    fg?.pauseAnimation?.();
  }, []);

  const scheduleIdlePause = React.useCallback(() => {
    if (idleTimerRef.current != null) window.clearTimeout(idleTimerRef.current);
    idleTimerRef.current = window.setTimeout(() => {
      idleTimerRef.current = null;
      if (engineStoppedRef.current) pauseGraph();
    }, IDLE_PAUSE_MS);
  }, [pauseGraph]);

  const resumeGraph = React.useCallback(() => {
    const fg = fgRef.current as (GraphInstance & AnimationControls) | undefined;
    fg?.resumeAnimation?.();
    scheduleIdlePause();
  }, [scheduleIdlePause]);

  React.useEffect(() => {
    const el = sizeRef.current;
    if (!el) return;
    const wake = () => resumeGraph();
    const opts: AddEventListenerOptions = { passive: true };
    const events = ["pointermove", "pointerdown", "wheel", "touchstart"] as const;
    for (const type of events) el.addEventListener(type, wake, opts);
    return () => {
      for (const type of events) el.removeEventListener(type, wake, opts);
    };
  }, [resumeGraph, sizeRef]);

  React.useEffect(() => {
    const onVisibility = () => {
      if (document.hidden) pauseGraph();
      else resumeGraph();
    };
    document.addEventListener("visibilitychange", onVisibility);
    return () => document.removeEventListener("visibilitychange", onVisibility);
  }, [pauseGraph, resumeGraph]);

  React.useEffect(
    () => () => {
      if (idleTimerRef.current != null) window.clearTimeout(idleTimerRef.current);
    },
    [],
  );

  const clusterMembers = React.useMemo(() => {
    const map = new Map<number, Set<string>>();
    for (const c of clusters) map.set(c.id, new Set(c.nodeIds));
    return map;
  }, [clusters]);

  React.useEffect(() => {
    fitPendingRef.current = true;
    userMovedRef.current = false;
    engineStoppedRef.current = false;
    resumeGraph();
    const t = setTimeout(() => {
      if (fitPendingRef.current && !userMovedRef.current) {
        resumeGraph();
        fgRef.current?.zoomToFit(500, 64);
        captureBaseZoom();
      }
    }, 700);
    return () => clearTimeout(t);
  }, [data, captureBaseZoom, resumeGraph]);

  React.useEffect(() => {
    resumeGraph();
  }, [hoverId, selectedId, activeClusterId, resumeGraph]);

  const configureForces = React.useCallback(() => {
    const fg = fgRef.current;
    if (!fg) return;
    const charge = fg.d3Force("charge");
    charge?.strength?.(-140)?.distanceMax?.(520);
    const link = fg.d3Force("link");
    link
      ?.distance?.((l) => 24 + (1 - (l.weight ?? 0)) * 64)
      ?.strength?.((l) => 0.06 + (l.weight ?? 0) * 0.55);
  }, []);

  const assignRef = React.useCallback(
    (inst: GraphInstance | undefined) => {
      fgRef.current = inst;
      if (inst) configureForces();
    },
    [configureForces],
  );

  React.useImperativeHandle(
    forwardedRef,
    () => ({
      focusNode: (id: string) => {
        resumeGraph();
        userMovedRef.current = true;
        fitPendingRef.current = false;
        const fg = fgRef.current;
        const node = dataRef.current.nodes.find((n) => n.id === id);
        if (!fg || !node || node.x == null || node.y == null) return;
        fg.centerAt(node.x, node.y, 700);
        fg.zoom(Math.max(fg.zoom(), baseZoomRef.current * 2.6), 700);
      },
      focusCluster: (clusterId: number) => {
        resumeGraph();
        userMovedRef.current = true;
        fitPendingRef.current = false;
        fgRef.current?.zoomToFit(700, 96, (node) => node.community === clusterId);
      },
      fitAll: () => {
        resumeGraph();
        userMovedRef.current = true;
        fitPendingRef.current = false;
        fgRef.current?.zoomToFit(600, 64);
        captureBaseZoom();
      },
    }),
    [captureBaseZoom, resumeGraph],
  );

  const focusId = hoverId ?? selectedId;
  const highlightSet = React.useMemo<Set<string> | null>(() => {
    if (focusId) {
      const set = new Set<string>([focusId]);
      for (const n of adjacency.get(focusId) ?? []) set.add(n);
      return set;
    }
    if (activeClusterId != null) {
      return clusterMembers.get(activeClusterId) ?? new Set();
    }
    return null;
  }, [focusId, activeClusterId, adjacency, clusterMembers]);

  const isLinkHot = React.useCallback(
    (source: string, target: string): boolean => {
      if (focusId) return source === focusId || target === focusId;
      if (activeClusterId != null) {
        const members = clusterMembers.get(activeClusterId);
        return !!members && members.has(source) && members.has(target);
      }
      return false;
    },
    [focusId, activeClusterId, clusterMembers],
  );

  /**
   * Paint one node: a disc in its cluster color with a soft glow, a bright core
   * for a lit-from-within look, a ring when it is the focus, and its title once
   * semantic zoom crosses the node-label threshold. Nodes outside the highlight
   * set are drawn as small dim dots instead.
   */
  const paintNode = React.useCallback(
    (node: VizNode, ctx: CanvasRenderingContext2D, scale: number) => {
      if (node.x == null || node.y == null) return;
      const id = node.id;
      const r = nodeRadius(node.degree);
      const dimmed = highlightSet != null && !highlightSet.has(id);
      const isFocus = id === focusId;

      if (dimmed) {
        ctx.beginPath();
        ctx.arc(node.x, node.y, r * 0.78, 0, TAU);
        ctx.fillStyle = `rgba(${DIM_RGB}, 0.32)`;
        ctx.fill();
        return;
      }

      ctx.save();
      ctx.shadowColor = node.color;
      ctx.shadowBlur = r * (isFocus ? 4.2 : 2.6);
      ctx.beginPath();
      ctx.arc(node.x, node.y, r, 0, TAU);
      ctx.fillStyle = node.color;
      ctx.fill();
      ctx.shadowBlur = 0;

      ctx.globalAlpha = isFocus ? 0.9 : 0.5;
      ctx.beginPath();
      ctx.arc(node.x, node.y, r * 0.45, 0, TAU);
      ctx.fillStyle = "rgba(255, 255, 255, 0.85)";
      ctx.fill();
      ctx.globalAlpha = 1;

      if (isFocus) {
        ctx.beginPath();
        ctx.arc(node.x, node.y, r + 3 / scale, 0, TAU);
        ctx.lineWidth = 1.6 / scale;
        ctx.strokeStyle = "rgba(255, 255, 255, 0.9)";
        ctx.stroke();
      }
      ctx.restore();

      const labelAlpha = isFocus ? 1 : nodeLabelOpacity(relZoom(scale));
      if (labelAlpha > 0.05) {
        const fontSize = 12 / scale;
        ctx.font = `500 ${fontSize}px Inter, system-ui, sans-serif`;
        ctx.textAlign = "center";
        ctx.textBaseline = "top";
        const label = truncate(node.title, 30);
        const ly = node.y + r + 2 / scale;
        ctx.lineWidth = 3 / scale;
        ctx.strokeStyle = `rgba(4, 6, 12, ${0.7 * labelAlpha})`;
        ctx.strokeText(label, node.x, ly);
        ctx.fillStyle = `rgba(226, 232, 240, ${labelAlpha})`;
        ctx.fillText(label, node.x, ly);
      }
    },
    [highlightSet, focusId, relZoom],
  );

  const paintNodePointerArea = React.useCallback(
    (node: VizNode, color: string, ctx: CanvasRenderingContext2D) => {
      if (node.x == null || node.y == null) return;
      ctx.fillStyle = color;
      ctx.beginPath();
      ctx.arc(node.x, node.y, nodeRadius(node.degree) + 2, 0, TAU);
      ctx.fill();
    },
    [],
  );

  const linkColor = React.useCallback(
    (link: { source?: unknown; target?: unknown; weight?: number }): string => {
      const w = link.weight ?? 0;
      if (highlightSet == null) {
        return `rgba(${EDGE_RGB}, ${0.05 + w * 0.16})`;
      }
      const s = endId(link.source as never);
      const t = endId(link.target as never);
      if (isLinkHot(s, t)) {
        return `rgba(196, 214, 255, ${0.4 + w * 0.45})`;
      }
      return `rgba(${DIM_RGB}, 0.035)`;
    },
    [highlightSet, isLinkHot],
  );

  const linkWidth = React.useCallback(
    (link: { source?: unknown; target?: unknown; weight?: number }): number => {
      const base = 0.4 + (link.weight ?? 0) * 1.7;
      if (highlightSet == null) return base;
      const s = endId(link.source as never);
      const t = endId(link.target as never);
      return isLinkHot(s, t) ? base * 1.9 : base * 0.5;
    },
    [highlightSet, isLinkHot],
  );

  /** Big translucent topic labels at each community centroid, faded by zoom. */
  const paintClusterLabels = React.useCallback(
    (ctx: CanvasRenderingContext2D, scale: number) => {
      const alpha = clusterLabelOpacity(relZoom(scale));
      if (alpha < 0.03) return;
      const nodes = dataRef.current.nodes;
      const sums = new Map<number, { x: number; y: number; n: number }>();
      for (const n of nodes) {
        if (n.x == null || n.y == null) continue;
        const acc = sums.get(n.community) ?? { x: 0, y: 0, n: 0 };
        acc.x += n.x;
        acc.y += n.y;
        acc.n += 1;
        sums.set(n.community, acc);
      }
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      for (const cluster of clusters) {
        const acc = sums.get(cluster.id);
        if (!acc || acc.n === 0) continue;
        const cx = acc.x / acc.n;
        const cy = acc.y / acc.n;
        const screenPx = clamp(15 + cluster.count * 0.5, 15, 40);
        const fontSize = screenPx / scale;
        ctx.save();
        ctx.font = `700 ${fontSize}px Inter, system-ui, sans-serif`;
        ctx.shadowColor = rgba(cluster.color, 0.9 * alpha);
        ctx.shadowBlur = 18 / scale;
        ctx.fillStyle = rgba(cluster.color, 0.52 * alpha);
        ctx.fillText(cluster.label.toUpperCase(), cx, cy);
        ctx.restore();
      }
    },
    [clusters, relZoom],
  );

  const ready = width > 0 && height > 0;

  return (
    <div ref={sizeRef} className="absolute inset-0">
      {ready ? (
        <ForceGraphClient
          assignRef={assignRef}
          width={width}
          height={height}
          graphData={data}
          backgroundColor={CANVAS_BG}
          nodeRelSize={4}
          nodeVal={(n) => n.degree + 1}
          nodeColor={(n) => n.color}
          nodeLabel={() => ""}
          nodeCanvasObject={paintNode}
          nodePointerAreaPaint={paintNodePointerArea}
          linkColor={linkColor}
          linkWidth={linkWidth}
          onRenderFramePost={paintClusterLabels}
          cooldownTime={4000}
          warmupTicks={20}
          onNodeHover={(n) => onHoverNode(n ? n.id : null)}
          onNodeClick={(n) => onSelectNode(n as VizNode)}
          onNodeDrag={() => {
            userMovedRef.current = true;
            engineStoppedRef.current = false;
          }}
          onBackgroundClick={() => {
            userMovedRef.current = true;
            onBackgroundClick();
          }}
          onEngineStop={() => {
            if (fitPendingRef.current && !userMovedRef.current) {
              fgRef.current?.zoomToFit(500, 64);
              captureBaseZoom();
            }
            fitPendingRef.current = false;
            engineStoppedRef.current = true;
            scheduleIdlePause();
          }}
        />
      ) : null}
    </div>
  );
}

export const GraphCanvas = React.forwardRef(GraphCanvasInner);
GraphCanvas.displayName = "GraphCanvas";
