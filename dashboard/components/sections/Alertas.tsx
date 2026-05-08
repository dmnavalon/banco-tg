"use client";
import { DashboardKPIs } from "@/lib/types";
import { Card } from "../ui/Card";
import { SectionHeader } from "../ui/SectionHeader";
import { cn } from "@/lib/utils";
import { AlertTriangle, AlertCircle, Info, Bell, ArrowRight } from "lucide-react";

const SEV_STYLES = {
  alta: { bg: "bg-rose-50 dark:bg-rose-950/30", text: "text-rose-700 dark:text-rose-300", border: "border-rose-200 dark:border-rose-900", hover: "hover:bg-rose-100 dark:hover:bg-rose-950/50", Icon: AlertTriangle },
  media: { bg: "bg-amber-50 dark:bg-amber-950/30", text: "text-amber-700 dark:text-amber-300", border: "border-amber-200 dark:border-amber-900", hover: "hover:bg-amber-100 dark:hover:bg-amber-950/50", Icon: AlertCircle },
  baja: { bg: "bg-blue-50 dark:bg-blue-950/30", text: "text-blue-700 dark:text-blue-300", border: "border-blue-200 dark:border-blue-900", hover: "hover:bg-blue-100 dark:hover:bg-blue-950/50", Icon: Info },
};

const SECCION_LABEL: Record<string, string> = {
  resumen: "Resumen",
  flujo: "Flujo de caja",
  gastos: "Gastos",
  presupuesto: "Presupuesto",
  deuda: "Deuda",
  fondo: "Fondo emergencia",
  inversiones: "Inversiones",
  patrimonio: "Patrimonio",
  calidad: "Calidad de datos",
  insights: "Insights",
};

export function AlertasSection({
  kpis,
  onNavigate,
}: {
  kpis: DashboardKPIs;
  onNavigate?: (seccion: string) => void;
}) {
  return (
    <div className="space-y-6">
      <SectionHeader title="Alertas automáticas" question="Qué requiere mi atención ahora" description={`${kpis.alertas.length} alerta${kpis.alertas.length === 1 ? "" : "s"} activa${kpis.alertas.length === 1 ? "" : "s"}`} />

      {kpis.alertas.length === 0 ? (
        <Card padding="lg">
          <div className="flex flex-col items-center py-8 text-center">
            <Bell className="mb-3 h-10 w-10 text-zinc-300" />
            <p className="text-sm text-zinc-500">Sin alertas activas en este momento.</p>
          </div>
        </Card>
      ) : (
        <div className="space-y-3">
          {kpis.alertas.map((a) => {
            const s = SEV_STYLES[a.severidad];
            const destinoLabel = a.seccionDestino ? SECCION_LABEL[a.seccionDestino] : null;
            const clickable = !!a.seccionDestino && !!onNavigate;
            return (
              <button
                key={a.id}
                type="button"
                onClick={clickable ? () => onNavigate?.(a.seccionDestino!) : undefined}
                disabled={!clickable}
                className={cn(
                  "group block w-full rounded-xl border p-4 text-left transition-colors",
                  s.bg, s.border,
                  clickable && s.hover,
                  clickable && "cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500",
                  !clickable && "cursor-default",
                )}
              >
                <div className="flex items-start gap-3">
                  <s.Icon className={cn("mt-0.5 h-5 w-5 shrink-0", s.text)} />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center justify-between gap-2">
                      <h4 className={cn("text-sm font-semibold", s.text)}>{a.titulo}</h4>
                      <span className="shrink-0 text-[10px] uppercase tracking-wide text-zinc-500">{a.id} · {a.severidad}</span>
                    </div>
                    <p className="mt-1 text-xs text-zinc-600 dark:text-zinc-400">{a.evidencia}</p>
                    <p className="mt-2 text-sm text-zinc-700 dark:text-zinc-300">→ {a.accion}</p>
                    {destinoLabel && (
                      <p className={cn("mt-3 inline-flex items-center gap-1 text-xs font-medium", s.text)}>
                        <span className="opacity-70">Ir a</span> {destinoLabel}
                        <ArrowRight className="h-3 w-3 transition-transform group-hover:translate-x-0.5" />
                      </p>
                    )}
                  </div>
                </div>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
