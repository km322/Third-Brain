"use client";

import * as React from "react";
import { usePathname } from "next/navigation";
import { AlertTriangle } from "lucide-react";

import { PageLoading } from "@/components/dashboard/loading";
import { Sidebar } from "@/components/dashboard/sidebar";
import { Topbar } from "@/components/dashboard/topbar";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet";
import { AuthProvider, useAuth } from "@/lib/auth-context";

/**
 * Shown when loading the identity failed with a non-401 error (network blip, API
 * restart, gateway timeout). Without this the shell would sit on a permanent skeleton,
 * since a non-401 error never triggers the `/login` redirect and nothing refetches.
 */
function SessionError({ onRetry }: { onRetry: () => void }) {
  return (
    <div className="flex min-h-[50vh] flex-col items-center justify-center gap-4 text-center">
      <AlertTriangle className="h-8 w-8 text-muted-foreground" />
      <div className="space-y-1">
        <p className="text-sm font-semibold text-foreground">
          Couldn&apos;t load your session
        </p>
        <p className="text-sm text-muted-foreground">
          Something went wrong reaching the server. Check your connection and try again.
        </p>
      </div>
      <Button onClick={onRetry} variant="outline">
        Retry
      </Button>
    </div>
  );
}

/**
 * Inner shell - assumes an {@link AuthProvider} above it. Renders the fixed
 * desktop sidebar, a mobile drawer (closed again on every route change), the
 * sticky topbar and the routed page.
 *
 * Protected pages are gated on a resolved identity so they never render with a
 * null user (avoids flashes while loading or redirecting). While the session
 * resolves it shows a skeleton; a non-401 load failure shows a retry instead of
 * a permanent skeleton, and a 401 is handled by the provider's redirect to
 * `/login`.
 */
function DashboardShell({ children }: { children: React.ReactNode }) {
  const { user, isError, refetch } = useAuth();
  const pathname = usePathname();
  const [mobileOpen, setMobileOpen] = React.useState(false);

  React.useEffect(() => {
    setMobileOpen(false);
  }, [pathname]);

  return (
    <div className="min-h-screen bg-background">
      <aside className="fixed inset-y-0 left-0 z-40 hidden w-64 border-r bg-card lg:block">
        <Sidebar />
      </aside>

      <Sheet open={mobileOpen} onOpenChange={setMobileOpen}>
        <SheetContent side="left" className="w-72 bg-card p-0">
          <SheetTitle className="sr-only">Navigation</SheetTitle>
          <Sidebar onNavigate={() => setMobileOpen(false)} />
        </SheetContent>
      </Sheet>

      <div className="flex min-h-screen flex-col lg:pl-64">
        <Topbar onMenuClick={() => setMobileOpen(true)} />
        <main className="mx-auto w-full max-w-7xl flex-1 px-4 py-6 sm:px-6 lg:px-8">
          {user ? (
            children
          ) : isError ? (
            <SessionError onRetry={refetch} />
          ) : (
            <PageLoading />
          )}
        </main>
      </div>
    </div>
  );
}

/**
 * Authenticated dashboard layout. Wraps every `/dashboard/*` route with the
 * {@link AuthProvider} (identity + active org + logout/switch) and the app
 * shell. Redirects to `/login` when there is no valid session.
 */
export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  return (
    <AuthProvider>
      <DashboardShell>{children}</DashboardShell>
    </AuthProvider>
  );
}
