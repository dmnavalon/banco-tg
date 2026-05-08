import { Estado, Confianza } from "@/lib/types";
import { cn } from "@/lib/utils";

const STATE_STYLES: Record<Estado, string> = {
  Sano: "bg-emerald-50 text-emerald-700 ring-emerald-600/20 dark:bg-emerald-950/40 dark:text-emerald-300 dark:ring-emerald-500/30",
  Atención: "bg-amber-50 text-amber-700 ring-amber-600/20 dark:bg-amber-950/40 dark:text-amber-300 dark:ring-amber-500/30",
  Crítico: "bg-rose-50 text-rose-700 ring-rose-600/20 dark:bg-rose-950/40 dark:text-rose-300 dark:ring-rose-500/30",
  "Sin data": "bg-zinc-50 text-zinc-600 ring-zinc-600/20 dark:bg-zinc-900 dark:text-zinc-400 dark:ring-zinc-700",
};

const CONFIANZA_STYLES: Record<Confianza, string> = {
  Real: "bg-zinc-100 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300",
  Estimado: "bg-blue-50 text-blue-700 dark:bg-blue-950/40 dark:text-blue-300",
  Incompleto: "bg-amber-50 text-amber-700 dark:bg-amber-950/40 dark:text-amber-300",
  NoHayData: "bg-zinc-50 text-zinc-500 dark:bg-zinc-900 dark:text-zinc-500",
};

export function StatusBadge({ estado }: { estado: Estado }) {
  return (
    <span className={cn("inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset", STATE_STYLES[estado])}>
      {estado}
    </span>
  );
}

export function ConfianzaBadge({ confianza }: { confianza: Confianza }) {
  const labels: Record<Confianza, string> = {
    Real: "Datos reales",
    Estimado: "Estimado",
    Incompleto: "Incompleto",
    NoHayData: "No hay data",
  };
  return (
    <span className={cn("inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide", CONFIANZA_STYLES[confianza])}>
      {labels[confianza]}
    </span>
  );
}
