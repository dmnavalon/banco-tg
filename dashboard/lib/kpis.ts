import {
  Alerta,
  CategoriaGasto,
  Confianza,
  DashboardData,
  DashboardKPIs,
  DesviacionCategoria,
  Estado,
  Kpi,
  Movimiento,
} from "./types";
import { avg, deltaPct, monthKey, previousMonths } from "./utils";

// ─────────────────────────────────────────────────────────────────────────────
// Helpers de filtrado
// ─────────────────────────────────────────────────────────────────────────────

const isOperativo = (m: Movimiento): boolean =>
  !m.excluido && m.tipoMovimiento !== "MovimientoInterno";

const isIngresoOperativo = (m: Movimiento): boolean =>
  isOperativo(m) && m.tipoMovimiento === "Ingreso";

const isGastoReal = (m: Movimiento): boolean =>
  isOperativo(m) && m.tipoMovimiento === "GastoReal";

const isPagoDeuda = (m: Movimiento): boolean =>
  isOperativo(m) && m.tipoMovimiento === "PagoDeuda";

const isAhorroOInversion = (m: Movimiento): boolean =>
  isOperativo(m) && (m.tipoMovimiento === "Ahorro" || m.tipoMovimiento === "AporteInversión");

function montoEgreso(m: Movimiento): number {
  return Math.abs(m.montoCLP);
}

function montoIngreso(m: Movimiento): number {
  return Math.abs(m.montoCLP);
}

// ─────────────────────────────────────────────────────────────────────────────
// Agrupación por mes
// ─────────────────────────────────────────────────────────────────────────────

interface AgregadoMes {
  mes: string;
  ingresos: number;
  gastosReales: number;
  pagosDeuda: number;
  ahorroEInversion: number;
  flujoLibre: number;
  gastoEsencial: number;
  gastoDiscrecional: number;
  gastoFijo: number;
  gastoVariable: number;
  // Idxs de movimientos que entraron a cada bucket
  ingresosIdxs: number[];
  gastosRealesIdxs: number[];
  pagosDeudaIdxs: number[];
  ahorroEInversionIdxs: number[];
  gastoEsencialIdxs: number[];
  gastoDiscrecionalIdxs: number[];
  gastoFijoIdxs: number[];
  gastoVariableIdxs: number[];
}

function emptyAggregate(mes: string): AgregadoMes {
  return {
    mes,
    ingresos: 0, gastosReales: 0, pagosDeuda: 0, ahorroEInversion: 0,
    flujoLibre: 0, gastoEsencial: 0, gastoDiscrecional: 0, gastoFijo: 0, gastoVariable: 0,
    ingresosIdxs: [], gastosRealesIdxs: [], pagosDeudaIdxs: [], ahorroEInversionIdxs: [],
    gastoEsencialIdxs: [], gastoDiscrecionalIdxs: [], gastoFijoIdxs: [], gastoVariableIdxs: [],
  };
}

function agregarPorMes(movimientos: Movimiento[]): Map<string, AgregadoMes> {
  const map = new Map<string, AgregadoMes>();

  for (const m of movimientos) {
    if (m.excluido) continue;
    const k = monthKey(m.fecha);
    if (!map.has(k)) map.set(k, emptyAggregate(k));
    const agg = map.get(k)!;
    if (isIngresoOperativo(m)) {
      agg.ingresos += montoIngreso(m);
      agg.ingresosIdxs.push(m.idx);
    } else if (isGastoReal(m)) {
      const e = montoEgreso(m);
      agg.gastosReales += e;
      agg.gastosRealesIdxs.push(m.idx);
      if (m.esencial) { agg.gastoEsencial += e; agg.gastoEsencialIdxs.push(m.idx); }
      else { agg.gastoDiscrecional += e; agg.gastoDiscrecionalIdxs.push(m.idx); }
      if (m.fijo) { agg.gastoFijo += e; agg.gastoFijoIdxs.push(m.idx); }
      else { agg.gastoVariable += e; agg.gastoVariableIdxs.push(m.idx); }
    } else if (isPagoDeuda(m)) {
      agg.pagosDeuda += montoEgreso(m);
      agg.pagosDeudaIdxs.push(m.idx);
    } else if (isAhorroOInversion(m)) {
      agg.ahorroEInversion += montoEgreso(m);
      agg.ahorroEInversionIdxs.push(m.idx);
    }
  }

  for (const agg of map.values()) {
    agg.flujoLibre = agg.ingresos - agg.gastosReales - agg.pagosDeuda;
  }

  return map;
}

