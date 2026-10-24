"use client";

import * as React from "react";

function subscribe(): () => void {
  return () => {};
}

/**
 * Whether the component is rendering on the client after hydration. False during the
 * server render and the hydration pass (so their markup matches), true from then on and
 * immediately on client-side navigations. For UI that depends on browser-only state,
 * such as the theme next-themes resolves from localStorage.
 */
export function useMounted(): boolean {
  return React.useSyncExternalStore(
    subscribe,
    () => true,
    () => false,
  );
}
