import Link from "next/link";
import type { ReactNode } from "react";
import { ArrowLeft } from "lucide-react";

import { LOGO_PATH, LogoLockup } from "@/components/brand/logo";

/**
 * Chrome for the public product demo (`/demo/*`). A quiet, centered surface with the same
 * hairline brand watermark as the auth screens, so the login-gated demo reads as part of
 * the product rather than a detour. The authenticated dashboard has its own shell and is
 * unaffected - this layout only wraps the demo sign-in gateway.
 */
export default function DemoLayout({ children }: { children: ReactNode }) {
  return (
    <div className="relative flex min-h-screen flex-col bg-background">
      {/* Watermark: the mark's geometry drawn as a hairline, centered behind the content. */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 flex items-center justify-center overflow-hidden"
      >
        <svg
          viewBox="0 0 64 64"
          fill="none"
          className="h-[560px] w-[560px] shrink-0 text-foreground/[0.04]"
        >
          <path
            d={LOGO_PATH}
            stroke="currentColor"
            strokeWidth={1}
            vectorEffect="non-scaling-stroke"
          />
        </svg>
      </div>

      <header className="relative z-10 border-b border-border/60">
        <div className="container flex h-16 items-center justify-between">
          <Link href="/" aria-label="Third Brain home" className="flex items-center">
            <LogoLockup
              markClassName="h-6 w-6"
              wordmarkClassName="text-sm font-medium tracking-tight"
            />
          </Link>
          <Link
            href="/"
            className="inline-flex items-center gap-2 text-sm text-muted-foreground transition-colors hover:text-foreground"
          >
            <ArrowLeft className="h-4 w-4" />
            Back to home
          </Link>
        </div>
      </header>

      <main className="relative z-10 flex flex-1 items-center justify-center px-4 py-12 sm:px-6">
        {children}
      </main>
    </div>
  );
}