// ─────────────────────────────────────────────────────────────────────────────
// Construcción de Kpi
// ─────────────────────────────────────────────────────────────────────────────

function buildKpi(opts: {
  nombre: string;
  valor: number | null;
  formato: Kpi["formato"];
  formula?: string;
  confianza?: Confianza;
  estado?: Estado;
  comparaciones?: Kpi["comparaciones"];
  recomendacion?: string;
  meta?: number | null;
  presupuesto?: number | null;
  breakdownIdxs?: number[];
  pasosCalculo?: Kpi["pasosCalculo"];
  fuenteDatos?: string;
}): Kpi {
  return {
    nombre: opts.nombre,
    valor: opts.valor,
    formato: opts.formato,
    confianza: opts.confianza ?? (opts.valor === null ? "NoHayData" : "Real"),
    estado: opts.estado ?? "Sin data",
    formula: opts.formula,
    comparaciones: opts.comparaciones,
    recomendacion: opts.recomendacion,
    meta: opts.meta,
    presupuesto: opts.presupuesto,
    breakdownIdxs: opts.breakdownIdxs,
    pasosCalculo: opts.pasosCalculo,
    fuenteDatos: opts.fuenteDatos,
  };
}

function evaluarSemaforo(valor: number, umbralVerde: number, umbralAmarillo: number, mayorEsMejor = true): Estado {
  if (mayorEsMejor) {
    if (valor >= umbralVerde) return "Sano";
    if (valor >= umbralAmarillo) return "Atención";
    return "Crítico";
  } else {
    if (valor <= umbralVerde) return "Sano";
    if (valor <= umbralAmarillo) return "Atención";
    return "Crítico";
  }
}

function comparacionesContra(
  valor: number,
  agregados: Map<string, AgregadoMes>,
  mes: string,
  campo: keyof Omit<AgregadoMes, "mes">,
): Kpi["comparaciones"] {
  const mesAnt = previousMonths(mes, 1)[0];
  const m3 = previousMonths(mes, 3);
  const m6 = previousMonths(mes, 6);
  const m12 = previousMonths(mes, 12);
  const [year, month] = mes.split("-").map(Number);
  const mesAnioAnt = `${year - 1}-${String(month).padStart(2, "0")}`;

  const get = (k: string): number | null => {
    const a = agregados.get(k);
    if (!a) return null;
    return a[campo] as number;
  };

  const valuesIn = (keys: string[]): number[] =>
    keys.map(get).filter((v): v is number => v !== null);

  return {
    mesAnterior: get(mesAnt),
    avg3m: avg(valuesIn(m3)),
    avg6m: avg(valuesIn(m6)),
    avg12m: avg(valuesIn(m12)),
    mismoMesAnioAnterior: get(mesAnioAnt),
  };
}

// ─────────────────────────────────────────────────────────────────────────────
// Cálculos por sección
// ─────────────────────────────────────────────────────────────────────────────

