// Capa de datos del dashboard: lee TODO desde el backend Python (Firestore),
// que es la fuente única desde la migración 2026-06. Reemplaza a lib/sheets.ts.
//
// El mapper produce el mismo shape `Movimiento` que producía sheets.ts, así que
// lib/kpis.ts queda intacto. La derivación de tipoMovimiento / esencial / fijo
// vive en el backend (campos calculados tipo_movimiento / esencial_efectivo /
// fijo_efectivo) — por eso los KPIs y la vista Movimientos cuadran por
// construcción: comparten exactamente el mismo valor.

import { fetchBackend } from "./movimientos-api";
import {
  DashboardData,
  DeudaMaestro,
  DeudaSnapshot,
  InversionMaestro,
  InversionSnapshot,
  Moneda,
  Movimiento,
  PatrimonioRow,
  TaxonomiaRow,
} from "./types";
import { expandirCuotas } from "./cuotas";
import { parseChileanDate } from "./utils";

// Frescura aceptable para finanzas personales; evita 1 lectura full-collection
// de Firestore por cada navegación al dashboard (page es force-dynamic).
const REVALIDATE_S = 60;

async function getJSON<T>(path: string, fallback: T): Promise<T> {
  try {
    const r = await fetchBackend(path, { next: { revalidate: REVALIDATE_S } });
    if (!r.ok) return fallback;
    return (await r.json()) as T;
  } catch {
    return fallback;
  }
}

function num(v: unknown): number {
  const n = typeof v === "number" ? v : parseFloat(String(v ?? ""));
  return Number.isFinite(n) ? n : 0;
}

function asMoneda(s: unknown): Moneda {
  const v = String(s ?? "").toUpperCase();
  return v === "USD" ? "USD" : v === "UF" ? "UF" : "CLP";
}

function fechaFromISO(date: string): Date | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(date || "");
  if (!m) return null;
  return new Date(Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3])));
}

// ── Shape de un movimiento Firestore (subset que consume el dashboard) ──
interface BackendMov {
  date: string;
  description: string;
  amount: number;
  bank: string;
  persona: string | null;
  final_category: string | null;
  suggested_category: string | null;
  final_subcategory: string | null;
  suggested_subcategory: string | null;
  moneda: string | null;
  monto_clp: number | null;
  saldo: number | null;
  cuotas_actual: number | null;
  cuotas_total: number | null;
  cuota_monto: number | null;
  recurrente: boolean | null;
  extraordinario: boolean | null;
  excluido: boolean | null;
  notas: string | null;
  // Campos calculados por el backend (fuente única):
  tipo_movimiento: Movimiento["tipoMovimiento"];
  esencial_efectivo: boolean;
  fijo_efectivo: boolean;
}

function mapMovimiento(m: BackendMov, idx: number): Movimiento | null {
  const fecha = fechaFromISO(m.date);
  if (!fecha) return null;
  const dd = String(fecha.getUTCDate()).padStart(2, "0");
  const mm = String(fecha.getUTCMonth() + 1).padStart(2, "0");
  const fechaISO = `${dd}/${mm}/${fecha.getUTCFullYear()}`;

  const amount = num(m.amount);
  // El mundo GSheet (que kpis.ts espera) usa montos ABSOLUTOS + `tipo`
  // Abono/Cargo para la dirección. Replicamos eso exacto.
  const montoCLP = Math.abs(m.monto_clp != null ? num(m.monto_clp) : amount);
  const cuotasTotal = m.cuotas_total != null ? num(m.cuotas_total) : null;
  const cuotaAPagar = m.cuota_monto != null ? Math.abs(num(m.cuota_monto)) : null;
  const montoMesCLP =
    cuotasTotal && cuotasTotal > 1
      ? cuotaAPagar !== null
        ? cuotaAPagar
        : montoCLP / cuotasTotal
      : montoCLP;

  return {
    idx,
    fecha,
    fechaISO,
    banco: m.bank || "",
    persona: m.persona || "",
    descripcion: m.description || "",
    monto: montoCLP,
    montoCLP,
    montoMesCLP,
    tipo: amount >= 0 ? "Abono" : "Cargo",
    saldo: m.saldo != null ? num(m.saldo) : null,
    categoria: (m.final_category || m.suggested_category || "").trim(),
    subcategoria: (m.final_subcategory || m.suggested_subcategory || "").trim(),
    cuotaActual: m.cuotas_actual != null ? num(m.cuotas_actual) : null,
    cuotasTotal,
    cuotaAPagar,
    moneda: asMoneda(m.moneda),
    esencial: !!m.esencial_efectivo,
    fijo: !!m.fijo_efectivo,
    recurrente: !!m.recurrente,
    extraordinario: !!m.extraordinario,
    excluido: !!m.excluido,
    notas: m.notas || "",
    tipoMovimiento: m.tipo_movimiento,
  };
}

