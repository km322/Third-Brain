import Link from "next/link";
import type { ReactNode } from "react";
import { ArrowLeft } from "lucide-react";

import { LOGO_PATH, LogoLockup, LogoMark } from "@/components/brand/logo";

/**
 * Split-screen shell for the unauthenticated flows (login, signup, password
 * reset). The left panel is a quiet, text-free brand surface: the Strata Spine
 * mark oversized as 1px line art - a construction drawing of the identity -
 * with the small solid mark top-left. The routed form lives in the right
 * column, which also carries the mobile lockup and a "back to home" link.
 */
export default function AuthLayout({ children }: { children: ReactNode }) {
  return (
    <div className="grid min-h-screen lg:grid-cols-2">
      <aside className="relative hidden border-r border-border/60 bg-muted/30 dark:bg-card lg:block">
        <div
          aria-hidden
          className="pointer-events-none absolute inset-0 flex items-center justify-center overflow-hidden"
        >
          <svg
            viewBox="0 0 64 64"
            fill="none"
            className="h-[540px] w-[540px] shrink-0 text-foreground/[0.09]"
          >
            <path
              d={LOGO_PATH}
              stroke="currentColor"
              strokeWidth={1}
              vectorEffect="non-scaling-stroke"
            />
          </svg>
        </div>

        <Link
          href="/"
          aria-label="Third Brain home"
          className="absolute left-12 top-12 text-foreground"
        >
          <LogoMark className="h-6 w-6" />
        </Link>
      </aside>

      <main className="flex flex-col px-6 py-8 sm:px-12">
        <div className="flex items-center justify-between">
          <Link
            href="/"
            className="inline-flex items-center gap-2 text-sm text-muted-foreground transition-colors hover:text-foreground"
          >
            <ArrowLeft className="h-4 w-4" />
            Back to home
          </Link>

          <LogoLockup
            className="lg:hidden"
            markClassName="h-6 w-6"
            wordmarkClassName="text-sm font-medium"
          />
        </div>

        <div className="flex flex-1 items-center justify-center py-10">
          <div className="w-full max-w-sm">{children}</div>
        </div>
      </main>
    </div>
  );
}