function calcResumen(
  data: DashboardData,
  mesActual: string,
  agregados: Map<string, AgregadoMes>,
): DashboardKPIs["resumen"] {
  const aggMes = agregados.get(mesActual);
  const ingresosNetos = aggMes?.ingresos ?? null;
  const gastosTotales = aggMes?.gastosReales ?? null;
  const pagosDeuda = aggMes?.pagosDeuda ?? 0;
  const flujoLibre = ingresosNetos !== null && gastosTotales !== null ? ingresosNetos - gastosTotales - pagosDeuda : null;
  const ahorroEInversion = aggMes?.ahorroEInversion ?? 0;
  const tasaAhorro = ingresosNetos && ingresosNetos > 0 ? ahorroEInversion / ingresosNetos : null;
  const gastoEsencial = aggMes?.gastoEsencial ?? null;
  const gastoDiscrecional = aggMes?.gastoDiscrecional ?? null;
  const gastoFijo = aggMes?.gastoFijo ?? null;
  const gastoVariable = aggMes?.gastoVariable ?? null;

  // Patrimonio
  const patrimonioMesActual = data.patrimonio.find((p) => p.mes === mesActual);
  const patrimonioMesAnterior = data.patrimonio.find((p) => p.mes === previousMonths(mesActual, 1)[0]);
  const patrimonioNeto = patrimonioMesActual?.patrimonioNeto ?? null;
  const variacionPatrimonio =
    patrimonioMesActual && patrimonioMesAnterior
      ? patrimonioMesActual.patrimonioNeto - patrimonioMesAnterior.patrimonioNeto
      : null;

  // Fondo de emergencia
  const cajaLiquida = patrimonioMesActual?.cajaLiquida ?? null;
  const ultimosSeisMeses = previousMonths(mesActual, 6);
  const gastosEsencialesPasados = ultimosSeisMeses.map((m) => agregados.get(m)?.gastoEsencial ?? 0).filter((v) => v > 0);
  const gastoEsencialAvg = gastosEsencialesPasados.length > 0 ? avg(gastosEsencialesPasados) : null;
  const mesesFondoEmergencia =
    cajaLiquida !== null && gastoEsencialAvg !== null && gastoEsencialAvg > 0
      ? cajaLiquida / gastoEsencialAvg
      : null;

  // Endeudamiento mensual
  const deudasSnapshotMes = data.deudasSnapshot.filter((d) => d.mes === mesActual);
  const tienePagoDeudaSnapshot = deudasSnapshotMes.length > 0;
  const pagoMensualDeudaSnapshot = tienePagoDeudaSnapshot
    ? deudasSnapshotMes.reduce((s, d) => s + d.interesesPagadosMes + d.capitalPagadoMes, 0)
    : null;
  const pagoMensualDeuda = pagoMensualDeudaSnapshot ?? (pagosDeuda || null);
  const endeudamiento = pagoMensualDeuda !== null && ingresosNetos && ingresosNetos > 0 ? pagoMensualDeuda / ingresosNetos : null;

  // % gastos
  const pct = (n: number | null): number | null =>
    n !== null && ingresosNetos && ingresosNetos > 0 ? n / ingresosNetos : null;

  const gastoEsencialPct = pct(gastoEsencial);
  const gastoDiscrecionalPct = pct(gastoDiscrecional);
  const gastoFijoPct = pct(gastoFijo);
  const gastoVariablePct = pct(gastoVariable);

  // Estado general
  const estadoGeneral: Estado = (() => {
    if (flujoLibre === null) return "Sin data";
    if (flujoLibre < 0) return "Crítico";
    if (endeudamiento !== null && endeudamiento > 0.35) return "Crítico";
    if (mesesFondoEmergencia !== null && mesesFondoEmergencia < 1) return "Crítico";
    const sanoFlujo = ingresosNetos && flujoLibre / ingresosNetos > 0.1;
    const sanoAhorro = tasaAhorro !== null && tasaAhorro >= 0.1;
    const sanoDeuda = endeudamiento === null || endeudamiento < 0.2;
    const sanoFE = mesesFondoEmergencia === null ? false : mesesFondoEmergencia >= 3;
    if (sanoFlujo && sanoAhorro && sanoDeuda && sanoFE) return "Sano";
    return "Atención";
  })();

  return {
    ingresosNetos: buildKpi({
      nombre: "Ingresos netos del mes",
      valor: ingresosNetos,
      formato: "CLP",
      formula: `Σ movimientos del mes ${mesActual} con tipoMovimiento=Ingreso, excluyendo movimientos internos y excluidos. ${aggMes?.ingresosIdxs.length ?? 0} movimientos sumados.`,
      breakdownIdxs: aggMes?.ingresosIdxs,
      comparaciones: ingresosNetos !== null ? comparacionesContra(ingresosNetos, agregados, mesActual, "ingresos") : undefined,
    }),
    gastosTotales: buildKpi({
      nombre: "Gastos totales del mes",
      valor: gastosTotales,
      formato: "CLP",
      formula: `Σ movimientos del mes ${mesActual} con tipoMovimiento=GastoReal, excluyendo movimientos internos, pagos de tarjeta y excluidos. ${aggMes?.gastosRealesIdxs.length ?? 0} movimientos sumados.`,
      breakdownIdxs: aggMes?.gastosRealesIdxs,
      comparaciones: gastosTotales !== null ? comparacionesContra(gastosTotales, agregados, mesActual, "gastosReales") : undefined,
    }),
    flujoLibre: buildKpi({
      nombre: "Flujo libre mensual",
      valor: flujoLibre,
      formato: "CLP",
      formula: "Ingresos netos − Gastos totales − Pagos de deuda",
      estado: flujoLibre !== null ? (flujoLibre > 0 ? "Sano" : "Crítico") : "Sin data",
      recomendacion: flujoLibre !== null && flujoLibre < 0 ? "Recortar gasto discrecional este mes." : undefined,
      comparaciones: flujoLibre !== null ? comparacionesContra(flujoLibre, agregados, mesActual, "flujoLibre") : undefined,
      pasosCalculo: aggMes
        ? [
            { etiqueta: "Ingresos netos", valor: aggMes.ingresos, formato: "CLP", breakdownIdxs: aggMes.ingresosIdxs },
            { etiqueta: "− Gastos totales", valor: aggMes.gastosReales, formato: "CLP", breakdownIdxs: aggMes.gastosRealesIdxs },
            { etiqueta: "− Pagos de deuda", valor: aggMes.pagosDeuda, formato: "CLP", breakdownIdxs: aggMes.pagosDeudaIdxs },
            { etiqueta: "= Flujo libre", valor: flujoLibre, formato: "CLP" },
          ]
        : undefined,
    }),
    tasaAhorro: buildKpi({
      nombre: "Tasa de ahorro",
      valor: tasaAhorro,
      formato: "PCT",
      formula: "(Ahorro + Aporte a inversión) / Ingresos netos",
      estado: tasaAhorro !== null ? evaluarSemaforo(tasaAhorro, 0.2, 0.1) : "Sin data",
      recomendacion: tasaAhorro !== null && tasaAhorro < 0.1 ? "Aumentar ahorro hasta al menos 10%." : undefined,
      pasosCalculo: aggMes
        ? [
            { etiqueta: "Ahorro + aportes a inversión", valor: aggMes.ahorroEInversion, formato: "CLP", breakdownIdxs: aggMes.ahorroEInversionIdxs },
            { etiqueta: "÷ Ingresos netos", valor: aggMes.ingresos, formato: "CLP", breakdownIdxs: aggMes.ingresosIdxs },
            { etiqueta: "= Tasa de ahorro", valor: tasaAhorro, formato: "PCT" },
          ]
        : undefined,
    }),
    patrimonioNeto: buildKpi({
      nombre: "Patrimonio neto",
      valor: patrimonioNeto,
      formato: "CLP",
      formula: "Activos totales − Pasivos totales",
      fuenteDatos: patrimonioMesActual ? `Pestaña Patrimonio · fila del mes ${mesActual}` : "Pestaña Patrimonio (no hay snapshot del mes)",
      confianza: patrimonioNeto !== null ? "Real" : "NoHayData",
      recomendacion: patrimonioNeto === null ? "Poblá la pestaña Patrimonio con un snapshot mensual." : undefined,
      pasosCalculo: patrimonioMesActual
        ? [
            { etiqueta: "Activos líquidos (caja)", valor: patrimonioMesActual.cajaLiquida, formato: "CLP" },
            { etiqueta: "+ Activos invertidos", valor: patrimonioMesActual.activosInvertidos, formato: "CLP" },
            { etiqueta: "+ Activos ilíquidos", valor: patrimonioMesActual.activosIliquidos, formato: "CLP" },
            { etiqueta: "− Pasivos totales", valor: patrimonioMesActual.pasivosTotales, formato: "CLP" },
            { etiqueta: "= Patrimonio neto", valor: patrimonioMesActual.patrimonioNeto, formato: "CLP" },
          ]
        : undefined,
    }),
    variacionPatrimonio: buildKpi({
      nombre: "Variación mensual del patrimonio",
      valor: variacionPatrimonio,
      formato: "CLP",
      formula: "Patrimonio fin − Patrimonio inicio",
      fuenteDatos: "Pestaña Patrimonio (mes actual − mes anterior)",
      confianza: variacionPatrimonio !== null ? "Real" : "NoHayData",
      estado: variacionPatrimonio !== null ? (variacionPatrimonio >= 0 ? "Sano" : "Atención") : "Sin data",
      pasosCalculo: patrimonioMesActual && patrimonioMesAnterior
        ? [
            { etiqueta: `Patrimonio ${patrimonioMesActual.mes}`, valor: patrimonioMesActual.patrimonioNeto, formato: "CLP" },
            { etiqueta: `− Patrimonio ${patrimonioMesAnterior.mes}`, valor: patrimonioMesAnterior.patrimonioNeto, formato: "CLP" },
            { etiqueta: "= Variación", valor: variacionPatrimonio, formato: "CLP" },
          ]
        : undefined,
    }),
    mesesFondoEmergencia: buildKpi({
      nombre: "Meses de fondo de emergencia",
      valor: mesesFondoEmergencia,
      formato: "MESES",
      formula: "Caja líquida / Gasto esencial mensual promedio (últimos 6 meses con data, excluyendo extraordinarios)",
      confianza: mesesFondoEmergencia !== null ? "Real" : "NoHayData",
      estado: mesesFondoEmergencia !== null ? evaluarSemaforo(mesesFondoEmergencia, 6, 3) : "Sin data",
      recomendacion: mesesFondoEmergencia !== null && mesesFondoEmergencia < 3 ? "Aportar a caja hasta cubrir 3 meses." : undefined,
      pasosCalculo: cajaLiquida !== null && gastoEsencialAvg !== null
        ? [
            { etiqueta: "Caja líquida (pestaña Patrimonio)", valor: cajaLiquida, formato: "CLP" },
            { etiqueta: `÷ Gasto esencial avg ${gastosEsencialesPasados.length}m`, valor: gastoEsencialAvg, formato: "CLP" },
            { etiqueta: "= Meses cubiertos", valor: mesesFondoEmergencia, formato: "MESES" },
          ]
        : undefined,
    }),
    endeudamientoMensual: buildKpi({
      nombre: "Endeudamiento mensual",
      valor: endeudamiento,
      formato: "PCT",
      formula: "Pago mensual de deuda / Ingresos del mes",
      fuenteDatos: tienePagoDeudaSnapshot ? `Pestaña Deudas_Snapshot · mes ${mesActual}` : "Movimientos clasificados como PagoDeuda",
      confianza: endeudamiento !== null ? "Real" : "NoHayData",
      estado: endeudamiento !== null ? evaluarSemaforo(endeudamiento, 0.2, 0.35, false) : "Sin data",
      recomendacion: endeudamiento !== null && endeudamiento > 0.35 ? "Refinanciar o consolidar deuda cara." : undefined,
      pasosCalculo: pagoMensualDeuda !== null && ingresosNetos
        ? [
            { etiqueta: "Pago mensual de deuda", valor: pagoMensualDeuda, formato: "CLP", breakdownIdxs: tienePagoDeudaSnapshot ? undefined : aggMes?.pagosDeudaIdxs },
            { etiqueta: "÷ Ingresos netos", valor: ingresosNetos, formato: "CLP", breakdownIdxs: aggMes?.ingresosIdxs },
            { etiqueta: "= Endeudamiento", valor: endeudamiento, formato: "PCT" },
          ]
        : undefined,
    }),
    gastoEsencialPct: buildKpi({
      nombre: "Gasto esencial %",
      valor: gastoEsencialPct,
      formato: "PCT",
      formula: "Gasto esencial / Ingresos netos",
      breakdownIdxs: aggMes?.gastoEsencialIdxs,
      estado: gastoEsencialPct !== null ? evaluarSemaforo(gastoEsencialPct, 0.5, 0.7, false) : "Sin data",
      pasosCalculo: aggMes
        ? [
            { etiqueta: "Gasto esencial", valor: aggMes.gastoEsencial, formato: "CLP", breakdownIdxs: aggMes.gastoEsencialIdxs },
            { etiqueta: "÷ Ingresos netos", valor: aggMes.ingresos, formato: "CLP", breakdownIdxs: aggMes.ingresosIdxs },
            { etiqueta: "= % esencial", valor: gastoEsencialPct, formato: "PCT" },
          ]
        : undefined,
    }),
    gastoDiscrecionalPct: buildKpi({
      nombre: "Gasto discrecional %",
      valor: gastoDiscrecionalPct,
      formato: "PCT",
      formula: "Gasto discrecional / Ingresos netos",
      breakdownIdxs: aggMes?.gastoDiscrecionalIdxs,
      estado: gastoDiscrecionalPct !== null ? evaluarSemaforo(gastoDiscrecionalPct, 0.3, 0.5, false) : "Sin data",
      recomendacion: gastoDiscrecionalPct !== null && gastoDiscrecionalPct > 0.3 ? "Reducir top 3 categorías discrecionales." : undefined,
      pasosCalculo: aggMes
        ? [
            { etiqueta: "Gasto discrecional", valor: aggMes.gastoDiscrecional, formato: "CLP", breakdownIdxs: aggMes.gastoDiscrecionalIdxs },
            { etiqueta: "÷ Ingresos netos", valor: aggMes.ingresos, formato: "CLP", breakdownIdxs: aggMes.ingresosIdxs },
            { etiqueta: "= % discrecional", valor: gastoDiscrecionalPct, formato: "PCT" },
          ]
        : undefined,
    }),
    gastoFijoPct: buildKpi({
      nombre: "Gasto fijo %",
      valor: gastoFijoPct,
      formato: "PCT",
      formula: "Gasto fijo / Ingresos netos",
      breakdownIdxs: aggMes?.gastoFijoIdxs,
      estado: gastoFijoPct !== null ? evaluarSemaforo(gastoFijoPct, 0.5, 0.7, false) : "Sin data",
      pasosCalculo: aggMes
        ? [
            { etiqueta: "Gasto fijo", valor: aggMes.gastoFijo, formato: "CLP", breakdownIdxs: aggMes.gastoFijoIdxs },
            { etiqueta: "÷ Ingresos netos", valor: aggMes.ingresos, formato: "CLP", breakdownIdxs: aggMes.ingresosIdxs },
            { etiqueta: "= % fijo", valor: gastoFijoPct, formato: "PCT" },
          ]
        : undefined,
    }),
    gastoVariablePct: buildKpi({
      nombre: "Gasto variable %",
      valor: gastoVariablePct,
      formato: "PCT",
      formula: "Gasto variable / Ingresos netos",
      breakdownIdxs: aggMes?.gastoVariableIdxs,
      pasosCalculo: aggMes
        ? [
            { etiqueta: "Gasto variable", valor: aggMes.gastoVariable, formato: "CLP", breakdownIdxs: aggMes.gastoVariableIdxs },
            { etiqueta: "÷ Ingresos netos", valor: aggMes.ingresos, formato: "CLP", breakdownIdxs: aggMes.ingresosIdxs },
            { etiqueta: "= % variable", valor: gastoVariablePct, formato: "PCT" },
          ]
        : undefined,
    }),
    estadoGeneral,
  };
}