export async function loadDashboardData(): Promise<DashboardData> {
  const warnings: string[] = [];

  const [movsRes, catsRes, patSnaps, deudasMaes, deudasSnaps, invMaes, invSnaps] = await Promise.all([
    getJSON<{ items: BackendMov[] }>("/api/movements/export", { items: [] }),
    getJSON<{ taxonomy: Record<string, string[]> }>("/api/categories", { taxonomy: {} }),
    getJSON<{ items: Record<string, unknown>[] }>("/api/patrimonio/snapshots", { items: [] }),
    getJSON<{ items: Record<string, unknown>[] }>("/api/deudas/maestro", { items: [] }),
    getJSON<{ items: Record<string, unknown>[] }>("/api/deudas/snapshots", { items: [] }),
    getJSON<{ items: Record<string, unknown>[] }>("/api/inversiones/maestro", { items: [] }),
    getJSON<{ items: Record<string, unknown>[] }>("/api/inversiones/snapshots", { items: [] }),
  ]);

  const movimientosRaw = movsRes.items
    .map((m, i) => mapMovimiento(m, i))
    .filter((m): m is Movimiento => m !== null);
  if (movimientosRaw.length === 0) {
    warnings.push("Sin movimientos del backend. ¿BACKEND_API_URL/TOKEN bien configurados y feature activa?");
  }

  // taxonomia: kpis.ts solo usa `.categoria` (set de categorías cubiertas).
  const taxonomia: TaxonomiaRow[] = Object.entries(catsRes.taxonomy).flatMap(([cat, subs]) =>
    (subs.length ? subs : [""]).map((sub) => ({
      categoria: cat,
      subcategoria: sub,
      esencial: false,
      fijo: false,
      recurrentePorDefecto: false,
      tipoMovimiento: "GastoReal" as const,
    })),
  );

  const patrimonio: PatrimonioRow[] = patSnaps.items.map((p) => ({
    mes: String(p.mes ?? ""),
    cajaLiquida: num(p.caja_liquida),
    activosInvertidos: num(p.activos_invertidos),
    activosIliquidos: num(p.activos_iliquidos),
    activosTotales: num(p.activos_totales),
    pasivosTotales: num(p.pasivos_totales),
    patrimonioNeto: num(p.patrimonio_neto),
    notas: String(p.notas ?? ""),
  }));

  const deudasMaestro: DeudaMaestro[] = deudasMaes.items.map((d) => ({
    id: String(d.id ?? ""),
    institucion: String(d.institucion ?? ""),
    tipo: String(d.tipo ?? ""),
    moneda: asMoneda(d.moneda),
    saldoOriginal: num(d.saldo_original),
    tasaAnual: num(d.tasa_anual),
    cuota: num(d.cuota),
    cuotasRestantes: num(d.cuotas_restantes),
    proximoVencimiento: parseChileanDate(String(d.proximo_vencimiento ?? "")),
    activa: !!d.activa,
  }));

  const deudasSnapshot: DeudaSnapshot[] = deudasSnaps.items.map((d) => ({
    mes: String(d.mes ?? ""),
    id: String(d.id ?? ""),
    saldoActual: num(d.saldo_actual),
    saldoCLP: num(d.saldo_clp),
    interesesPagadosMes: num(d.intereses_pagados_mes),
    capitalPagadoMes: num(d.capital_pagado_mes),
  }));

  const inversionesMaestro: InversionMaestro[] = invMaes.items.map((i) => ({
    id: String(i.id ?? ""),
    activo: String(i.activo ?? ""),
    clase: String(i.clase ?? ""),
    subclase: String(i.subclase ?? ""),
    moneda: asMoneda(i.moneda),
    pais: String(i.pais ?? ""),
    institucion: String(i.institucion ?? ""),
    liquidez: (["Alta", "Media", "Baja"].includes(String(i.liquidez)) ? i.liquidez : "") as InversionMaestro["liquidez"],
    fechaInicio: parseChileanDate(String(i.fecha_inicio ?? "")),
    activa: !!i.activa,
  }));

  const inversionesSnapshot: InversionSnapshot[] = invSnaps.items.map((i) => ({
    mes: String(i.mes ?? ""),
    id: String(i.id ?? ""),
    aportesDelMes: num(i.aportes_del_mes),
    retirosDelMes: num(i.retiros_del_mes),
    valorMonedaOrig: num(i.valor_moneda_orig),
    tipoCambioCierre: num(i.tipo_cambio_cierre),
    valorCLP: num(i.valor_clp),
    notas: String(i.notas ?? ""),
  }));

  return {
    movimientos: expandirCuotas(movimientosRaw, warnings),
    taxonomia,
    // Pestañas placeholder: kpis.ts no las usa en cálculos.
    presupuesto: [],
    tipoCambio: [],
    deudasMaestro,
    deudasSnapshot,
    inversionesMaestro,
    inversionesSnapshot,
    inversionesObjetivo: [],
    activosIliquidos: [],
    patrimonio,
    metas: [],
    ingresosEsperados: [],
    egresosEsperados: [],
    fetchedAt: new Date().toISOString(),
    warnings,
  };
}
