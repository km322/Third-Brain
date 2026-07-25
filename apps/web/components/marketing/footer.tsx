import Link from "next/link";

import { LogoMark } from "@/components/brand/logo";

const REPO_URL = "https://github.com/km322/Third-Brain";
const CONTACT_EMAIL = "admin@third-brain.ai";

interface FooterLink {
  label: string;
  href: string;
  external?: boolean;
}

interface FooterColumn {
  title: string;
  links: FooterLink[];
}

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
      { label: "Documentation", href: "/docs" },
      { label: "OpenAI-compatible API", href: "/#features" },
      { label: "MCP server", href: "/#integrations" },
      { label: "Docs on GitHub", href: `${REPO_URL}/tree/main/docs`, external: true },
      { label: "Source on GitHub", href: REPO_URL, external: true },
    ],
  },
  {
    title: "Company",
    links: [
      { label: "Security", href: "/#security" },
      { label: "Sign in", href: "/login" },
      { label: "Get started", href: "/signup" },
      { label: "Contact", href: `mailto:${CONTACT_EMAIL}`, external: true },
    ],
  },
];

export function Footer() {
  return (
    <footer className="border-t border-border/60 bg-background">
      <div className="container py-16">
        <div className="grid gap-10 lg:grid-cols-5">
          {/* Brand */}
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

          {/* Link columns */}
          {COLUMNS.map((col) => (
            <div key={col.title}>
              <h3 className="text-xs font-semibold text-foreground">
                {col.title}
              </h3>
              <ul className="mt-4 space-y-2.5">
                {col.links.map((link) => (
                  <li key={link.label}>
                    {link.external ? (
                      <a
                        href={link.href}
                        target={link.href.startsWith("mailto:") ? undefined : "_blank"}
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
          <p>© {new Date().getFullYear()} Third Brain. All rights reserved.</p>
          <div className="flex items-center gap-5">
            <Link href="/#security" className="transition-colors hover:text-foreground">
              Security
            </Link>
            <a
              href={`mailto:${CONTACT_EMAIL}`}
              className="transition-colors hover:text-foreground"
            >
              Contact
            </a>
          </div>
        </div>
      </div>
    </footer>
  );
}
