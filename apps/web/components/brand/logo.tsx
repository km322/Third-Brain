import type { JSX } from "react";

import { cn } from "@/lib/utils";

/**
 * Canonical "Strata Spine" path on a 64x64 art grid: three horizontal strata
 * bound into a single vertical spine. Exported so consumers (the auth panel,
 * the Open Graph card) redraw the same geometry without duplicating the path
 * data. One deliberate exception: app/icon.svg (the static favicon) inlines
 * this same string for battery/runtime reasons - update it in lockstep when
 * this path changes.
 */
export const LOGO_PATH =
  "M13 10 H42 A10 10 0 0 1 52 20 V44 A10 10 0 0 1 42 54 H13 A5 5 0 0 1 13 44 H38 A3.5 3.5 0 0 0 38 37 H16 A5 5 0 0 1 16 27 H38 A3.5 3.5 0 0 0 38 20 H13 A5 5 0 0 1 13 10 Z";

/**
 * The Third Brain symbol mark. Decorative (aria-hidden), fills with
 * `currentColor` and is sized via className (e.g. `h-6 w-6`).
 */
export function LogoMark({ className }: { className?: string }): JSX.Element {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      viewBox="0 0 64 64"
      fill="currentColor"
      aria-hidden="true"
      className={className}
    >
      <path d={LOGO_PATH} />
    </svg>
  );
}

/**
 * Symbol mark plus the "Third Brain" wordmark. Renders in ink by default;
 * both pieces inherit color, so a single text-* class recolors the lockup.
 */
export function LogoLockup({
  className,
  markClassName,
  wordmarkClassName,
}: {
  className?: string;
  markClassName?: string;
  wordmarkClassName?: string;
}): JSX.Element {
  return (
    <span className={cn("flex items-center gap-2 text-foreground", className)}>
      <LogoMark className={cn("h-6 w-6", markClassName)} />
      <span className={cn("font-semibold tracking-tight", wordmarkClassName)}>
        Third Brain
      </span>
    </span>
  );
}
