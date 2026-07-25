import { Building2, Globe, Lock, Users, type LucideIcon } from "lucide-react";

import { Badge, type BadgeProps } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import type { Visibility } from "@/lib/types";

const VISIBILITY_META: Record<
  Visibility,
  { label: string; variant: NonNullable<BadgeProps["variant"]>; icon: LucideIcon }
> = {
  private: { label: "Private", variant: "muted", icon: Lock },
  team: { label: "Team", variant: "info", icon: Users },
  org: { label: "Organization", variant: "secondary", icon: Building2 },
  public: { label: "Public", variant: "success", icon: Globe },
};

/** Small pill communicating who can see a collection. */
export function VisibilityBadge({
  visibility,
  className,
}: {
  visibility: Visibility;
  className?: string;
}) {
  const meta = VISIBILITY_META[visibility] ?? VISIBILITY_META.private;
  const Icon = meta.icon;
  return (
    <Badge variant={meta.variant} className={cn("gap-1", className)}>
      <Icon className="h-3 w-3" />
      {meta.label}
    </Badge>
  );
}
