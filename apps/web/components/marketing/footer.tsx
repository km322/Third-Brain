import Link from "next/link";

import { LogoMark } from "@/components/brand/logo";
import {
  GITHUB_LICENSE_URL,
  GITHUB_URL,
  IS_STATIC_SITE,
} from "@/components/marketing/links";

interface FooterLink {
  label: string;
  href: string;
  external?: boolean;
}

interface FooterColumn {
  title: string;
  links: FooterLink[];
}

/**
 * The footer's link columns. The static project site publishes no app, so it
 * carries no sign-in link.
 */
const COLUMNS: FooterColumn[] = [
  {
    title: "Product",
    links: [
      { label: "Features", href: "/#features" },
      { label: "How it works", href: "/#how-it-works" },
      { label: "Integrations", href: "/#integrations" },
    ],
  },
  {
    title: "Developers",
    links: [
      { label: "Docs", href: "/docs" },
      { label: "OpenAI-compatible API", href: "/#features" },
      { label: "MCP server", href: "/#integrations" },
    ],
  },
  {
    title: "Project",
    links: [
      { label: "Security", href: "/#security" },
      { label: "GitHub", href: GITHUB_URL, external: true },
      { label: "License", href: GITHUB_LICENSE_URL, external: true },
      ...(IS_STATIC_SITE ? [] : [{ label: "Sign in", href: "/login" }]),
      { label: "Get started", href: "/docs#quick-start" },
    ],
  },
];

/**
 * Site footer: the brand lockup beside the link columns, over a rule carrying the
 * copyright line and the two links worth repeating.
 */
export function Footer() {
  return (
    <footer className="border-t border-border/60 bg-background">
      <div className="container py-16">
        <div className="grid gap-10 lg:grid-cols-5">
          <div className="lg:col-span-2">
            <Link
              href="/"
              className="inline-flex text-foreground"
              aria-label="Third Brain home"
            >
              <LogoMark className="h-6 w-6" />
            </Link>
            <p className="mt-4 text-sm text-muted-foreground">
              The documentation writes itself.
            </p>
          </div>

          {COLUMNS.map((col) => (
            <div key={col.title}>
              <h3 className="text-xs font-semibold text-foreground">{col.title}</h3>
              <ul className="mt-4 space-y-2.5">
                {col.links.map((link) => (
                  <li key={link.label}>
                    {link.external ? (
                      <a
                        href={link.href}
                        target="_blank"
                        rel="noreferrer"
                        className="text-[13px] text-muted-foreground transition-colors hover:text-foreground"
                      >
                        {link.label}
                      </a>
                    ) : (
                      <Link
                        href={link.href}
                        className="text-[13px] text-muted-foreground transition-colors hover:text-foreground"
                      >
                        {link.label}
                      </Link>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>

        <div className="mt-12 flex flex-col items-center justify-between gap-4 border-t border-border/60 pt-6 text-xs text-muted-foreground sm:flex-row">
          <p>
            © {new Date().getFullYear()} Third Brain. Free and open source under
            Apache-2.0.
          </p>
          <div className="flex items-center gap-5">
            <Link href="/#security" className="transition-colors hover:text-foreground">
              Security
            </Link>
            <a
              href={GITHUB_URL}
              target="_blank"
              rel="noreferrer"
              className="transition-colors hover:text-foreground"
            >
              GitHub
            </a>
          </div>
        </div>
      </div>
    </footer>
  );
}
