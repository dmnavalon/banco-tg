"use client";
import { DashboardKPIs } from "@/lib/types";
import { Card, CardHeader } from "../ui/Card";
import { SectionHeader } from "../ui/SectionHeader";
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid, Cell, PieChart, Pie } from "recharts";
import { formatCLP, formatPct } from "@/lib/utils";
import { cn } from "@/lib/utils";

const COLORS = ["#3b82f6", "#10b981", "#f59e0b", "#ef4444", "#8b5cf6", "#ec4899", "#06b6d4", "#f97316", "#84cc16", "#6366f1"];

export function GastosSection({ kpis }: { kpis: DashboardKPIs }) {
  const g = kpis.gastos;
  const totalMes = g.porCategoria.reduce((s, c) => s + c.montoCLP, 0);

  const topCategorias = g.porCategoria.slice(0, 10);
  const topComercios = g.porComercio.slice(0, 10);

  // Pareto data (acumulado)
  const paretoData = g.porCategoria.map((c, i) => {
    const acumulado = g.porCategoria.slice(0, i + 1).reduce((s, x) => s + x.montoCLP, 0);
    return { categoria: c.categoria, valor: c.montoCLP, acumuladoPct: totalMes > 0 ? acumulado / totalMes : 0 };
  });

  return (
    <div className="space-y-6">
      <SectionHeader title="Gastos" question="En qué se va mi dinero y qué subió" description={`Mes ${kpis.mesActual} · Total ${formatCLP(totalMes)} en ${g.porCategoria.length} categorías`} />

      {totalMes === 0 ? (
        <Card padding="lg">
          <p className="py-6 text-center text-sm text-zinc-500">No hay gastos clasificados como GastoReal en este mes.</p>
        </Card>
      ) : (
        <>
          <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
            {/* Top categorías */}
            <Card padding="md" className="lg:col-span-2">
              <CardHeader
                title="Top categorías del mes"
                subtitle="vs promedio histórico 6 meses (cuando hay)"
                info={
                  <>
                    <p className="font-semibold text-zinc-700 dark:text-zinc-300">Cómo se calcula</p>
                    <p className="mt-1 leading-relaxed">
                      Σ del monto efectivo del mes (Cuota a pagar si está en cuotas,
                      monto total si no) de los movs con tipoMovimiento=GastoReal,
                      agrupados por Categoría. Excluye internos (transferencias, pago
                      de TC) y con Excluido=TRUE.
                    </p>
                    <p className="mt-2 text-[10px] uppercase tracking-wide text-zinc-400">Color de barra</p>
                    <p>Azul = Esencial en TaxonomíaExtendida. Naranja = Discrecional.</p>
                  </>
                }
              />
              <ResponsiveContainer width="100%" height={Math.max(280, topCategorias.length * 32)}>
                <BarChart data={topCategorias} layout="vertical" margin={{ top: 5, right: 24, left: 8, bottom: 5 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#e4e4e7" />
                  <XAxis type="number" tick={{ fontSize: 11 }} tickFormatter={(v) => formatCLP(v, true)} />
                  <YAxis type="category" dataKey="categoria" tick={{ fontSize: 11 }} width={140} />
                  <Tooltip formatter={(v) => formatCLP(typeof v === "number" ? v : 0)} />
                  <Bar dataKey="montoCLP" fill="#3b82f6" radius={[0, 4, 4, 0]}>
                    {topCategorias.map((c, i) => (
                      <Cell key={i} fill={c.esencial ? "#3b82f6" : "#f59e0b"} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
              <div className="mt-2 flex gap-4 text-xs text-zinc-500">
                <span className="inline-flex items-center gap-1.5"><span className="h-2 w-3 rounded-sm bg-blue-500"></span>Esencial</span>
                <span className="inline-flex items-center gap-1.5"><span className="h-2 w-3 rounded-sm bg-amber-500"></span>Discrecional</span>
              </div>
            </Card>

            {/* Donut esencial vs discrecional */}
            <Card padding="md">
              <CardHeader
                title="Esencial vs Discrecional"
                subtitle="Composición del gasto del mes"
                info={
                  <>
                    <p className="font-semibold text-zinc-700 dark:text-zinc-300">Cómo se determina</p>
                    <p className="mt-1 leading-relaxed">
                      Cada movimiento hereda el flag <b>Esencial</b> desde la pestaña
                      TaxonomíaExtendida vía VLOOKUP por categoría. Si no encuentra
                      la categoría en la taxonomía, queda como No Esencial (default
                      conservador).
                    </p>
                    <p className="mt-2 text-[10px] italic text-zinc-400">
                      Para mover una categoría entre Esencial/Discrecional, editá la
                      columna C de TaxonomíaExtendida y refrescá el dashboard.
                    </p>
                  </>
                }
              />
              {(() => {
                const esencial = g.porCategoria.filter((c) => c.esencial).reduce((s, c) => s + c.montoCLP, 0);
                const discrecional = totalMes - esencial;
                const data = [
                  { name: "Esencial", value: esencial },
                  { name: "Discrecional", value: discrecional },
                ];
                return (
                  <ResponsiveContainer width="100%" height={240}>
                    <PieChart>
                      <Pie data={data} dataKey="value" nameKey="name" cx="50%" cy="50%" outerRadius={80} innerRadius={50} paddingAngle={2}>
                        <Cell fill="#3b82f6" />
                        <Cell fill="#f59e0b" />
                      </Pie>
                      <Tooltip formatter={(v) => formatCLP(typeof v === "number" ? v : 0)} />
                    </PieChart>
                  </ResponsiveContainer>
                );
              })()}
              <div className="mt-2 space-y-1 text-xs">
                {(() => {
                  const esencial = g.porCategoria.filter((c) => c.esencial).reduce((s, c) => s + c.montoCLP, 0);
                  const discrecional = totalMes - esencial;
                  return (
                    <>
                      <div className="flex items-center justify-between">
                        <span className="inline-flex items-center gap-1.5"><span className="h-2 w-3 rounded-sm bg-blue-500"></span>Esencial</span>
                        <span className="tabular-nums text-zinc-700 dark:text-zinc-300">{formatCLP(esencial)} · {formatPct(esencial / totalMes)}</span>
                      </div>
                      <div className="flex items-center justify-between">
                        <span className="inline-flex items-center gap-1.5"><span className="h-2 w-3 rounded-sm bg-amber-500"></span>Discrecional</span>
                        <span className="tabular-nums text-zinc-700 dark:text-zinc-300">{formatCLP(discrecional)} · {formatPct(discrecional / totalMes)}</span>
                      </div>
                    </>
                  );
                })()}
              </div>
            </Card>
          </div>

          {/* Top comercios */}
          <Card padding="md">
            <CardHeader
              title="Top comercios"
              subtitle="Concentración por proveedor"
              info={
                <>
                  <p className="font-semibold text-zinc-700 dark:text-zinc-300">Cómo se calcula</p>
                  <p className="mt-1 leading-relaxed">
                    Σ movimientos del mes (GastoReal) agrupados por la columna
                    Descripción, ordenados por monto descendente. La columna "Tx" es el
                    conteo de movimientos en cada comercio.
                  </p>
                  <p className="mt-2 text-[10px] italic text-zinc-400">
                    Si dos compras al mismo comercio aparecen como rows distintas,
                    revisá si el agente normaliza la descripción.
                  </p>
                </>
              }
            />
            <div className="overflow-hidden rounded-lg border border-zinc-200 dark:border-zinc-800">
              <table className="w-full text-sm">
                <thead className="bg-zinc-50 text-left text-xs font-medium text-zinc-500 dark:bg-zinc-900">
                  <tr>
                    <th className="px-3 py-2">#</th>
                    <th className="px-3 py-2">Comercio</th>
                    <th className="px-3 py-2 text-right">Monto</th>
                    <th className="px-3 py-2 text-right">% del mes</th>
                    <th className="px-3 py-2 text-right">Tx</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-zinc-100 dark:divide-zinc-800">
                  {topComercios.map((c, i) => (
                    <tr key={i} className="hover:bg-zinc-50 dark:hover:bg-zinc-900/50">
                      <td className="px-3 py-2 text-zinc-400">{i + 1}</td>
                      <td className="px-3 py-2 font-medium">{c.categoria}</td>
                      <td className="px-3 py-2 text-right tabular-nums">{formatCLP(c.montoCLP)}</td>
                      <td className="px-3 py-2 text-right tabular-nums text-zinc-500">{formatPct(c.montoCLP / totalMes)}</td>
                      <td className="px-3 py-2 text-right tabular-nums text-zinc-500">{c.cantidad}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>

          {/* Desviaciones */}
          {g.desviaciones.some((d) => d.promedioHistorico > 0) && (
            <Card padding="md">
              <CardHeader
                title="Desviaciones vs promedio histórico"
                subtitle="Categorías que se movieron fuera de banda"
                info={
                  <>
                    <p className="font-semibold text-zinc-700 dark:text-zinc-300">Cómo se calcula</p>
                    <p className="mt-1 leading-relaxed">
                      Para cada categoría se calcula el avg de los últimos 6 meses
                      (excluyendo el mes actual y los meses sin gasto). Δ% =
                      (mes_actual − avg6m) / avg6m. Si avg=0 o no hay data se muestra "—".
                    </p>
                    <p className="mt-2 text-[10px] uppercase tracking-wide text-zinc-400">Umbrales</p>
                    <ul className="ml-4 list-disc">
                      <li>&gt;+25% → rojo (alerta de aumento)</li>
                      <li>&lt;−25% → verde (mejora)</li>
                      <li>otro → dentro de banda</li>
                    </ul>
                  </>
                }
              />
              <div className="overflow-hidden rounded-lg border border-zinc-200 dark:border-zinc-800">
                <table className="w-full text-sm">
                  <thead className="bg-zinc-50 text-left text-xs font-medium text-zinc-500 dark:bg-zinc-900">
                    <tr>
                      <th className="px-3 py-2">Categoría</th>
                      <th className="px-3 py-2 text-right">Mes</th>
                      <th className="px-3 py-2 text-right">Avg histórico</th>
                      <th className="px-3 py-2 text-right">Δ</th>
                      <th className="px-3 py-2 text-right">Δ %</th>
                      <th className="px-3 py-2">Estado</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-zinc-100 dark:divide-zinc-800">
                    {g.desviaciones.map((d, i) => (
                      <tr key={i} className="hover:bg-zinc-50 dark:hover:bg-zinc-900/50">
                        <td className="px-3 py-2 font-medium">{d.categoria}</td>
                        <td className="px-3 py-2 text-right tabular-nums">{formatCLP(d.actual)}</td>
                        <td className="px-3 py-2 text-right tabular-nums text-zinc-500">{d.promedioHistorico > 0 ? formatCLP(d.promedioHistorico) : "—"}</td>
                        <td className="px-3 py-2 text-right tabular-nums">{d.promedioHistorico > 0 ? formatCLP(d.diferenciaAbsoluta) : "—"}</td>
                        <td className={cn("px-3 py-2 text-right tabular-nums",
                          d.diferenciaPorcentual > 0.25 ? "text-rose-600" : d.diferenciaPorcentual < -0.25 ? "text-emerald-600" : "text-zinc-500"
                        )}>
                          {d.promedioHistorico > 0 ? formatPct(d.diferenciaPorcentual) : "—"}
                        </td>
                        <td className="px-3 py-2 text-xs text-zinc-500">{d.explicacion}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
          )}

          {/* Pareto */}
          {paretoData.length > 1 && (
            <Card padding="md">
              <CardHeader
                title="Pareto de gasto por categoría"
                subtitle="Cuántas categorías explican qué % del gasto"
                info={
                  <>
                    <p className="font-semibold text-zinc-700 dark:text-zinc-300">Cómo leer este gráfico</p>
                    <p className="mt-1 leading-relaxed">
                      Categorías ordenadas por monto descendente. Buscá dónde se llega
                      al 80% del gasto: típicamente 2-3 categorías concentran el grueso
                      (regla de Pareto). Esas son las que más mueven la aguja si las
                      reducís.
                    </p>
                  </>
                }
              />
              <ResponsiveContainer width="100%" height={240}>
                <BarChart data={paretoData}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#e4e4e7" />
                  <XAxis dataKey="categoria" tick={{ fontSize: 10 }} angle={-30} textAnchor="end" height={80} />
                  <YAxis yAxisId="left" tickFormatter={(v) => formatCLP(v, true)} tick={{ fontSize: 10 }} />
                  <YAxis yAxisId="right" orientation="right" tickFormatter={(v) => `${(v * 100).toFixed(0)}%`} tick={{ fontSize: 10 }} domain={[0, 1]} />
                  <Tooltip
                    formatter={(value, name) => {
                      const v = typeof value === "number" ? value : 0;
                      return name === "valor" ? formatCLP(v) : `${(v * 100).toFixed(1)}%`;
                    }}
                  />
                  <Bar yAxisId="left" dataKey="valor" fill="#3b82f6" />
                </BarChart>
              </ResponsiveContainer>
            </Card>
          )}
        </>
      )}
    </div>
  );
}
