/**
 * Visual constants for the knowledge graph canvas. The graph is intentionally
 * rendered on a fixed near-black field (Obsidian-style) regardless of the app
 * theme, since a glowing web reads best on a dark ground.
 */

/** Deep indigo-black the canvas paints itself with. */
export const CANVAS_BG = "#070912";

/**
 * Categorical cluster palette. Tailwind-400 hues: distinct, saturated and high
 * contrast on the near-black canvas. Cycled by cluster index, in order: violet,
 * sky, emerald, amber, rose, cyan, indigo, pink, lime, orange, teal, fuchsia,
 * yellow, blue.
 */
export const CLUSTER_PALETTE = [
  "#a78bfa",
  "#38bdf8",
  "#34d399",
  "#fbbf24",
  "#fb7185",
  "#22d3ee",
  "#818cf8",
  "#f472b6",
  "#a3e635",
  "#fb923c",
  "#2dd4bf",
  "#e879f9",
  "#facc15",
  "#60a5fa",
];

/** Muted slate used for dimmed (out-of-focus) nodes and links. */
export const DIM_RGB = "148, 163, 184";

/** Base tint for edges when nothing is focused. */
export const EDGE_RGB = "148, 163, 184";

export const clusterColor = (index: number): string =>
  CLUSTER_PALETTE[index % CLUSTER_PALETTE.length];

/** Parse a `#rrggbb` string into an `r, g, b` component string for rgba(). */
export function hexToRgb(hex: string): string {
  const h = hex.replace("#", "");
  const r = parseInt(h.slice(0, 2), 16);
  const g = parseInt(h.slice(2, 4), 16);
  const b = parseInt(h.slice(4, 6), 16);
  return `${r}, ${g}, ${b}`;
}

export function rgba(hex: string, alpha: number): string {
  return `rgba(${hexToRgb(hex)}, ${alpha})`;
}

const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));

/**
 * Zoom thresholds that drive the semantic zoom. The input is the zoom RELATIVE to
 * the fit-to-view baseline (1.0 = the default "see all documents" framing), so the
 * behavior is independent of graph size. At the default framing topics dominate;
 * zooming in fades the big cluster labels out and fades per-node titles in.
 */
const NODE_LABEL_START = 1.35;
const NODE_LABEL_FULL = 2.4;
const CLUSTER_LABEL_FULL = 1.0;
const CLUSTER_LABEL_END = 1.75;

/** Opacity for per-node title labels: 0 when zoomed out, 1 when zoomed in. */
export function nodeLabelOpacity(scale: number): number {
  return clamp((scale - NODE_LABEL_START) / (NODE_LABEL_FULL - NODE_LABEL_START), 0, 1);
}

/** Opacity for big cluster (topic) labels: 1 when zoomed out, 0 when zoomed in. */
export function clusterLabelOpacity(scale: number): number {
  return clamp(
    (CLUSTER_LABEL_END - scale) / (CLUSTER_LABEL_END - CLUSTER_LABEL_FULL),
    0,
    1,
  );
}

/** Node radius in graph units, scaled by degree so hubs read larger. */
export function nodeRadius(degree: number): number {
  return clamp(2.4 + Math.sqrt(Math.max(degree, 0)) * 1.5, 2.4, 13);
}
