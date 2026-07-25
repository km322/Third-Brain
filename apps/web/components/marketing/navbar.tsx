"use client";

import * as React from "react";
import Link from "next/link";
import { Menu, X } from "lucide-react";

import { LogoLockup } from "@/components/brand/logo";
import { ThemeToggle } from "@/components/theme-toggle";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/** Primary marketing nav links. Anchors resolve to sections on the landing page. */
const NAV_LINKS = [
  { label: "Product", href: "/#features" },
  { label: "How it works", href: "/#how-it-works" },
  { label: "Docs", href: "/docs" },
];

/** Brand lockup that returns to the marketing home. */
function Brand({ onClick }: { onClick?: () => void }) {
  return (
    <Link
      href="/"
      onClick={onClick}
      className="flex items-center"
      aria-label="Third Brain home"
    >
      <LogoLockup
        markClassName="h-6 w-6"
        wordmarkClassName="text-sm font-medium tracking-tight"
      />
    </Link>
  );
}

/**
 * Sticky top navigation. Collapses to a slide-down panel on mobile and grows a
 * hairline border, blur and whisper shadow once the page is scrolled.
 */
export function Navbar() {
  const [open, setOpen] = React.useState(false);
  const [scrolled, setScrolled] = React.useState(false);

  React.useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 8);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  // Lock body scroll while the mobile panel is open.
  React.useEffect(() => {
    document.body.style.overflow = open ? "hidden" : "";
    return () => {
      document.body.style.overflow = "";
    };
  }, [open]);

  return (
    <header
      className={cn(
        "sticky top-0 z-50 w-full transition-colors duration-200",
        scrolled || open
          ? "border-b border-border/60 bg-background/80 shadow-[0_1px_2px_rgb(0_0_0/0.03)] backdrop-blur-xl supports-[backdrop-filter]:bg-background/60"
          : "border-b border-transparent bg-background/0",
      )}
    >
      <div className="container relative flex h-16 items-center justify-between gap-6">
        <Brand onClick={() => setOpen(false)} />

        {/* Absolutely positioned so the links center on the bar itself, not the
            leftover space between the (narrower) brand and (wider) actions. */}
        <nav className="absolute left-1/2 top-1/2 hidden -translate-x-1/2 -translate-y-1/2 items-center gap-7 md:flex">
          {NAV_LINKS.map((link) => (
            <Link
              key={link.href}
              href={link.href}
              className="text-sm text-muted-foreground transition-colors hover:text-foreground"
            >
              {link.label}
            </Link>
          ))}
        </nav>

        <div className="hidden items-center gap-2 md:flex">
          <ThemeToggle />
          <Button
            asChild
            variant="ghost"
            size="sm"
            className="text-muted-foreground hover:bg-transparent hover:text-foreground"
          >
            <Link href="/login">Sign in</Link>
          </Button>
          <Button asChild size="sm" className="rounded-full px-4">
            <Link href="/#waitlist">Join waitlist</Link>
          </Button>
        </div>

        <div className="flex items-center gap-1 md:hidden">
          <ThemeToggle />
          <Button
            variant="ghost"
            size="icon"
            aria-label={open ? "Close menu" : "Open menu"}
            aria-expanded={open}
            onClick={() => setOpen((v) => !v)}
          >
            {open ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}
          </Button>
        </div>
      </div>

      {/* Mobile panel */}
      {open && (
        <div className="border-t border-border/60 bg-background/95 backdrop-blur-xl md:hidden">
          <div className="container flex flex-col py-4">
            {NAV_LINKS.map((link) => (
              <Link
                key={link.href}
                href={link.href}
                onClick={() => setOpen(false)}
                className="py-2.5 text-sm font-medium text-muted-foreground transition-colors hover:text-foreground"
              >
                {link.label}
              </Link>
            ))}
            <div className="mt-3 flex flex-col gap-2">
              <Button
                asChild
                className="rounded-full"
                onClick={() => setOpen(false)}
              >
                <Link href="/#waitlist">Join waitlist</Link>
              </Button>
              <Button
                asChild
                variant="outline"
                className="rounded-full"
                onClick={() => setOpen(false)}
              >
                <Link href="/login">Sign in</Link>
              </Button>
            </div>
          </div>
        </div>
      )}
    </header>
  );
}
