"use client";

import * as React from "react";
import { useRouter } from "next/navigation";

import { auth } from "@/lib/api";

// useLayoutEffect on the client (fires before the browser paints, so an
// already-authenticated visitor is redirected before the auth form is ever shown),
// falling back to useEffect during SSR where layout effects do not run.
const useIsomorphicLayoutEffect =
  typeof window !== "undefined" ? React.useLayoutEffect : React.useEffect;

/**
 * Redirect a visitor who already holds a session away from an auth page (login /
 * signup) before paint, so the form never flashes. `getTarget` is evaluated at
 * redirect time. Returns whether a redirect is under way so the caller can render
 * nothing.
 */
export function useRedirectIfAuthenticated(getTarget: () => string): boolean {
  const router = useRouter();
  const [redirecting, setRedirecting] = React.useState(false);
  const targetRef = React.useRef(getTarget);
  targetRef.current = getTarget;

  useIsomorphicLayoutEffect(() => {
    if (auth.isAuthenticated) {
      setRedirecting(true);
      router.replace(targetRef.current());
    }
  }, [router]);

  return redirecting;
}
