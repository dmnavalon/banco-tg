"use client";

import { HandCoins } from "lucide-react";

import { DashboardKPIs } from "@/lib/types";
import { formatCLP } from "@/lib/utils";
import { Card, CardHeader } from "../ui/Card";
import { SectionHeader } from "../ui/SectionHeader";
import { EmptyState } from "../ui/EmptyState";

/** Libro de rendiciones (Gastos por rendir): plata que pones por cuenta de un
 * tercero. NO entra a ingresos ni gastos — acá ves el saldo por entidad para
 * saber quién te debe y qué rendiciones siguen pendientes. */
export function RendicionesSection({ kpis }: { kpis: DashboardKPIs }) {
  const r = kpis.rendiciones;

  const abrirEntidad = (entidad: string) => {
    const sp = new URLSearchParams({
      tab: "todos",
      tipoMovimiento: "GastoPorRendir",
      subcategoria: entidad,
    });
    window.open(`/movimientos?${sp.toString()}`, "_blank");
  };

  if (!r.entidades.length) {
    return (
      <div className="space-y-6">
        <SectionHeader title="Rendiciones" question="¿Quién me debe? ¿Qué tengo por cobrar?" />
        <EmptyState
          icon={<HandCoins className="h-10 w-10" />}
          title="Sin rendiciones registradas"
          description="Marca un movimiento como 'Gastos por rendir' (con la persona/empresa como subcategoría) y aquí verás el saldo de cada uno."
        />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <SectionHeader
        title="Rendiciones (gastos por rendir)"
        question="Plata de terceros — no entra a ingresos ni gastos"
      />

      <Card padding="lg">
        <p className="text-xs uppercase tracking-wide text-zinc-500">Total por cobrar (te deben)</p>
        <p className="mt-2 text-4xl font-bold text-amber-600 dark:text-amber-400">{formatCLP(r.totalPendiente)}</p>
        <p className="mt-1 text-xs text-zinc-500">
          Gastado histórico {formatCLP(r.totalGastado)} · reembolsado {formatCLP(r.totalReembolsado)}
        </p>
      </Card>

      <Card padding="md">
        <CardHeader
          title="Saldo por entidad"
          subtitle="Saldo &gt; 0 = te deben · &lt; 0 = te adelantaron. Click para ver los movimientos."
        />
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="text-zinc-500">
              <tr>
                <th className="pb-2 pr-3">Entidad</th>
                <th className="pb-2 pr-3 text-right">Movs</th>
                <th className="pb-2 pr-3 text-right">Gastado</th>
                <th className="pb-2 pr-3 text-right">Reembolsado</th>
                <th className="pb-2 pr-3 text-right">Saldo</th>
                <th className="pb-2 pr-3">Último mov.</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-100 dark:divide-zinc-900">
              {r.entidades.map((e) => (
                <tr
                  key={e.entidad}
                  onClick={() => abrirEntidad(e.entidad)}
                  className="cursor-pointer text-zinc-700 hover:bg-zinc-50 dark:text-zinc-300 dark:hover:bg-zinc-900"
                  title="Ver movimientos de esta entidad"
                >
                  <td className="py-2 pr-3 font-medium">{e.entidad}</td>
                  <td className="py-2 pr-3 text-right text-zinc-500">{e.cantidad}</td>
                  <td className="py-2 pr-3 text-right font-mono">{formatCLP(e.gastado)}</td>
                  <td className="py-2 pr-3 text-right font-mono">{formatCLP(e.reembolsado)}</td>
                  <td className={
                    "py-2 pr-3 text-right font-mono font-semibold " +
                    (e.saldo > 0 ? "text-amber-600 dark:text-amber-400"
                      : e.saldo < 0 ? "text-blue-600 dark:text-blue-400"
                      : "text-zinc-400")
                  }>
                    {formatCLP(e.saldo)}
                  </td>
                  <td className="py-2 pr-3 text-zinc-500">{e.ultimaFecha || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
