import type { ReactNode } from "react";

import { Footer } from "@/components/marketing/footer";
import { Navbar } from "@/components/marketing/navbar";

/**
 * Chrome for the public marketing surface (`/`).
 *
 * The root layout owns <html>/<body> and global providers; this layout only
 * wraps the marketing pages with the glassy sticky navbar and the footer so the
 * authenticated dashboard (which has its own shell) stays untouched.
 */
export default function MarketingLayout({ children }: { children: ReactNode }) {
  return (
    <div className="relative flex min-h-screen flex-col bg-background">
      <Navbar />
      <main className="flex-1">{children}</main>
      <Footer />
    </div>
  );
}