function calcEvolucion(agregados: Map<string, AgregadoMes>, patrimonio: { mes: string; patrimonioNeto: number }[]): DashboardKPIs["evolucion"] {
  const meses = Array.from(agregados.keys()).sort();

  return {
    ingresos: meses.map((m) => ({ mes: m, valor: agregados.get(m)!.ingresos })),
    gastos: meses.map((m) => ({ mes: m, valor: agregados.get(m)!.gastosReales })),
    flujoLibre: meses.map((m) => ({ mes: m, valor: agregados.get(m)!.flujoLibre })),
    patrimonio: patrimonio
      .slice()
      .sort((a, b) => a.mes.localeCompare(b.mes))
      .map((p) => ({ mes: p.mes, valor: p.patrimonioNeto })),
  };
}

function calcGastos(
  data: DashboardData,
  mesActual: string,
  agregados: Map<string, AgregadoMes>,
): DashboardKPIs["gastos"] {
  const movsMes = data.movimientos.filter((m) => isGastoReal(m) && monthKey(m.fecha) === mesActual);

  const groupBy = <K extends string>(arr: Movimiento[], keyFn: (m: Movimiento) => K): Map<K, CategoriaGasto> => {
    const map = new Map<K, CategoriaGasto>();
    for (const m of arr) {
      const k = keyFn(m);
      const existing = map.get(k);
      if (existing) {
        existing.montoCLP += montoEgreso(m);
        existing.cantidad += 1;
      } else {
        map.set(k, {
          categoria: m.categoria,
          subcategoria: m.subcategoria,
          montoCLP: montoEgreso(m),
          cantidad: 1,
          esencial: m.esencial,
          fijo: m.fijo,
        });
      }
    }
    return map;
  };

  const porCategoria = Array.from(groupBy(movsMes, (m) => m.categoria as string).values()).sort((a, b) => b.montoCLP - a.montoCLP);
  const porSubcategoria = Array.from(groupBy(movsMes, (m) => `${m.categoria}|${m.subcategoria}` as string).values()).sort((a, b) => b.montoCLP - a.montoCLP);
  const porComercio = Array.from(groupBy(movsMes, (m) => m.descripcion as string).values()).sort((a, b) => b.montoCLP - a.montoCLP);
  const porPersona = Array.from(groupBy(movsMes, (m) => m.persona as string).values()).sort((a, b) => b.montoCLP - a.montoCLP);

  // Desviaciones vs avg 6m por categoría
  const ultimosSeis = previousMonths(mesActual, 6);
  const desviaciones: DesviacionCategoria[] = porCategoria.map((c) => {
    const totalesPorMes = ultimosSeis.map((m) => {
      const movs = data.movimientos.filter(
        (mov) => isGastoReal(mov) && monthKey(mov.fecha) === m && mov.categoria === c.categoria,
      );
      return movs.reduce((s, mov) => s + montoEgreso(mov), 0);
    });
    const histAvg = avg(totalesPorMes.filter((v) => v > 0));
    const dif = histAvg !== null ? c.montoCLP - histAvg : 0;
    const difPct = histAvg !== null && histAvg > 0 ? dif / histAvg : 0;
    let explicacion = "";
    if (histAvg === null) explicacion = "sin información histórica suficiente";
    else if (difPct > 0.25) explicacion = `+${Math.round(difPct * 100)}% sobre el promedio histórico`;
    else if (difPct < -0.25) explicacion = `${Math.round(difPct * 100)}% bajo el promedio histórico`;
    else explicacion = "dentro de banda histórica";
    return {
      categoria: c.categoria,
      actual: c.montoCLP,
      promedioHistorico: histAvg ?? 0,
      diferenciaAbsoluta: dif,
      diferenciaPorcentual: difPct,
      explicacion,
    };
  });

  const extraordinarios = movsMes.filter((m) => m.extraordinario);
  const recurrentes = movsMes.filter((m) => m.recurrente);

  // Posibles duplicados: mismo monto + descripción + fecha ±2d
  const posiblesDuplicados: Movimiento[][] = [];
  const candidatos = movsMes.slice();
  const visitados = new Set<number>();
  for (let i = 0; i < candidatos.length; i++) {
    if (visitados.has(i)) continue;
    const a = candidatos[i];
    const grupo: Movimiento[] = [a];
    for (let j = i + 1; j < candidatos.length; j++) {
      if (visitados.has(j)) continue;
      const b = candidatos[j];
      const sameAmount = Math.abs(a.montoCLP - b.montoCLP) < 1;
      const sameDescr = a.descripcion.trim().toUpperCase() === b.descripcion.trim().toUpperCase();
      const dateDiff = Math.abs(a.fecha.getTime() - b.fecha.getTime()) / (1000 * 60 * 60 * 24);
      if (sameAmount && sameDescr && dateDiff <= 2) {
        grupo.push(b);
        visitados.add(j);
      }
    }
    if (grupo.length > 1) {
      visitados.add(i);
      posiblesDuplicados.push(grupo);
    }
  }

  const sinClasificar = movsMes.filter((m) => !m.categoria);

  return {
    porCategoria,
    porSubcategoria,
    porComercio,
    porPersona,
    desviaciones: desviaciones.sort((a, b) => Math.abs(b.diferenciaPorcentual) - Math.abs(a.diferenciaPorcentual)).slice(0, 10),
    extraordinarios,
    recurrentes,
    posiblesDuplicados,
    sinClasificar,
  };
}

