"use client";
import { useState, useRef, useEffect, ReactNode } from "react";
import { Info } from "lucide-react";
import { cn } from "@/lib/utils";

/**
 * Tooltip activado por hover en desktop y click/tap en mobile.
 * Útil para mostrar fórmulas, fuentes de datos y detalles que el usuario quiere ver bajo demanda.
 */
export function InfoTooltip({
  children,
  className,
  size = "sm",
  ariaLabel = "Más información",
}: {
  children: ReactNode;
  className?: string;
  size?: "xs" | "sm" | "md";
  ariaLabel?: string;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const onEsc = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onClick);
    document.addEventListener("keydown", onEsc);
    return () => {
      document.removeEventListener("mousedown", onClick);
      document.removeEventListener("keydown", onEsc);
    };
  }, [open]);

  const sizeMap = { xs: "h-3 w-3", sm: "h-3.5 w-3.5", md: "h-4 w-4" };

  return (
    <div ref={ref} className={cn("relative inline-flex", className)}>
      <button
        type="button"
        aria-label={ariaLabel}
        onClick={(e) => { e.stopPropagation(); setOpen((v) => !v); }}
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => setOpen(false)}
        className="rounded-full p-0.5 text-zinc-400 transition-colors hover:bg-zinc-100 hover:text-zinc-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 dark:hover:bg-zinc-800 dark:hover:text-zinc-300"
      >
        <Info className={sizeMap[size]} />
      </button>
      {open && (
        <div
          className="absolute right-0 top-full z-50 mt-2 w-72 rounded-lg border border-zinc-200 bg-white p-3 text-xs text-zinc-600 shadow-lg dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-400"
          role="tooltip"
          onClick={(e) => e.stopPropagation()}
        >
          {children}
        </div>
      )}
    </div>
  );
}
