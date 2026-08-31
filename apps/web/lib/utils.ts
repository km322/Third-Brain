import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function formatDate(value: string | Date, opts?: Intl.DateTimeFormatOptions) {
  const d = typeof value === "string" ? new Date(value) : value;
  return d.toLocaleDateString(
    undefined,
    opts ?? { year: "numeric", month: "short", day: "numeric" },
  );
}

const NUMBER_FORMAT = new Intl.NumberFormat();
const CURRENCY_FORMAT = new Intl.NumberFormat(undefined, {
  style: "currency",
  currency: "USD",
});

export function formatNumber(n: number) {
  return NUMBER_FORMAT.format(n);
}

export function formatCurrency(n: number) {
  return CURRENCY_FORMAT.format(n);
}

export function formatBytes(bytes: number) {
  if (bytes === 0) return "0 B";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(1))} ${sizes[i]}`;
}

export function initials(name?: string | null) {
  if (!name) return "?";
  return name
    .split(" ")
    .map((p) => p[0])
    .slice(0, 2)
    .join("")
    .toUpperCase();
}
