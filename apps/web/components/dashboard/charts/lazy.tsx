"use client";

import dynamic from "next/dynamic";

import { Skeleton } from "@/components/ui/skeleton";

/**
 * Lazily-loaded chart entry points. The recharts bundle is heavy (~90 kB gzip)
 * and only ever renders on the Overview and Analytics pages, so it is kept out
 * of their first-load JS and fetched on demand once the chart mounts. Charts are
 * client-only (they measure the DOM), hence `ssr: false`; each skeleton is sized
 * to the mounted chart's card height (erring a few pixels tall for charts reused
 * at varying heights) so the layout does not jump while recharts loads.
 */

export const AreaUsageChart = dynamic(
  () => import("./area-usage").then((m) => m.AreaUsageChart),
  {
    ssr: false,
    loading: () => <Skeleton className="h-[392px] w-full rounded-xl" />,
  },
);

export const BarByKind = dynamic(() => import("./bar-by-kind").then((m) => m.BarByKind), {
  ssr: false,
  loading: () => <Skeleton className="h-[260px] w-full rounded-lg" />,
});

export const DonutChart = dynamic(() => import("./donut").then((m) => m.DonutChart), {
  ssr: false,
  loading: () => <Skeleton className="h-[220px] w-full rounded-lg" />,
});
