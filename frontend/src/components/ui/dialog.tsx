"use client";

import { X } from "lucide-react";
import { useEffect, useId, useRef, type ReactNode } from "react";

import { cn } from "@/lib/cn";

interface DialogProps {
  title: string;
  description?: ReactNode;
  onClose: () => void;
  children: ReactNode;
  className?: string;
}

const FOCUSABLE = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled]):not([type='hidden'])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  "[tabindex]:not([tabindex='-1'])",
].join(",");

function focusables(container: HTMLElement) {
  return Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
    (element) => !element.closest("fieldset[disabled]") && element.getAttribute("aria-hidden") !== "true",
  );
}

/**
 * Modal panel. While open, focus stays inside it: Tab and Shift+Tab cycle through its controls and focus
 * that escapes (a click on the backdrop, a programmatic focus) is brought back. Escape or the close button
 * dismisses it, and focus returns to the control that opened it.
 */
export function Dialog({ title, description, onClose, children, className }: DialogProps) {
  const titleId = useId();
  const panel = useRef<HTMLDivElement>(null);
  const close = useRef(onClose);
  useEffect(() => {
    close.current = onClose;
  }, [onClose]);

  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    const container = panel.current;
    if (!container) return;
    const first = focusables(container).find((element) => !element.hasAttribute("data-close"));
    (first ?? container).focus();

    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.stopPropagation();
        close.current();
        return;
      }
      if (event.key !== "Tab") return;
      const items = focusables(container);
      if (items.length === 0) {
        event.preventDefault();
        container.focus();
        return;
      }
      const firstItem = items[0];
      const lastItem = items[items.length - 1];
      const active = document.activeElement;
      if (event.shiftKey && (active === firstItem || !container.contains(active))) {
        event.preventDefault();
        lastItem.focus();
      } else if (!event.shiftKey && (active === lastItem || !container.contains(active))) {
        event.preventDefault();
        firstItem.focus();
      }
    };
    const onFocusIn = (event: FocusEvent) => {
      if (event.target instanceof Node && !container.contains(event.target)) {
        (focusables(container)[0] ?? container).focus();
      }
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("focusin", onFocusIn);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("focusin", onFocusIn);
      if (opener?.isConnected) opener.focus();
    };
  }, []);

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-foreground/30 p-0 sm:items-center sm:p-4">
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        className={cn(
          "max-h-[100dvh] w-full overflow-y-auto rounded-t-lg border border-border bg-surface shadow-card focus:outline-none sm:max-w-lg sm:rounded-lg",
          className,
        )}
      >
        <div className="flex items-start justify-between gap-4 p-5 pb-3">
          <div className="space-y-1">
            <h2 id={titleId} className="font-display text-lg font-semibold tracking-tight">
              {title}
            </h2>
            {description && <div className="text-sm text-muted-foreground">{description}</div>}
          </div>
          <button
            type="button"
            data-close
            aria-label="Cerrar"
            className="rounded-md p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
            onClick={onClose}
          >
            <X className="size-4" aria-hidden />
          </button>
        </div>
        <div className="p-5 pt-0">{children}</div>
      </div>
    </div>
  );
}
