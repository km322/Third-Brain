import type { UsageKind } from "@/lib/types";

/**
 * Recharts-free palette and usage-kind helpers. Kept in its own module so pages
 * can label and color usage rows synchronously without pulling the (heavy)
 * recharts bundle into their first-load JS; the chart components that do need
 * recharts import from here too, keeping a single source of truth for colors.
 *
 * Categorical palette drawn from the `--chart-*` CSS variables. Assigned by a
 * stable entity ordering (never by rank) so a usage kind keeps the same color
 * across the bar chart, the cost donut and the breakdown table.
 */
export const CHART_PALETTE = [
  "hsl(var(--chart-1))",
  "hsl(var(--chart-2))",
  "hsl(var(--chart-3))",
  "hsl(var(--chart-4))",
  "hsl(var(--chart-5))",
  "hsl(var(--chart-6))",
] as const;

/** Canonical order of usage kinds - fixes each kind's categorical color slot. */
export const KIND_ORDER: UsageKind[] = [
  "completion",
  "embedding",
  "search",
  "ingest",
];

const KIND_LABELS: Record<UsageKind, string> = {
  completion: "Completions",
  embedding: "Embeddings",
  search: "Search",
  ingest: "Ingest",
};

/** Human-readable label for a usage kind. */
export function kindLabel(kind: UsageKind): string {
  return KIND_LABELS[kind] ?? kind;
}

/** Stable categorical color for a usage kind (matches the donut + table). */
export function kindColor(kind: UsageKind): string {
  const idx = KIND_ORDER.indexOf(kind);
  return CHART_PALETTE[(idx < 0 ? 0 : idx) % CHART_PALETTE.length];
}
