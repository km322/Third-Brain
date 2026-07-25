"use client";

import * as React from "react";
import { Check, Copy } from "lucide-react";
import { toast } from "sonner";

import { Button, type ButtonProps } from "@/components/ui/button";
import { cn } from "@/lib/utils";

interface CopyButtonProps extends Omit<ButtonProps, "onClick" | "value"> {
  /** Text to copy to the clipboard. */
  value: string;
  /** Optional toast message shown on success. Pass `null` to suppress. */
  toastMessage?: string | null;
  /** Optional label rendered next to the icon. */
  label?: string;
}

/**
 * Copies `value` to the clipboard and briefly swaps its icon to a checkmark.
 * Handy for API keys, IDs, snippets and cURL examples throughout the dashboard.
 */
export function CopyButton({
  value,
  toastMessage = "Copied to clipboard",
  label,
  className,
  variant = "ghost",
  size,
  ...props
}: CopyButtonProps) {
  const [copied, setCopied] = React.useState(false);

  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      if (toastMessage) toast.success(toastMessage);
      setTimeout(() => setCopied(false), 1800);
    } catch {
      toast.error("Couldn't copy to clipboard");
    }
  }

  return (
    <Button
      type="button"
      variant={variant}
      size={size ?? (label ? "sm" : "icon")}
      onClick={handleCopy}
      className={cn(className)}
      aria-label={label ? undefined : "Copy"}
      {...props}
    >
      {copied ? (
        <Check className="h-4 w-4 text-success" />
      ) : (
        <Copy className="h-4 w-4" />
      )}
      {label}
    </Button>
  );
}
