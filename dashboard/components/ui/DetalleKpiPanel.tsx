"use client";
import { useEffect, useMemo, useState } from "react";
import { Kpi, Movimiento, PasoCalculo } from "@/lib/types";
import { cn, formatCLP, formatMonths, formatNum, formatPct } from "@/lib/utils";
import { X, Calendar, Filter, Download, ExternalLink } from "lucide-react";
import { ConfianzaBadge, StatusBadge } from "./StatusBadge";

function gsheetUrl(spreadsheetId: string): string {
  if (!spreadsheetId) return "#";
  return `https://docs.google.com/spreadsheets/d/${spreadsheetId}/edit`;
}

function formatPaso(p: PasoCalculo): string {
  if (p.valor === null) return "—";
  switch (p.formato) {
    case "PCT": return formatPct(p.valor);
    case "MESES": return formatMonths(p.valor);
    case "RATIO": return formatNum(p.valor, 2) + "×";
    case "NUM": return formatNum(p.valor);
    case "CLP":
    default: return formatCLP(p.valor);
  }
}

export function DetalleKpiPanel({
  kpi,
  movimientos,
  onClose,
  contextoTitulo,
  spreadsheetId,
}: {
  kpi: Kpi | null;
  movimientos: Movimiento[];
  onClose: () => void;
  contextoTitulo?: string;
  spreadsheetId: string;
}) {
  const [pasoActivo, setPasoActivo] = useState<number | null>(null);

  useEffect(() => {
    setPasoActivo(null);
    if (!kpi) return;
    const onEsc = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onEsc);
    return () => document.removeEventListener("keydown", onEsc);
  }, [kpi, onClose]);

  const idxsActivos = useMemo(() => {
    if (!kpi) return [] as number[];
    if (pasoActivo !== null && kpi.pasosCalculo?.[pasoActivo]?.breakdownIdxs) {
      return kpi.pasosCalculo[pasoActivo].breakdownIdxs!;
    }
    return kpi.breakdownIdxs ?? [];
  }, [kpi, pasoActivo]);

  const movs = useMemo(() => {
    if (idxsActivos.length === 0) return [];
    const set = new Set(idxsActivos);
    return movimientos
      .filter((m) => set.has(m.idx))
      .sort((a, b) => Math.abs(b.montoCLP) - Math.abs(a.montoCLP));
  }, [idxsActivos, movimientos]);

  const totalMovs = useMemo(() => movs.reduce((s, m) => s + Math.abs(m.montoCLP), 0), [movs]);

  if (!kpi) return null;

  const csvHref = (() => {
    if (movs.length === 0) return null;
    const header = "Fecha,Banco,Persona,Descripción,Monto,Moneda,MontoCLP,MontoMesCLP,CuotaActual,CuotasTotal,CuotaAPagar,Categoría,Subcategoría,Esencial,Fijo,Recurrente,TipoMovimiento\n";
    const rows = movs.map((m) =>
      [m.fechaISO, m.banco, m.persona, `"${m.descripcion.replace(/"/g, "''")}"`, m.monto, m.moneda, m.montoCLP, m.montoMesCLP, m.cuotaActual ?? "", m.cuotasTotal ?? "", m.cuotaAPagar ?? "", m.categoria, m.subcategoria, m.esencial, m.fijo, m.recurrente, m.tipoMovimiento].join(","),
    );
    return "data:text/csv;charset=utf-8," + encodeURIComponent(header + rows.join("\n"));
  })();

  return (
    <>
      {/* Overlay */}
      <div
        className="fixed inset-0 z-40 bg-zinc-950/40 backdrop-blur-sm"
        onClick={onClose}
        aria-hidden="true"
      />
      {/* Panel */}
      <div className="fixed inset-y-0 right-0 z-50 flex w-full max-w-2xl flex-col border-l border-zinc-200 bg-white shadow-2xl dark:border-zinc-800 dark:bg-zinc-950" role="dialog" aria-modal="true">
        {/* Header */}
        <div className="flex items-start justify-between gap-4 border-b border-zinc-200 p-5 dark:border-zinc-800">
          <div className="min-w-0 flex-1">
            {contextoTitulo && <p className="text-[11px] font-medium uppercase tracking-wide text-zinc-400">{contextoTitulo}</p>}
            <h2 className="mt-1 truncate text-lg font-bold text-zinc-900 dark:text-zinc-50">{kpi.nombre}</h2>
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <ConfianzaBadge confianza={kpi.confianza} />
              {kpi.estado !== "Sin data" && <StatusBadge estado={kpi.estado} />}
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-1">
            {spreadsheetId && (
              <a
                href={gsheetUrl(spreadsheetId)}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1.5 rounded-md border border-zinc-200 bg-white px-2.5 py-1.5 text-xs font-medium text-zinc-700 transition-colors hover:bg-zinc-50 hover:text-zinc-900 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-300 dark:hover:bg-zinc-800 dark:hover:text-zinc-100"
                title="Abrir el Google Sheet en una pestaña nueva"
              >
                <ExternalLink className="h-3.5 w-3.5" />
                <span className="hidden sm:inline">Abrir GSheet</span>
              </a>
            )}
            <button
              type="button"
              onClick={onClose}
              className="rounded-full p-1.5 text-zinc-500 hover:bg-zinc-100 dark:hover:bg-zinc-800"
              aria-label="Cerrar panel"
            >
              <X className="h-5 w-5" />
            </button>
          </div>
        </div>

        {/* Cuerpo scrollable */}
        <div className="flex-1 overflow-y-auto">
          <div className="space-y-5 p-5">
            {/* Fórmula */}
            {kpi.formula && (
              <section>
                <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-zinc-500">Cómo se calcula</h3>
                <p className="text-sm text-zinc-700 dark:text-zinc-300">{kpi.formula}</p>
                {kpi.fuenteDatos && (
                  <p className="mt-1 text-xs text-zinc-500">Fuente: {kpi.fuenteDatos}</p>
                )}
              </section>
            )}

            {/* Pasos del cálculo */}
            {kpi.pasosCalculo && kpi.pasosCalculo.length > 0 && (
              <section>
                <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-zinc-500">Pasos del cálculo</h3>
                <div className="overflow-hidden rounded-lg border border-zinc-200 dark:border-zinc-800">
                  {kpi.pasosCalculo.map((p, i) => {
                    const clickable = !!p.breakdownIdxs && p.breakdownIdxs.length > 0;
                    const active = pasoActivo === i;
                    return (
                      <button
                        key={i}
                        type="button"
                        onClick={() => clickable && setPasoActivo(active ? null : i)}
                        disabled={!clickable}
                        className={cn(
                          "flex w-full items-center justify-between gap-3 border-b border-zinc-100 px-3 py-2.5 text-left text-sm last:border-b-0 dark:border-zinc-800",
                          clickable ? "hover:bg-zinc-50 dark:hover:bg-zinc-900" : "",
                          active ? "bg-blue-50 dark:bg-blue-950/30" : "",
                          i === kpi.pasosCalculo!.length - 1 ? "font-semibold" : "",
                        )}
                      >
                        <div className="min-w-0 flex-1">
                          <span className="text-zinc-700 dark:text-zinc-300">{p.etiqueta}</span>
                          {clickable && (
                            <span className="ml-2 text-[10px] text-zinc-400">
                              ({p.breakdownIdxs!.length} mov)
                            </span>
                          )}
                        </div>
                        <span className="tabular-nums text-zinc-900 dark:text-zinc-50">{formatPaso(p)}</span>
                      </button>
                    );
                  })}
                </div>
                {kpi.pasosCalculo.some((p) => p.breakdownIdxs && p.breakdownIdxs.length > 0) && (
                  <p className="mt-2 text-[11px] text-zinc-400">
                    <Filter className="mr-1 inline h-3 w-3" />
                    Hacé clic en una fila con (N mov) para filtrar la tabla de abajo a esos movimientos.
                  </p>
                )}
              </section>
            )}

            {/* Comparaciones */}
            {kpi.comparaciones && (
              <section>
                <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-zinc-500">Comparaciones</h3>
                <div className="grid grid-cols-2 gap-2 text-sm sm:grid-cols-3">
                  {([
                    ["Mes anterior", kpi.comparaciones.mesAnterior],
                    ["Avg 3m", kpi.comparaciones.avg3m],
                    ["Avg 6m", kpi.comparaciones.avg6m],
                    ["Avg 12m", kpi.comparaciones.avg12m],
                    ["Mismo mes año-1", kpi.comparaciones.mismoMesAnioAnterior],
                  ] as const).map(([label, v]) => (
                    <div key={label} className="rounded-md border border-zinc-200 px-2.5 py-1.5 dark:border-zinc-800">
                      <p className="text-[10px] uppercase tracking-wide text-zinc-400">{label}</p>
                      <p className="tabular-nums text-zinc-700 dark:text-zinc-300">
                        {v === null || v === undefined ? "sin data" : formatCLP(v)}
                      </p>
                    </div>
                  ))}
                </div>
              </section>
            )}

            {/* Lista de movimientos */}
            {idxsActivos.length > 0 && (
              <section>
                <div className="mb-2 flex items-center justify-between">
                  <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-500">
                    Movimientos que componen el cálculo
                    {pasoActivo !== null && kpi.pasosCalculo?.[pasoActivo] && (
                      <span className="ml-2 normal-case text-zinc-400">
                        · filtrado a "{kpi.pasosCalculo[pasoActivo].etiqueta}"
                      </span>
                    )}
                  </h3>
                  <div className="flex items-center gap-3">
                    {pasoActivo !== null && (
                      <button
                        type="button"
                        onClick={() => setPasoActivo(null)}
                        className="text-xs text-blue-600 hover:underline dark:text-blue-400"
                      >
                        Quitar filtro
                      </button>
                    )}
                    {csvHref && (
                      <a
                        href={csvHref}
                        download={`${kpi.nombre.replace(/[^a-z0-9]+/gi, "_").toLowerCase()}.csv`}
                        className="inline-flex items-center gap-1 text-xs text-zinc-600 hover:text-zinc-900 dark:text-zinc-400 dark:hover:text-zinc-100"
                      >
                        <Download className="h-3 w-3" />
                        CSV
                      </a>
                    )}
                  </div>
                </div>
                <div className="rounded-lg border border-zinc-200 bg-zinc-50 p-2 text-xs dark:border-zinc-800 dark:bg-zinc-900">
                  <div className="flex items-center justify-between text-zinc-500">
                    <span>{movs.length} movimiento{movs.length === 1 ? "" : "s"}</span>
                    <span className="tabular-nums font-medium text-zinc-700 dark:text-zinc-300">Σ {formatCLP(totalMovs)}</span>
                  </div>
                </div>
                <div className="mt-2 overflow-hidden rounded-lg border border-zinc-200 dark:border-zinc-800">
                  <table className="w-full text-xs">
                    <thead className="bg-zinc-50 text-left font-medium text-zinc-500 dark:bg-zinc-900">
                      <tr>
                        <th className="px-2 py-1.5">Fecha</th>
                        <th className="px-2 py-1.5">Descripción</th>
                        <th className="px-2 py-1.5">Categoría</th>
                        <th className="px-2 py-1.5 text-right">Monto CLP</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-zinc-100 dark:divide-zinc-800">
                      {movs.map((m) => {
                        const enCuotas = m.cuotasTotal !== null && m.cuotasTotal > 1;
                        const montoEfectivo = Math.abs(m.montoMesCLP);
                        const montoTotal = Math.abs(m.montoCLP);
                        return (
                          <tr key={m.idx} className="hover:bg-zinc-50 dark:hover:bg-zinc-900/50">
                            <td className="whitespace-nowrap px-2 py-1.5 text-zinc-500">
                              <Calendar className="mr-1 inline h-3 w-3" />
                              {m.fechaISO}
                            </td>
                            <td className="px-2 py-1.5">
                              <p className="max-w-[16ch] truncate font-medium text-zinc-700 dark:text-zinc-300" title={m.descripcion}>
                                {m.descripcion}
                              </p>
                              <p className="text-[10px] text-zinc-400">
                                {m.banco} · {m.persona}
                                {enCuotas && (
                                  <span className="ml-1 inline-flex items-center rounded bg-blue-50 px-1 py-px text-[9px] font-medium text-blue-700 dark:bg-blue-950/40 dark:text-blue-300">
                                    Cuota {m.cuotaActual ?? "?"}/{m.cuotasTotal}
                                  </span>
                                )}
                              </p>
                            </td>
                            <td className="px-2 py-1.5 text-zinc-500">
                              <p>{m.categoria}</p>
                              {m.subcategoria && <p className="text-[10px] text-zinc-400">{m.subcategoria}</p>}
                            </td>
                            <td className="whitespace-nowrap px-2 py-1.5 text-right tabular-nums">
                              <p className="text-zinc-900 dark:text-zinc-50">{formatCLP(montoEfectivo)}</p>
                              {enCuotas && (
                                <p className="text-[10px] text-zinc-400">de {formatCLP(montoTotal)} total</p>
                              )}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
                {idxsActivos.length === 0 && (
                  <p className="mt-2 text-xs text-zinc-500">Este KPI no se calcula a partir de movimientos individuales.</p>
                )}
              </section>
            )}

            {idxsActivos.length === 0 && !kpi.pasosCalculo && (
              <section>
                <p className="text-sm text-zinc-500">
                  Este KPI no tiene drill-down a movimientos {kpi.fuenteDatos ? `— viene de ${kpi.fuenteDatos}` : ""}.
                </p>
              </section>
            )}

            {kpi.recomendacion && (
              <section>
                <h3 className="mb-1 text-xs font-semibold uppercase tracking-wide text-zinc-500">Recomendación</h3>
                <div className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-200">
                  {kpi.recomendacion}
                </div>
              </section>
            )}
          </div>
        </div>
      </div>
    </>
  );
}
