"use client";
import { DashboardKPIs, Kpi } from "@/lib/types";
import { KpiCard } from "../ui/KpiCard";
import { Card, CardHeader } from "../ui/Card";
import { SectionHeader } from "../ui/SectionHeader";
import { StatusBadge } from "../ui/StatusBadge";
import { LineChart, Line, XAxis, YAxis, Tooltip, ResponsiveContainer, Legend, CartesianGrid } from "recharts";
import { formatCLP } from "@/lib/utils";

export function ResumenGeneralSection({ kpis, onKpiClick }: { kpis: DashboardKPIs; onKpiClick?: (kpi: Kpi) => void }) {
  const r = kpis.resumen;
  const data = kpis.evolucion.ingresos.map((i, idx) => ({
    mes: i.mes,
    Ingresos: i.valor,
    Gastos: kpis.evolucion.gastos[idx]?.valor ?? 0,
    "Flujo libre": kpis.evolucion.flujoLibre[idx]?.valor ?? 0,
  }));

  const handle = (kpi: Kpi) => () => onKpiClick?.(kpi);

  return (
    <div className="space-y-6">
      <SectionHeader
        title="Resumen financiero general"
        question="Cómo estoy financieramente este mes"
        right={<StatusBadge estado={r.estadoGeneral} />}
      />

      {/* KPIs grandes */}
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-4">
        <KpiCard kpi={r.ingresosNetos} size="lg" trendBase="avg6m" onClick={handle(r.ingresosNetos)} />
        <KpiCard kpi={r.gastosTotales} size="lg" trendBase="avg6m" onClick={handle(r.gastosTotales)} />
        <KpiCard kpi={r.flujoLibre} size="lg" trendBase="avg6m" onClick={handle(r.flujoLibre)} />
        <KpiCard kpi={r.tasaAhorro} size="lg" onClick={handle(r.tasaAhorro)} />
      </div>

      {/* KPIs de patrimonio y deuda */}
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-4">
        <KpiCard kpi={r.patrimonioNeto} size="md" onClick={handle(r.patrimonioNeto)} />
        <KpiCard kpi={r.variacionPatrimonio} size="md" onClick={handle(r.variacionPatrimonio)} />
        <KpiCard kpi={r.mesesFondoEmergencia} size="md" onClick={handle(r.mesesFondoEmergencia)} />
        <KpiCard kpi={r.endeudamientoMensual} size="md" onClick={handle(r.endeudamientoMensual)} />
      </div>

      {/* KPIs de gasto % */}
      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <KpiCard kpi={r.gastoEsencialPct} size="sm" onClick={handle(r.gastoEsencialPct)} />
        <KpiCard kpi={r.gastoDiscrecionalPct} size="sm" onClick={handle(r.gastoDiscrecionalPct)} />
        <KpiCard kpi={r.gastoFijoPct} size="sm" onClick={handle(r.gastoFijoPct)} />
        <KpiCard kpi={r.gastoVariablePct} size="sm" onClick={handle(r.gastoVariablePct)} />
      </div>

      {/* Gráfico de evolución */}
      <Card padding="md">
        <CardHeader
          title="Evolución mensual"
          subtitle="Ingresos · Gastos · Flujo libre"
          info={
            <>
              <p className="font-semibold text-zinc-700 dark:text-zinc-300">Cómo se calcula</p>
              <p className="mt-1 leading-relaxed">
                Cada punto agrupa los movimientos del mes correspondiente:
              </p>
              <ul className="mt-1.5 ml-4 list-disc space-y-0.5">
                <li><b>Ingresos</b>: Σ movs con tipoMovimiento=Ingreso (sin internos).</li>
                <li><b>Gastos</b>: Σ movs con tipoMovimiento=GastoReal.</li>
                <li><b>Flujo libre</b>: Ingresos − Gastos − Pagos de deuda.</li>
              </ul>
              <p className="mt-2 text-[10px] italic text-zinc-400">
                Movimientos con flag Excluido=TRUE no se cuentan.
              </p>
            </>
          }
        />
        {data.length > 0 ? (
          <ResponsiveContainer width="100%" height={280}>
            <LineChart data={data} margin={{ top: 10, right: 12, left: 12, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#e4e4e7" />
              <XAxis dataKey="mes" tick={{ fontSize: 11 }} />
              <YAxis tick={{ fontSize: 11 }} tickFormatter={(v) => formatCLP(v, true)} width={70} />
              <Tooltip formatter={(v) => formatCLP(typeof v === "number" ? v : 0)} />
              <Legend wrapperStyle={{ fontSize: 12 }} />
              <Line type="monotone" dataKey="Ingresos" stroke="#10b981" strokeWidth={2} dot={false} />
              <Line type="monotone" dataKey="Gastos" stroke="#ef4444" strokeWidth={2} dot={false} />
              <Line type="monotone" dataKey="Flujo libre" stroke="#3b82f6" strokeWidth={2} dot={false} />
            </LineChart>
          </ResponsiveContainer>
        ) : (
          <p className="py-8 text-center text-sm text-zinc-500">Sin meses con data suficiente para graficar.</p>
        )}
      </Card>
    </div>
  );
}