function calcCalidadDatos(data: DashboardData): DashboardKPIs["calidadDatos"] {
  const total = data.movimientos.length;
  const sinCategoria = data.movimientos.filter((m) => !m.categoria);
  const clasificados = total - sinCategoria.length;
  const excluidos = data.movimientos.filter((m) => m.excluido).length;

  // Duplicados sospechosos en TODA la data
  const dupGroups: Movimiento[][] = [];
  const visitados = new Set<number>();
  for (let i = 0; i < data.movimientos.length; i++) {
    if (visitados.has(i)) continue;
    const a = data.movimientos[i];
    const grupo: Movimiento[] = [a];
    for (let j = i + 1; j < data.movimientos.length; j++) {
      if (visitados.has(j)) continue;
      const b = data.movimientos[j];
      const sameAmount = Math.abs(a.montoCLP - b.montoCLP) < 1 && a.montoCLP > 0;
      const sameDescr = a.descripcion.trim().toUpperCase() === b.descripcion.trim().toUpperCase();
      const dateDiff = Math.abs(a.fecha.getTime() - b.fecha.getTime()) / (1000 * 60 * 60 * 24);
      if (sameAmount && sameDescr && dateDiff <= 2) {
        grupo.push(b);
        visitados.add(j);
      }
    }
    if (grupo.length > 1) {
      visitados.add(i);
      dupGroups.push(grupo);
    }
  }
  const duplicados = dupGroups.flat().length;

  const taxonomiaCubre = new Set<string>(data.taxonomia.map((t) => t.categoria));
  const sinTaxonomia = data.movimientos.filter((m) => m.categoria && !taxonomiaCubre.has(m.categoria));

  const issues = [
    { tipo: "Sin categoría", count: sinCategoria.length, movimientos: sinCategoria.slice(0, 50) },
    { tipo: "Posibles duplicados", count: duplicados, movimientos: dupGroups.flat().slice(0, 50) },
    { tipo: "Categoría no cubierta por TaxonomíaExtendida", count: sinTaxonomia.length, movimientos: sinTaxonomia.slice(0, 50) },
    { tipo: "Excluidos del análisis", count: excluidos, movimientos: data.movimientos.filter((m) => m.excluido).slice(0, 50) },
  ].filter((i) => i.count > 0);

  return {
    totalMovimientos: total,
    clasificados,
    sinCategoria: sinCategoria.length,
    duplicados,
    excluidos,
    pctCompletos: total > 0 ? clasificados / total : 1,
    issues,
  };
}

