"use client";

import * as React from "react";
import ForceGraph2D, { type ForceGraphProps } from "react-force-graph-2d";

import type { GraphInstance, VizNodeData, VizLinkData } from "./types";

export type ForceGraphClientProps = ForceGraphProps<VizNodeData, VizLinkData> & {
  /** Receives the imperative graph instance once (and `undefined` on unmount). */
  assignRef?: (instance: GraphInstance | undefined) => void;
};

/**
 * react-force-graph's default export is a deeply-generic function component. We
 * render it through a permissive cast so this wrapper stays the single place
 * that touches those generics; callers get the strongly-typed props above.
 */
const ForceGraph = ForceGraph2D as unknown as React.ForwardRefExoticComponent<
  Record<string, unknown> & React.RefAttributes<unknown>
>;

/**
 * Thin client-only bridge to `react-force-graph-2d`. It is loaded via
 * `next/dynamic` with `ssr: false` (the library needs the DOM/canvas), and hands
 * the imperative instance back through `assignRef` - `next/dynamic` cannot
 * forward a React `ref`, so we bridge it with a plain callback prop instead.
 */
export default function ForceGraphClient({ assignRef, ...props }: ForceGraphClientProps) {
  const ref = React.useRef<GraphInstance | undefined>(undefined);

  React.useEffect(() => {
    assignRef?.(ref.current);
    return () => assignRef?.(undefined);
  }, [assignRef]);

  return <ForceGraph ref={ref} {...(props as Record<string, unknown>)} />;
}
