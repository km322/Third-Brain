"use client";

import { useQuery } from "@tanstack/react-query";
import {
  Fingerprint,
  Lock,
  ShieldAlert,
  ShieldCheck,
  type LucideIcon,
} from "lucide-react";

import { EmptyState } from "@/components/dashboard/empty-state";
import { StatCardsSkeleton, TableSkeleton } from "@/components/dashboard/loading";
import { PageHeader } from "@/components/dashboard/page-header";
import { isOrgAdmin } from "@/components/governance/role-badge";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { api, ApiError } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import type { OversharingReport, SensitivityLevel } from "@/lib/types";
import { cn } from "@/lib/utils";

const errMsg = (e: unknown, f = "Something went wrong") =>
  e instanceof ApiError ? e.message : f;

const SENSITIVITY_VARIANT: Record<
  SensitivityLevel,
  "info" | "destructive" | "muted"
> = {
  none: "muted",
  pii: "info",
  confidential: "destructive",
};

const SENSITIVITY_LABEL: Record<SensitivityLevel, string> = {
  none: "None",
  pii: "PII",
  confidential: "Confidential",
};

export default function OversharingPage() {
  const { role } = useAuth();
  const admin = isOrgAdmin(role);

  const reportQuery = useQuery<OversharingReport>({
    queryKey: ["oversharing"],
    queryFn: () => api.get<OversharingReport>("/governance/oversharing"),
    enabled: admin,
  });

  const report = reportQuery.data;
  const items = report?.items ?? [];

  return (
    <div className="space-y-6">
      <PageHeader
        title="Oversharing"
        description="Sensitive documents that are visible more broadly than they should be. Review them and tighten access."
      />

      {!admin ? (
        <EmptyState
          icon={Lock}
          title="Admin access required"
          description="Only organization admins and owners can view the oversharing report."
        />
      ) : reportQuery.isLoading ? (
        <>
          <StatCardsSkeleton count={3} />
          <TableSkeleton columns={4} />
        </>
      ) : reportQuery.isError ? (
        <EmptyState
          icon={ShieldAlert}
          title="Couldn't load the oversharing report"
          description={errMsg(
            reportQuery.error,
            "Something went wrong fetching the report. Try again in a moment.",
          )}
        />
      ) : (
        <>
          <div className="grid gap-4 sm:grid-cols-3">
            <StatTile
              label="Total oversharing"
              value={report?.total_oversharing ?? 0}
              icon={ShieldAlert}
            />
            <StatTile
              label="PII documents"
              value={report?.summary.pii ?? 0}
              icon={Fingerprint}
              tone="info"
            />
            <StatTile
              label="Confidential documents"
              value={report?.summary.confidential ?? 0}
              icon={Lock}
              tone="destructive"
            />
          </div>

          {items.length === 0 ? (
            <EmptyState
              icon={ShieldCheck}
              title="No oversharing detected"
              description="Every sensitive document is scoped appropriately. New risks will show up here as your knowledge base grows."
            />
          ) : (
            <Card className="overflow-hidden">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Document</TableHead>
                    <TableHead>Sensitivity</TableHead>
                    <TableHead>Effective visibility</TableHead>
                    <TableHead>Collection</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {items.map((item) => (
                    <TableRow key={item.document_id}>
                      <TableCell className="font-medium text-foreground">
                        {item.title}
                      </TableCell>
                      <TableCell>
                        <Badge variant={SENSITIVITY_VARIANT[item.sensitivity]}>
                          {SENSITIVITY_LABEL[item.sensitivity]}
                        </Badge>
                      </TableCell>
                      <TableCell>
                        <Badge variant="muted" className="capitalize">
                          {item.effective_visibility}
                        </Badge>
                      </TableCell>
                      <TableCell className="text-muted-foreground">
                        {item.collection_name}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </Card>
          )}
        </>
      )}
    </div>
  );
}

function StatTile({
  label,
  value,
  icon: Icon,
  tone = "default",
}: {
  label: string;
  value: number;
  icon: LucideIcon;
  tone?: "default" | "info" | "destructive";
}) {
  const toneClass = {
    default: "bg-primary/10 text-primary",
    info: "bg-info/15 text-info",
    destructive: "bg-destructive/15 text-destructive",
  }[tone];

  return (
    <Card className="p-5">
      <div className="flex items-start justify-between gap-3">
        <p className="text-sm font-medium text-muted-foreground">{label}</p>
        <span
          className={cn(
            "flex h-8 w-8 items-center justify-center rounded-md",
            toneClass,
          )}
        >
          <Icon className="h-4 w-4" />
        </span>
      </div>
      <p className="mt-3 text-2xl font-semibold tracking-tight tabular-nums">
        {value}
      </p>
    </Card>
  );
}