function calcAlertas(
  data: DashboardData,
  kpis: DashboardKPIs["resumen"],
  gastos: DashboardKPIs["gastos"],
  calidad: DashboardKPIs["calidadDatos"],
): Alerta[] {
  const alertas: Alerta[] = [];

  // Flujo
  if (kpis.flujoLibre.valor !== null && kpis.flujoLibre.valor < 0) {
    alertas.push({
      id: "AF-01",
      severidad: "alta",
      categoria: "flujo",
      titulo: "Flujo libre mensual negativo",
      evidencia: `Flujo libre = ${kpis.flujoLibre.valor.toLocaleString("es-CL")} CLP`,
      accion: "Recortar gasto discrecional inmediato.",
      seccionDestino: "gastos",
    });
  }
  if (kpis.mesesFondoEmergencia.valor !== null && kpis.mesesFondoEmergencia.valor < 3) {
    alertas.push({
      id: "AF-05",
      severidad: "alta",
      categoria: "flujo",
      titulo: "Fondo de emergencia bajo 3 meses",
      evidencia: `Cobertura actual: ${kpis.mesesFondoEmergencia.valor.toFixed(1)} meses`,
      accion: "Aporte mensual fijo a caja líquida hasta cubrir 3 meses.",
      seccionDestino: "fondo",
    });
  }

  // Deuda
  if (kpis.endeudamientoMensual.valor !== null && kpis.endeudamientoMensual.valor > 0.35) {
    alertas.push({
      id: "AD-01",
      severidad: "alta",
      categoria: "deuda",
      titulo: "Endeudamiento sobre 35%",
      evidencia: `Endeudamiento: ${(kpis.endeudamientoMensual.valor * 100).toFixed(1)}%`,
      accion: "Refinanciar o consolidar deuda cara.",
      seccionDestino: "deuda",
    });
  }

  // Gastos
  for (const d of gastos.desviaciones) {
    if (d.diferenciaPorcentual > 0.25 && d.promedioHistorico > 0) {
      alertas.push({
        id: `AG-02-${d.categoria}`,
        severidad: "media",
        categoria: "gastos",
        titulo: `${d.categoria}: +${Math.round(d.diferenciaPorcentual * 100)}% sobre promedio`,
        evidencia: `Mes ${d.actual.toLocaleString("es-CL")} vs avg ${Math.round(d.promedioHistorico).toLocaleString("es-CL")}`,
        accion: `Investigar movimientos de ${d.categoria} este mes.`,
        seccionDestino: "gastos",
      });
    }
  }
  if (gastos.posiblesDuplicados.length > 0) {
    alertas.push({
      id: "AG-04",
      severidad: "media",
      categoria: "gastos",
      titulo: `${gastos.posiblesDuplicados.length} posibles duplicados detectados`,
      evidencia: `Grupos con mismo monto+descripción+fecha ±2d`,
      accion: "Revisar y eliminar duplicados.",
      seccionDestino: "calidad",
    });
  }

  // Datos
  if (calidad.pctCompletos < 0.95 && calidad.totalMovimientos > 0) {
    alertas.push({
      id: "AQ-01",
      severidad: "alta",
      categoria: "datos",
      titulo: `Sólo ${(calidad.pctCompletos * 100).toFixed(0)}% de movimientos clasificados`,
      evidencia: `${calidad.sinCategoria} sin categoría de ${calidad.totalMovimientos}`,
      accion: "Clasificar pendientes en el bot antes de cierre del mes.",
      seccionDestino: "calidad",
    });
  }

  return alertas.sort((a, b) => {
    const order = { alta: 0, media: 1, baja: 2 };
    return order[a.severidad] - order[b.severidad];
  });
}

// ─────────────────────────────────────────────────────────────────────────────
// Entrypoint
// ─────────────────────────────────────────────────────────────────────────────

export function calculateDashboard(data: DashboardData, mesOverride?: string): DashboardKPIs {
  const agregados = agregarPorMes(data.movimientos);
  const mesesConData = Array.from(agregados.keys()).sort();
  const mesActual = mesOverride ?? (mesesConData.length > 0 ? mesesConData[mesesConData.length - 1] : monthKey(new Date()));

  const resumen = calcResumen(data, mesActual, agregados);
  const evolucion = calcEvolucion(agregados, data.patrimonio);
  const gastos = calcGastos(data, mesActual, agregados);
  const calidadDatos = calcCalidadDatos(data);
  const alertas = calcAlertas(data, resumen, gastos, calidadDatos);

  return {
    resumen,
    evolucion,
    gastos,
    calidadDatos,
    alertas,
    mesActual,
    mesesConData,
  };
}
