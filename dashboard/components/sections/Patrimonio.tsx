"use client";

import { useCallback, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { RefreshCw, Building, AlertTriangle, CheckCircle2, Clock, Pencil } from "lucide-react";

import { DashboardData, InversionMaestro, InversionSnapshot } from "@/lib/types";
import { formatCLP } from "@/lib/utils";
import { Card, CardHeader } from "../ui/Card";
import { SectionHeader } from "../ui/SectionHeader";
import { EmptyState } from "../ui/EmptyState";

interface PatrimonioSnapshot {
  id: string;
  maestro: InversionMaestro;
  snapshot: InversionSnapshot | null;
  estado: "ok" | "sesion_expirada" | "error" | "manual" | "desconocido";
  estadoLabel: string;
  fuente: "scraper" | "manual" | "—";
  actualizado: string;
}

function parseEstadoFromNotas(notas: string): {
  estado: PatrimonioSnapshot["estado"];
  estadoLabel: string;
  fuente: PatrimonioSnapshot["fuente"];
  actualizado: string;
} {
  if (!notas) {
    return { estado: "desconocido", estadoLabel: "—", fuente: "—", actualizado: "" };
  }
  // Format esperado del runner: "act:2026-05-21 22:00 · scraper:ok"
  const tsMatch = notas.match(/act:([0-9\-: ]+)/);
  const actualizado = tsMatch ? tsMatch[1].trim() : "";
  if (notas.includes("scraper:ok")) {
    return { estado: "ok", estadoLabel: "Scraper OK", fuente: "scraper", actualizado };
  }
  if (notas.includes("scraper:sesion_expirada")) {
    return { estado: "sesion_expirada", estadoLabel: "Sesión expirada", fuente: "scraper", actualizado };
  }
  if (notas.includes("scraper:error")) {
    return { estado: "error", estadoLabel: "Error scraper", fuente: "scraper", actualizado };
  }
  if (notas.startsWith("manual") || notas.includes("manual:")) {
    return { estado: "manual", estadoLabel: "Ingreso manual", fuente: "manual", actualizado };
  }
  return { estado: "desconocido", estadoLabel: notas.slice(0, 32), fuente: "—", actualizado };
}

function latestSnapshotByMonth(snaps: InversionSnapshot[], id: string): InversionSnapshot | null {
  // `mes` es string YYYY-MM, comparación lexicográfica funciona
  const mine = snaps.filter((s) => s.id === id);
  if (!mine.length) return null;
  return mine.reduce((best, cur) => (cur.mes > best.mes ? cur : best));
}

function buildEvolucion(maestro: InversionMaestro[], snaps: InversionSnapshot[]) {
  const months = Array.from(new Set(snaps.map((s) => s.mes))).sort();
  return months.map((mes) => {
    const row: Record<string, string | number> = { mes };
    let total = 0;
    for (const m of maestro) {
      const s = snaps.find((x) => x.mes === mes && x.id === m.id);
      const v = s?.valorCLP ?? 0;
      row[m.activo || m.id] = v;
      total += v;
    }
    row.Total = total;
    return row;
  });
}

export function PatrimonioSection({ data }: { data: DashboardData }) {
  const router = useRouter();
  const [syncing, setSyncing] = useState(false);
  const [syncMsg, setSyncMsg] = useState<string | null>(null);

  const activos = useMemo(
    () => data.inversionesMaestro.filter((m) => m.activa),
    [data.inversionesMaestro],
  );

  const rows: PatrimonioSnapshot[] = useMemo(
    () =>
      activos.map((m): PatrimonioSnapshot => {
        const snap = latestSnapshotByMonth(data.inversionesSnapshot, m.id);
        const parsed = parseEstadoFromNotas(snap?.notas ?? "");
        return {
          id: m.id,
          maestro: m,
          snapshot: snap,
          ...parsed,
        };
      }),
    [activos, data.inversionesSnapshot],
  );

  const total = useMemo(
    () => rows.reduce((acc, r) => acc + (r.snapshot?.valorCLP ?? 0), 0),
    [rows],
  );

  const evolucion = useMemo(() => buildEvolucion(activos, data.inversionesSnapshot), [activos, data.inversionesSnapshot]);

  const handleSync = useCallback(async () => {
    setSyncing(true);
    setSyncMsg("Encolando request…");
    try {
      const r = await fetch("/api/patrimonio/sync", { method: "POST" });
      if (r.status === 202) {
        const body = (await r.json().catch(() => ({}))) as {
          request_at?: string;
        };
        const requestAt = body.request_at ?? new Date().toISOString();
        setSyncMsg("Request encolado. Esperando que tu Mac lo procese…");
        await pollStatusUntilDone(requestAt, setSyncMsg, router);
        setSyncing(false);
        setSyncMsg(null);
      } else if (r.status === 409) {
        const body = (await r.json().catch(() => ({}))) as { started_at?: string };
        setSyncMsg(
          `Ya hay una corrida en curso (desde ${body.started_at ?? "?"}). Esperando…`,
        );
        await pollStatusUntilDone(null, setSyncMsg, router);
        setSyncing(false);
        setSyncMsg(null);
      } else {
        const body = (await r.json().catch(() => ({}))) as {
          error?: string;
          message?: string;
        };
        const detalle = body.message ?? body.error ?? r.statusText ?? "(sin detalle)";
        setSyncMsg(`Error ${r.status}: ${detalle}`);
        console.error("Patrimonio sync error:", { status: r.status, body });
        setTimeout(() => setSyncing(false), 8_000);
      }
    } catch (e) {
      setSyncMsg(`Error de red: ${e instanceof Error ? e.message : String(e)}`);
      setTimeout(() => setSyncing(false), 5_000);
    }
  }, [router]);

  const warningsHojas = data.warnings.filter((w) =>
    /Inversiones_Maestro|Inversiones_Snapshot|Patrimonio/i.test(w),
  );

  const refreshButton = (
    <button
      onClick={handleSync}
      disabled={syncing}
      className="inline-flex items-center gap-1.5 rounded-md border border-zinc-200 bg-white px-3 py-1.5 text-xs font-medium text-zinc-700 transition-colors hover:bg-zinc-50 disabled:opacity-50 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-200 dark:hover:bg-zinc-800"
    >
      <RefreshCw className={"h-3.5 w-3.5 " + (syncing ? "animate-spin" : "")} />
      {syncing ? "Actualizando…" : "Actualizar ahora"}
    </button>
  );

  if (!activos.length) {
    return (
      <div className="space-y-6">
        <SectionHeader title="Patrimonio" question="Aumenta mi patrimonio y por qué" right={refreshButton} />
        {syncMsg && (
          <div className="rounded-md border border-blue-200 bg-blue-50 p-3 text-xs text-blue-800 dark:border-blue-900 dark:bg-blue-950/30 dark:text-blue-200">
            {syncMsg}
          </div>
        )}
        {warningsHojas.length > 0 && (
          <div className="rounded-md border border-amber-200 bg-amber-50 p-3 text-xs text-amber-800 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-300">
            <p className="font-medium">No pude leer las hojas de inversiones:</p>
            <ul className="ml-4 mt-1 list-disc">
              {warningsHojas.map((w, i) => <li key={i}>{w}</li>)}
            </ul>
            <p className="mt-2">Verifica en Vercel que <code>GSHEET_SPREADSHEET_ID</code> apunte al spreadsheet correcto y que el service account tenga acceso a esas hojas.</p>
          </div>
        )}
        <EmptyState
          icon={<Building className="h-10 w-10" />}
          title="Sin sitios configurados"
          description="Aún no hay inversiones registradas en Inversiones_Maestro. Configura un sitio con: python -m src.patrimonio.cli add fintual"
        />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <SectionHeader
        title="Patrimonio"
        question="Aumenta mi patrimonio y por qué"
        right={refreshButton}
      />

      {syncMsg && (
        <div className="rounded-md border border-blue-200 bg-blue-50 p-3 text-xs text-blue-800 dark:border-blue-900 dark:bg-blue-950/30 dark:text-blue-200">
          {syncMsg}
        </div>
      )}

      {/* Total */}
      <Card padding="lg">
        <p className="text-xs uppercase tracking-wide text-zinc-500">Patrimonio total invertido (CLP)</p>
        <p className="mt-2 text-4xl font-bold text-zinc-900 dark:text-zinc-50">{formatCLP(total)}</p>
        <p className="mt-1 text-xs text-zinc-500">
          {rows.length} sitio{rows.length === 1 ? "" : "s"} · suma del último snapshot por activo
        </p>
      </Card>

      {/* Tabla por sitio */}
      <Card padding="md">
        <CardHeader title="Detalle por sitio" subtitle="Último snapshot disponible" />
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="text-zinc-500">
              <tr>
                <th className="pb-2 pr-3">Sitio</th>
                <th className="pb-2 pr-3">Clase</th>
                <th className="pb-2 pr-3 text-right">Valor CLP</th>
                <th className="pb-2 pr-3">Última act.</th>
                <th className="pb-2 pr-3">Estado</th>
                <th className="pb-2"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-100 dark:divide-zinc-900">
              {rows.map((r) => (
                <tr key={r.id} className="text-zinc-700 dark:text-zinc-300">
                  <td className="py-2 pr-3 font-medium">{r.maestro.activo}</td>
                  <td className="py-2 pr-3 text-zinc-500">{r.maestro.clase}</td>
                  <td className="py-2 pr-3 text-right font-mono">{formatCLP(r.snapshot?.valorCLP ?? 0)}</td>
                  <td className="py-2 pr-3 text-zinc-500">{r.actualizado || "—"}</td>
                  <td className="py-2 pr-3">
                    <EstadoBadge estado={r.estado} label={r.estadoLabel} />
                  </td>
                  <td className="py-2 text-right">
                    <EditHint id={r.id} />
                  </td>
                </tr>
              ))}
            </tbody>
            <tfoot>
              <tr className="border-t border-zinc-200 font-semibold dark:border-zinc-800">
                <td colSpan={2} className="pt-2">Total</td>
                <td className="pt-2 text-right font-mono">{formatCLP(total)}</td>
                <td colSpan={3}></td>
              </tr>
            </tfoot>
          </table>
        </div>
      </Card>

      {/* Evolución mensual */}
      {evolucion.length > 1 && (
        <Card padding="md">
          <CardHeader title="Evolución mensual" subtitle="Suma de todos los activos · CLP" />
          <div className="h-64 w-full">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={evolucion} margin={{ top: 10, right: 10, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" className="stroke-zinc-200 dark:stroke-zinc-800" />
                <XAxis dataKey="mes" className="text-xs" />
                <YAxis className="text-xs" tickFormatter={(v) => formatCLP(v, true)} />
                <Tooltip formatter={(v) => formatCLP(Number(v))} />
                <Area type="monotone" dataKey="Total" stroke="#2563eb" fill="#2563eb" fillOpacity={0.18} />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </Card>
      )}
    </div>
  );
}

function EstadoBadge({ estado, label }: { estado: PatrimonioSnapshot["estado"]; label: string }) {
  const map: Record<PatrimonioSnapshot["estado"], { cls: string; Icon: typeof CheckCircle2 }> = {
    ok: { cls: "border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-900 dark:bg-emerald-950/30 dark:text-emerald-300", Icon: CheckCircle2 },
    sesion_expirada: { cls: "border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-900 dark:bg-amber-950/30 dark:text-amber-300", Icon: Clock },
    error: { cls: "border-red-200 bg-red-50 text-red-700 dark:border-red-900 dark:bg-red-950/30 dark:text-red-300", Icon: AlertTriangle },
    manual: { cls: "border-zinc-200 bg-zinc-50 text-zinc-600 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-400", Icon: Pencil },
    desconocido: { cls: "border-zinc-200 bg-zinc-50 text-zinc-500 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-500", Icon: Clock },
  };
  const m = map[estado];
  return (
    <span className={"inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] font-medium " + m.cls}>
      <m.Icon className="h-3 w-3" />
      {label}
    </span>
  );
}

function EditHint({ id }: { id: string }) {
  // Mapeo INV-FINTUAL → fintual para que Diego pueda copiar comando si quiere
  const slug = id.replace(/^INV-/, "").toLowerCase().replace(/-/g, "_");
  return (
    <span
      className="cursor-help text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-300"
      title={`Editar a mano:\npython -m src.patrimonio.cli edit ${slug} <monto> "<nota>"`}
    >
      <Pencil className="inline h-3.5 w-3.5" />
    </span>
  );
}

interface PatrimonioStatus {
  running: boolean;
  started_at: string | null;
  last_request_at: string | null;
  last_processed_at: string | null;
  daemon_heartbeat_at: string | null;
  summary: { total_clp?: number; ok?: number; errors?: number } | null;
  error: string | null;
}

function parseTs(s: string | null | undefined): number {
  if (!s) return 0;
  // Format: "YYYY-MM-DD HH:MM:SS" (timezone-naive de Santiago)
  const d = new Date(s.replace(" ", "T"));
  return d.getTime() || 0;
}

function heartbeatStale(s: PatrimonioStatus): boolean {
  if (!s.daemon_heartbeat_at) return true;
  const age = Date.now() - parseTs(s.daemon_heartbeat_at);
  // Daemon polea cada 30s. Si pasaron >2 min sin heartbeat, está caído o Mac dormida.
  return age > 120_000;
}

/**
 * Polea /api/patrimonio/status cada 5s hasta que se complete el job.
 * Si `requestAt` viene, espera específicamente a que se procese ESE request.
 * Si es null, espera a que termine cualquier corrida en curso.
 */
async function pollStatusUntilDone(
  requestAt: string | null,
  setMsg: (m: string | null) => void,
  router: ReturnType<typeof useRouter>,
): Promise<void> {
  const maxWaitMs = 8 * 60 * 1000; // 8 minutos
  const deadline = Date.now() + maxWaitMs;
  let warned_dormant = false;

  while (Date.now() < deadline) {
    await new Promise((res) => setTimeout(res, 5_000));
    let s: PatrimonioStatus;
    try {
      const r = await fetch("/api/patrimonio/status", { cache: "no-store" });
      s = (await r.json()) as PatrimonioStatus;
    } catch {
      setMsg("Sin conexión al backend. Reintentando…");
      continue;
    }

    if (!warned_dormant && heartbeatStale(s)) {
      setMsg(
        "⚠️ Tu Mac no está respondiendo (sin heartbeat hace >2 min). ¿Está encendida y el daemon corriendo?",
      );
      warned_dormant = true;
      continue;
    }

    if (s.running) {
      setMsg(`Scrapers corriendo en tu Mac (desde ${s.started_at})…`);
      continue;
    }

    // Job terminado. Verificá que se procesó nuestro request (o que es más reciente que cuando arrancamos).
    const processed = parseTs(s.last_processed_at);
    const targetTs = requestAt ? parseTs(requestAt) : 0;
    if (processed >= targetTs && processed > 0) {
      if (s.error) {
        setMsg(`Error en la corrida: ${s.error}`);
        return;
      }
      const total = s.summary?.total_clp;
      const ok = s.summary?.ok ?? 0;
      const errs = s.summary?.errors ?? 0;
      const totalStr =
        total !== undefined
          ? new Intl.NumberFormat("es-CL", { style: "currency", currency: "CLP", maximumFractionDigits: 0 }).format(total)
          : "?";
      setMsg(`✓ Actualizado · ${totalStr} · ${ok} sitios OK · ${errs} con error · refrescando…`);
      router.refresh();
      // Pequeño delay para que el usuario vea el mensaje antes de la recarga
      await new Promise((res) => setTimeout(res, 2_000));
      return;
    }
  }

  setMsg(
    "Timeout de 8 min sin respuesta. Verificá que tu Mac esté encendida y el daemon corriendo (`launchctl list | grep patrimonio.daemon`).",
  );
}
