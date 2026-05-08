import { ReactNode } from "react";
import { cn } from "@/lib/utils";
import { InfoTooltip } from "./InfoTooltip";

export function Card({
  children,
  className,
  padding = "md",
}: {
  children: ReactNode;
  className?: string;
  padding?: "none" | "sm" | "md" | "lg";
}) {
  const padMap = { none: "", sm: "p-3", md: "p-5", lg: "p-7" };
  return (
    <div className={cn("rounded-xl border border-zinc-200 bg-white shadow-sm dark:border-zinc-800 dark:bg-zinc-950", padMap[padding], className)}>
      {children}
    </div>
  );
}

export function CardHeader({
  title,
  subtitle,
  right,
  info,
}: {
  title: string;
  subtitle?: string;
  right?: ReactNode;
  info?: ReactNode;
}) {
  return (
    <div className="mb-4 flex items-start justify-between gap-3">
      <div className="flex-1">
        <div className="flex items-center gap-1.5">
          <h3 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">{title}</h3>
          {info && (
            <InfoTooltip size="sm" ariaLabel={`Más información sobre ${title}`}>
              {info}
            </InfoTooltip>
          )}
        </div>
        {subtitle && <p className="mt-0.5 text-xs text-zinc-500 dark:text-zinc-400">{subtitle}</p>}
      </div>
      {right && <div className="shrink-0">{right}</div>}
    </div>
  );
}
