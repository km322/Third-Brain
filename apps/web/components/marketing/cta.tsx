import Link from "next/link";

import { Button } from "@/components/ui/button";

/**
 * Closing call to action: pure typography on the same quiet surface the page
 * started on. One violet pill, one violet text link, nothing else.
 */
export function Cta() {
  return (
    <section className="py-32 lg:py-40">
      <div className="container">
        <div className="mx-auto max-w-2xl text-center">
          <h2 className="text-balance text-4xl font-semibold leading-[1.08] tracking-[-0.025em] sm:text-5xl lg:text-6xl">
            Give your company a brain that writes itself.
          </h2>
          <p className="mx-auto mt-6 max-w-xl text-pretty text-lg leading-relaxed text-muted-foreground sm:text-xl">
            Third Brain is opening access in waves. Join the waitlist to let your
            agents capture the work as they go - with permissions built in from
            the first query.
          </p>
          <div className="mt-10 flex flex-col items-center justify-center gap-6 sm:flex-row">
            <Button asChild size="lg" className="h-12 rounded-full px-7">
              <Link href="/#waitlist">Join the waitlist</Link>
            </Button>
            <Link
              href="/login"
              className="text-[15px] font-medium text-primary transition-opacity hover:opacity-80"
            >
              Sign in <span aria-hidden="true">›</span>
            </Link>
          </div>
        </div>
      </div>
    </section>
  );
}
