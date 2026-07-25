"use client";

import * as React from "react";

import { Badge, type BadgeProps } from "@/components/ui/badge";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { cn } from "@/lib/utils";
import type { PermissionLevel } from "@/lib/types";

/**
 * Reusable permission-level primitives for the Access (permissions) page:
 * a labelled `<Select>` for choosing a grant level and a colored badge for
 * displaying one. The backend refuses a `none` grant (that means "revoke"),
 * so the select only offers the grantable levels unless `includeNone` is set.
 */

const GRANTABLE: PermissionLevel[] = ["viewer", "editor", "manager"];

export const PERMISSION_LABELS: Record<PermissionLevel, string> = {
  none: "No access",
  viewer: "Viewer",
  editor: "Editor",
  manager: "Manager",
};

const PERMISSION_HINTS: Record<PermissionLevel, string> = {
  none: "Cannot see the resource",
  viewer: "Read and search",
  editor: "Add and edit content",
  manager: "Full control, incl. sharing",
};

const PERMISSION_VARIANT: Record<PermissionLevel, BadgeProps["variant"]> = {
  none: "muted",
  viewer: "secondary",
  editor: "info",
  manager: "default",
};

interface PermissionSelectProps {
  value: PermissionLevel;
  onValueChange: (value: PermissionLevel) => void;
  /** Include the `none` option (e.g. to represent "no access" in a filter). */
  includeNone?: boolean;
  disabled?: boolean;
  id?: string;
  className?: string;
  /** Show the one-line description beneath each option (default true). */
  withHints?: boolean;
}

/** A `<Select>` bound to a {@link PermissionLevel}. */
export function PermissionSelect({
  value,
  onValueChange,
  includeNone = false,
  disabled = false,
  id,
  className,
  withHints = true,
}: PermissionSelectProps) {
  const options = includeNone
    ? (["none", ...GRANTABLE] as PermissionLevel[])
    : GRANTABLE;

  return (
    <Select
      value={value}
      onValueChange={(v) => onValueChange(v as PermissionLevel)}
      disabled={disabled}
    >
      <SelectTrigger id={id} className={cn("w-full", className)}>
        <SelectValue placeholder="Select a level" />
      </SelectTrigger>
      <SelectContent>
        {options.map((level) => (
          <SelectItem key={level} value={level}>
            <span className="flex flex-col">
              <span>{PERMISSION_LABELS[level]}</span>
              {withHints ? (
                <span className="text-xs text-muted-foreground">
                  {PERMISSION_HINTS[level]}
                </span>
              ) : null}
            </span>
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

/** Colored badge for a permission level. */
export function PermissionBadge({
  level,
  className,
}: {
  level: PermissionLevel;
  className?: string;
}) {
  return (
    <Badge
      variant={PERMISSION_VARIANT[level] ?? "muted"}
      className={cn(className)}
    >
      {PERMISSION_LABELS[level]}
    </Badge>
  );
}
