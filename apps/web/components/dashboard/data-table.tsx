"use client";

import * as React from "react";

import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

export interface Column<T> {
  /** Stable id for the column (used as React key). */
  id: string;
  header: React.ReactNode;
  /** Renders the cell for a given row. */
  cell: (row: T, index: number) => React.ReactNode;
  align?: "left" | "center" | "right";
  /** Applied to both the header and body cells for this column. */
  className?: string;
  headClassName?: string;
  /** Hide below the `md` breakpoint to keep tables readable on mobile. */
  hideOnMobile?: boolean;
}

interface DataTableProps<T> {
  columns: Column<T>[];
  data: T[];
  /** Stable key per row. */
  rowKey: (row: T, index: number) => string;
  onRowClick?: (row: T) => void;
  isLoading?: boolean;
  /** Skeleton rows to show while loading. */
  loadingRows?: number;
  /** Rendered (spanning all columns) when `data` is empty and not loading. */
  empty?: React.ReactNode;
  className?: string;
  /** Wrap the table in a bordered card (default true). */
  bordered?: boolean;
}

const alignClass = {
  left: "text-left",
  center: "text-center",
  right: "text-right",
} as const;

/**
 * A small, fully-typed table built on the UI `Table` primitives. Handles
 * loading skeletons, an empty slot and optional row clicks - everything the
 * dashboard list pages need without pulling in a data-grid dependency.
 *
 * ```tsx
 * <DataTable
 *   data={members}
 *   rowKey={(m) => m.id}
 *   columns={[
 *     { id: "name", header: "Name", cell: (m) => m.user?.full_name },
 *     { id: "role", header: "Role", cell: (m) => <Badge>{m.role}</Badge> },
 *   ]}
 * />
 * ```
 */
export function DataTable<T>({
  columns,
  data,
  rowKey,
  onRowClick,
  isLoading = false,
  loadingRows = 6,
  empty,
  className,
  bordered = true,
}: DataTableProps<T>) {
  const showEmpty = !isLoading && data.length === 0;

  return (
    <div
      className={cn(
        bordered && "overflow-hidden rounded-lg border bg-card",
        className,
      )}
    >
      <Table>
        <TableHeader>
          <TableRow className="hover:bg-transparent">
            {columns.map((col) => (
              <TableHead
                key={col.id}
                className={cn(
                  col.align && alignClass[col.align],
                  col.hideOnMobile && "hidden md:table-cell",
                  col.headClassName,
                )}
              >
                {col.header}
              </TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {isLoading
            ? Array.from({ length: loadingRows }).map((_, r) => (
                <TableRow key={`skeleton-${r}`} className="hover:bg-transparent">
                  {columns.map((col) => (
                    <TableCell
                      key={col.id}
                      className={cn(
                        col.hideOnMobile && "hidden md:table-cell",
                      )}
                    >
                      <Skeleton className="h-4 w-full max-w-[160px]" />
                    </TableCell>
                  ))}
                </TableRow>
              ))
            : data.map((row, index) => (
                <TableRow
                  key={rowKey(row, index)}
                  onClick={onRowClick ? () => onRowClick(row) : undefined}
                  className={cn(onRowClick && "cursor-pointer")}
                >
                  {columns.map((col) => (
                    <TableCell
                      key={col.id}
                      className={cn(
                        col.align && alignClass[col.align],
                        col.hideOnMobile && "hidden md:table-cell",
                        col.className,
                      )}
                    >
                      {col.cell(row, index)}
                    </TableCell>
                  ))}
                </TableRow>
              ))}

          {showEmpty && (
            <TableRow className="hover:bg-transparent">
              <TableCell
                colSpan={columns.length}
                className="h-40 p-0 text-center"
              >
                {empty ?? (
                  <span className="text-sm text-muted-foreground">
                    No records found.
                  </span>
                )}
              </TableCell>
            </TableRow>
          )}
        </TableBody>
      </Table>
    </div>
  );
}
