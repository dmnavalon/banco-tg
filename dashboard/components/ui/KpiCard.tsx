"use client";
import { Kpi } from "@/lib/types";
import { cn, formatCLP, formatMonths, formatNum, formatPct, deltaPct } from "@/lib/utils";
import { Card } from "./Card";
import { StatusBadge, ConfianzaBadge } from "./StatusBadge";
import { InfoTooltip } from "./InfoTooltip";
import { TrendingDown, TrendingUp, Minus, ChevronRight } from "lucide-react";

function formatValue(kpi: Kpi): string {
  if (kpi.valor === null) return "—";
  switch (kpi.formato) {
    case "CLP": return formatCLP(kpi.valor);
    case "PCT": return formatPct(kpi.valor);
    case "MESES": return formatMonths(kpi.valor);
    case "RATIO": return formatNum(kpi.valor, 2) + "×";
    case "NUM":
    default: return formatNum(kpi.valor);
  }
}

function Trend({ value, base }: { value: number; base: number | null | undefined }) {
  if (base === null || base === undefined) {
    return <span className="text-[11px] text-zinc-400">sin avg histórico</span>;
  }
  const d = deltaPct(value, base);
  if (d === null) return <span className="text-[11px] text-zinc-400">sin comparación</span>;
  const Icon = d > 0.02 ? TrendingUp : d < -0.02 ? TrendingDown : Minus;
  const color = d > 0.02 ? "text-emerald-600 dark:text-emerald-400" : d < -0.02 ? "text-rose-600 dark:text-rose-400" : "text-zinc-500";
  return (
    <span className={cn("inline-flex items-center gap-1 text-[11px]", color)}>
      <Icon className="h-3 w-3" />
      {(d * 100).toFixed(1)}%
    </span>
  );
}

export function KpiCard({
  kpi,
  size = "md",
  trendBase,
  onClick,
}: {
  kpi: Kpi;
  size?: "sm" | "md" | "lg";
  trendBase?: "avg6m" | "avg3m" | "mesAnterior";
  onClick?: () => void;
}) {
  const sizeMap = {
    sm: { value: "text-xl", title: "text-xs" },
    md: { value: "text-2xl", title: "text-xs" },
    lg: { value: "text-3xl", title: "text-sm" },
  };
  const baseField = trendBase ?? "avg6m";
  const baseValue = kpi.comparaciones?.[baseField];
  const tieneDetalle = (kpi.breakdownIdxs && kpi.breakdownIdxs.length > 0) || (kpi.pasosCalculo && kpi.pasosCalculo.length > 0);
  const clickable = !!onClick && tieneDetalle;

  return (
    <Card
      className={cn(
        "group relative flex h-full flex-col justify-between gap-3 transition-shadow",
        clickable && "cursor-pointer hover:shadow-md hover:ring-2 hover:ring-blue-500/20",
      )}
      padding="md"
    >
      {/* Click overlay para no chocar con el InfoTooltip */}
      {clickable && (
        <button
          type="button"
          onClick={onClick}
          aria-label={`Ver detalle de ${kpi.nombre}`}
          className="absolute inset-0 z-0 cursor-pointer rounded-xl focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
        />
      )}

      {/* Contenido (z-10 para quedar arriba del overlay) */}
      <div className="relative z-10 pointer-events-none flex items-start justify-between gap-2">
        <p className={cn("font-medium text-zinc-600 dark:text-zinc-400", sizeMap[size].title)}>
          {kpi.nombre}
        </p>
        <div className="pointer-events-auto flex items-center gap-1">
          {kpi.estado !== "Sin data" && <StatusBadge estado={kpi.estado} />}
          {(kpi.formula || kpi.pasosCalculo) && (
            <InfoTooltip size="sm" ariaLabel={`Cómo se calcula ${kpi.nombre}`}>
              <p className="font-semibold text-zinc-700 dark:text-zinc-300">{kpi.nombre}</p>
              {kpi.formula && <p className="mt-1 leading-relaxed">{kpi.formula}</p>}
              {kpi.fuenteDatos && (
                <p className="mt-2 text-[10px] uppercase tracking-wide text-zinc-400">Fuente</p>
              )}
              {kpi.fuenteDatos && <p className="text-[11px]">{kpi.fuenteDatos}</p>}
              {tieneDetalle && (
                <p className="mt-2 border-t border-zinc-100 pt-2 text-[10px] italic text-zinc-400 dark:border-zinc-800">
                  Hacé clic en la tarjeta para ver los movimientos que se sumaron.
                </p>
              )}
            </InfoTooltip>
          )}
        </div>
      </div>

      <div className="relative z-10 pointer-events-none">
        <p className={cn("font-bold tabular-nums text-zinc-900 dark:text-zinc-50", sizeMap[size].value)}>
          {formatValue(kpi)}
        </p>
        {kpi.comparaciones && kpi.valor !== null && (
          <div className="mt-1 flex items-center gap-2 text-[11px] text-zinc-500">
            <Trend value={kpi.valor} base={baseValue} />
            <span className="text-zinc-300">vs {baseField === "avg6m" ? "avg 6m" : baseField === "avg3m" ? "avg 3m" : "mes anterior"}</span>
          </div>
        )}
      </div>

      <div className="relative z-10 pointer-events-none flex items-center justify-between gap-2">
        <ConfianzaBadge confianza={kpi.confianza} />
        {clickable && (
          <span className="inline-flex items-center text-[11px] text-blue-600 opacity-0 transition-opacity group-hover:opacity-100 dark:text-blue-400">
            Ver detalle <ChevronRight className="h-3 w-3" />
          </span>
        )}
        {!clickable && kpi.recomendacion && (
          <p className="text-right text-[11px] italic text-zinc-500" title={kpi.recomendacion}>
            {kpi.recomendacion.length > 60 ? kpi.recomendacion.slice(0, 60) + "…" : kpi.recomendacion}
          </p>
        )}
      </div>
    </Card>
  );
}
