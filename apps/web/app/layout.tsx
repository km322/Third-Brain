import type { Metadata } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
import type { ReactNode } from "react";

import { Providers } from "./providers";
import "./globals.css";

const inter = Inter({ subsets: ["latin"], variable: "--font-sans", display: "swap" });
const mono = JetBrains_Mono({
  subsets: ["latin"],
  variable: "--font-mono",
  display: "swap",
});

export const metadata: Metadata = {
  title: {
    default: "Third Brain - The documentation writes itself",
    template: "%s · Third Brain",
  },
  description:
    "Connect Third Brain to Claude, Cursor and your agents - they capture decisions and answers into a governed brain your whole company can search. Permissions enforced in the query, every model supported. Free, open source and self-hosted.",
  // Absolute base for canonical/OpenGraph URLs. Self-hosted deployments set
  // NEXT_PUBLIC_SITE_URL to their own origin; the fallback keeps a local build valid.
  metadataBase: new URL(process.env.NEXT_PUBLIC_SITE_URL || "http://localhost:3000"),
  openGraph: {
    title: "Third Brain",
    description:
      "The documentation writes itself - a governed company brain your agents fill in as they work. Free, open source and self-hosted.",
    type: "website",
    siteName: "Third Brain",
  },
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body className={`${inter.variable} ${mono.variable} font-sans`}>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
