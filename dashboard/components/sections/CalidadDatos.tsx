"use client";
import { DashboardKPIs } from "@/lib/types";
import { Card, CardHeader } from "../ui/Card";
import { SectionHeader } from "../ui/SectionHeader";
import { formatPct, formatCLP, cn } from "@/lib/utils";
import { CheckCircle2, AlertCircle, AlertTriangle } from "lucide-react";

export function CalidadDatosSection({ kpis }: { kpis: DashboardKPIs }) {
  const c = kpis.calidadDatos;
  const status = c.pctCompletos >= 0.95 ? "ok" : c.pctCompletos >= 0.8 ? "warn" : "bad";
  const Icon = status === "ok" ? CheckCircle2 : status === "warn" ? AlertCircle : AlertTriangle;
  const color = status === "ok" ? "text-emerald-600" : status === "warn" ? "text-amber-600" : "text-rose-600";

  return (
    <div className="space-y-6">
      <SectionHeader title="Calidad de datos" question="Puedo confiar en este dashboard" />

      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <Card padding="md">
          <p className="text-xs font-medium text-zinc-500">Total movimientos</p>
          <p className="mt-2 text-2xl font-bold tabular-nums">{c.totalMovimientos}</p>
        </Card>
        <Card padding="md">
          <p className="text-xs font-medium text-zinc-500">Clasificados</p>
          <p className="mt-2 text-2xl font-bold tabular-nums">{c.clasificados}</p>
        </Card>
        <Card padding="md">
          <p className="text-xs font-medium text-zinc-500">Sin categoría</p>
          <p className={cn("mt-2 text-2xl font-bold tabular-nums", c.sinCategoria > 0 ? "text-rose-600" : "text-zinc-900 dark:text-zinc-50")}>
            {c.sinCategoria}
          </p>
        </Card>
        <Card padding="md">
          <p className="text-xs font-medium text-zinc-500">% completos</p>
          <div className="mt-2 flex items-center gap-2">
            <Icon className={cn("h-5 w-5", color)} />
            <p className={cn("text-2xl font-bold tabular-nums", color)}>{formatPct(c.pctCompletos, 0)}</p>
          </div>
        </Card>
      </div>

      {c.issues.length === 0 ? (
        <Card padding="lg">
          <p className="py-6 text-center text-sm text-zinc-500">No se detectaron problemas en la data.</p>
        </Card>
      ) : (
        c.issues.map((issue, i) => (
          <Card key={i} padding="md">
            <CardHeader title={`${issue.tipo} (${issue.count})`} subtitle="Muestra hasta 50 movimientos" />
            <div className="overflow-hidden rounded-lg border border-zinc-200 dark:border-zinc-800">
              <table className="w-full text-sm">
                <thead className="bg-zinc-50 text-left text-xs font-medium text-zinc-500 dark:bg-zinc-900">
                  <tr>
                    <th className="px-3 py-2">Fecha</th>
                    <th className="px-3 py-2">Banco</th>
                    <th className="px-3 py-2">Descripción</th>
                    <th className="px-3 py-2">Categoría</th>
                    <th className="px-3 py-2 text-right">Monto</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-zinc-100 dark:divide-zinc-800">
                  {issue.movimientos.map((m, j) => (
                    <tr key={j} className="hover:bg-zinc-50 dark:hover:bg-zinc-900/50">
                      <td className="px-3 py-2 text-xs text-zinc-500">{m.fechaISO}</td>
                      <td className="px-3 py-2">{m.banco}</td>
                      <td className="px-3 py-2 max-w-md truncate" title={m.descripcion}>{m.descripcion}</td>
                      <td className="px-3 py-2 text-xs text-zinc-500">{m.categoria || "—"}</td>
                      <td className="px-3 py-2 text-right tabular-nums">{formatCLP(m.montoCLP)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        ))
      )}
    </div>
  );
}
