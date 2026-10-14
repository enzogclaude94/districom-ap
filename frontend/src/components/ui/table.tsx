import type { HTMLAttributes, TdHTMLAttributes, ThHTMLAttributes } from "react";

import { cn } from "@/lib/cn";

/**
 * Below `md`, a `stacked` table shows each row as a card: its header is hidden and each cell shows its
 * `data-label` next to its value, so a phone never scrolls sideways. The DOM is the same table.
 */
const STACKED = cn(
  "max-md:block max-md:[&_thead]:hidden max-md:[&_tbody]:block",
  "max-md:[&_tr]:mb-2 max-md:[&_tr]:block max-md:[&_tr]:rounded-md max-md:[&_tr]:border! max-md:[&_tr]:px-3 max-md:[&_tr]:py-1",
  "max-md:[&_td]:flex max-md:[&_td]:items-start max-md:[&_td]:justify-between max-md:[&_td]:gap-4 max-md:[&_td]:px-0 max-md:[&_td]:py-1.5 max-md:[&_td]:text-right",
  "max-md:[&_td:empty]:hidden max-md:[&_td]:before:shrink-0 max-md:[&_td]:before:text-left max-md:[&_td]:before:text-muted-foreground max-md:[&_td]:before:content-[attr(data-label)]",
);

export function Table({
  className,
  stacked = false,
  ...props
}: HTMLAttributes<HTMLTableElement> & { stacked?: boolean }) {
  return (
    <div className={cn("w-full", stacked ? "md:overflow-x-auto" : "overflow-x-auto")}>
      <table className={cn("w-full caption-bottom text-sm", stacked && STACKED, className)} {...props} />
    </div>
  );
}

export function TableHeader(props: HTMLAttributes<HTMLTableSectionElement>) {
  return <thead className="sticky top-0 bg-surface [&_tr]:border-b" {...props} />;
}

export function TableBody(props: HTMLAttributes<HTMLTableSectionElement>) {
  return <tbody className="[&_tr:last-child]:border-0" {...props} />;
}

export function TableRow({ className, ...props }: HTMLAttributes<HTMLTableRowElement>) {
  return <tr className={cn("border-b border-border hover:bg-muted/50", className)} {...props} />;
}

export function TableHead({ className, ...props }: ThHTMLAttributes<HTMLTableCellElement>) {
  return (
    <th className={cn("h-10 px-3 text-left align-middle font-medium text-muted-foreground", className)} {...props} />
  );
}

export function TableCell({ className, ...props }: TdHTMLAttributes<HTMLTableCellElement>) {
  return <td className={cn("px-3 py-2.5 align-middle", className)} {...props} />;
}
