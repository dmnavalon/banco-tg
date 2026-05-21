import { google, sheets_v4 } from "googleapis";
import { readFileSync } from "node:fs";
import {
  ActivoIliquido,
  DashboardData,
  DeudaMaestro,
  DeudaSnapshot,
  EgresoEsperado,
  IngresoEsperado,
  InversionMaestro,
  InversionObjetivo,
  InversionSnapshot,
  MetaRow,
  Moneda,
  Movimiento,
  PatrimonioRow,
  PresupuestoRow,
  TaxonomiaRow,
  TipoCambioRow,
  TipoMovimiento,
} from "./types";
import { parseBool, parseChileanDate, parseNumber } from "./utils";

const SPREADSHEET_ID = process.env.GSHEET_SPREADSHEET_ID!;

let sheetsClient: sheets_v4.Sheets | null = null;

function loadCredentials(): Record<string, unknown> {
  // Cloud: JSON content directamente en env var
  const jsonInline = process.env.GOOGLE_SERVICE_ACCOUNT_KEY_JSON?.trim();
  if (jsonInline) {
    return JSON.parse(jsonInline);
  }
  // Local: archivo en disco
  const keyPath = process.env.GOOGLE_SERVICE_ACCOUNT_KEY_PATH?.trim();
  if (keyPath) {
    return JSON.parse(readFileSync(keyPath, "utf-8"));
  }
  throw new Error(
    "Falta credencial: configurá GOOGLE_SERVICE_ACCOUNT_KEY_JSON (cloud) o GOOGLE_SERVICE_ACCOUNT_KEY_PATH (local).",
  );
}

function getSheetsClient(): sheets_v4.Sheets {
  if (sheetsClient) return sheetsClient;
  const auth = new google.auth.GoogleAuth({
    credentials: loadCredentials(),
    scopes: ["https://www.googleapis.com/auth/spreadsheets.readonly"],
  });
  sheetsClient = google.sheets({ version: "v4", auth });
  return sheetsClient;
}

class SheetMissingError extends Error {
  constructor(public range: string, public httpStatus: number) {
    super(`Sheet/range no disponible (${httpStatus}): ${range}`);
    this.name = "SheetMissingError";
  }
}

async function readRange(range: string): Promise<string[][]> {
  const sheets = getSheetsClient();
  try {
    const res = await sheets.spreadsheets.values.get({
      spreadsheetId: SPREADSHEET_ID,
      range,
      valueRenderOption: "UNFORMATTED_VALUE",
      dateTimeRenderOption: "FORMATTED_STRING",
    });
    return (res.data.values as string[][]) || [];
  } catch (err) {
    const status = (err as { code?: number; status?: number }).code ?? (err as { status?: number }).status;
    if (status === 400 || status === 404) {
      // Antes se silenciaba (`return []`), ocultando hojas borradas/renombradas.
      // Ahora se propaga como SheetMissingError para que `loadDashboardData`
      // pueda agregarlo a `warnings` y el caller distinga "hoja vacía" de
      // "hoja desaparecida".
      throw new SheetMissingError(range, status ?? 0);
    }
    throw err;
  }
}

async function readRangeOrEmpty(range: string, warnings: string[]): Promise<string[][]> {
  try {
    return await readRange(range);
  } catch (err) {
    if (err instanceof SheetMissingError) {
      warnings.push(`Rango "${range}" no encontrado (HTTP ${err.httpStatus}). Verifica que la hoja exista en el spreadsheet.`);
      return [];
    }
    throw err;
  }
}

function asMoneda(s: string): Moneda {
  const v = (s || "").toUpperCase();
  if (v === "USD") return "USD";
  if (v === "UF") return "UF";
  return "CLP";
}

function asTipoMovimiento(s: string): TipoMovimiento {
  const valid: TipoMovimiento[] = [
    "Ingreso", "GastoReal", "MovimientoInterno", "PagoDeuda",
    "Ahorro", "AporteInversión", "RetiroInversión", "Devolución",
    "Impuesto", "GastoPorRendir",
  ];
  return valid.includes(s as TipoMovimiento) ? (s as TipoMovimiento) : "GastoReal";
}

// Columnas requeridas en la hoja "Movimientos". Si falta alguna, se agrega
// warning y se usan defaults seguros (0 / "" según corresponda).
const REQUIRED_MOV_COLS = [
  "Fecha", "Banco", "Persona", "Descripción", "Monto", "Tipo", "Saldo",
  "Categoría", "Subcategoría",
  "Cuota actual", "Cuotas total", "Cuota a pagar",
  "Moneda", "MontoCLP", "Esencial", "Fijo",
  "Recurrente", "Extraordinario", "Excluido", "Notas",
] as const;

async function readMovimientos(taxonomia: TaxonomiaRow[], warnings: string[]): Promise<Movimiento[]> {
  // Lee header dinámicamente para no atar el dashboard a un orden de columnas
  // específico. Si el sheet se reordena (ej. extend_sheet_header.py inserta
  // columnas), el dashboard sigue funcionando mientras los nombres existan.
  let headerRow: string[][] = [];
  try {
    headerRow = await readRange("Movimientos!1:1");
  } catch (err) {
    if (err instanceof SheetMissingError) {
      warnings.push(`Hoja "Movimientos" no encontrada (HTTP ${err.httpStatus}). Dashboard sin datos.`);
      return [];
    }
    throw err;
  }
  const header = headerRow[0] || [];
  if (header.length === 0) {
    warnings.push("Hoja 'Movimientos' sin header. Dashboard sin datos.");
    return [];
  }
  const colIdx = new Map<string, number>();
  header.forEach((name, i) => {
    const trimmed = (name || "").trim();
    if (trimmed) colIdx.set(trimmed, i);
  });
  for (const required of REQUIRED_MOV_COLS) {
    if (!colIdx.has(required)) {
      warnings.push(`Columna "${required}" no encontrada en hoja Movimientos. Se usará default.`);
    }
  }
  const get = (r: string[], name: string): string => {
    const i = colIdx.get(name);
    if (i === undefined) return "";
    const v = r[i];
    return v === undefined || v === null ? "" : String(v);
  };

  const lastCol = header.length;
  const lastColLetter = colNumberToLetter(lastCol);
  const rows = await readRangeOrEmpty(`Movimientos!A2:${lastColLetter}`, warnings);
  const taxIndex = new Map<string, TaxonomiaRow>();
  for (const t of taxonomia) {
    taxIndex.set(t.categoria, t);
  }

  let invalidDateCount = 0;
  const result: Movimiento[] = [];
  rows.filter((r) => r && r.length > 0 && r[0]).forEach((r, i) => {
    const fechaRaw = get(r, "Fecha");
    const fechaParsed = parseChileanDate(fechaRaw);
    if (fechaParsed === null) {
      invalidDateCount += 1;
      return;
    }
    const fecha = fechaParsed;
    // fechaISO siempre en formato DD/MM/YYYY para consistencia visual,
    // independiente de cómo viene del sheet (número serial o string).
    const dd = String(fecha.getUTCDate()).padStart(2, "0");
    const mm = String(fecha.getUTCMonth() + 1).padStart(2, "0");
    const yyyy = fecha.getUTCFullYear();
    const fechaISO = `${dd}/${mm}/${yyyy}`;
    const categoria = String(get(r, "Categoría") || "").trim();
    const subcategoria = String(get(r, "Subcategoría") || "").trim();

    const cuotaActualRaw = get(r, "Cuota actual");
    const cuotasTotalRaw = get(r, "Cuotas total");
    const cuotaAPagarRaw = get(r, "Cuota a pagar");
    const cuotaActual = cuotaActualRaw.trim() !== "" ? parseNumber(cuotaActualRaw) : null;
    const cuotasTotal = cuotasTotalRaw.trim() !== "" ? parseNumber(cuotasTotalRaw) : null;
    const cuotaAPagar = cuotaAPagarRaw.trim() !== "" ? parseNumber(cuotaAPagarRaw) : null;

    const moneda = asMoneda(String(get(r, "Moneda") || "CLP"));
    const monto = parseNumber(get(r, "Monto"));
    const montoCLP = parseNumber(get(r, "MontoCLP")) || monto;
    const tipoStr = String(get(r, "Tipo") || "");

    const montoMesCLP =
      cuotasTotal && cuotasTotal > 1
        ? cuotaAPagar !== null
          ? cuotaAPagar
          : montoCLP / cuotasTotal
        : montoCLP;

    const tax = taxIndex.get(categoria);
    const esencial = parseBool(get(r, "Esencial")) || (tax?.esencial ?? false);
    const fijo = parseBool(get(r, "Fijo")) || (tax?.fijo ?? false);
    const recurrente = parseBool(get(r, "Recurrente"));
    const extraordinario = parseBool(get(r, "Extraordinario"));
    const excluido = parseBool(get(r, "Excluido"));
    const tipoMovimiento = tax?.tipoMovimiento ?? (tipoStr === "Abono" ? "Ingreso" : "GastoReal");
    const saldoRaw = get(r, "Saldo");

    result.push({
      idx: i,
      fecha,
      fechaISO,
      banco: String(get(r, "Banco") || ""),
      persona: String(get(r, "Persona") || ""),
      descripcion: String(get(r, "Descripción") || ""),
      monto,
      montoCLP,
      montoMesCLP,
      tipo: (tipoStr === "Abono" || tipoStr === "Cargo") ? tipoStr : "",
      saldo: saldoRaw.trim() !== "" ? parseNumber(saldoRaw) : null,
      categoria,
      subcategoria,
      cuotaActual,
      cuotasTotal,
      cuotaAPagar,
      moneda,
      esencial,
      fijo,
      recurrente,
      extraordinario,
      excluido,
      notas: String(get(r, "Notas") || ""),
      tipoMovimiento,
    });
  });
  if (invalidDateCount > 0) {
    warnings.push(`${invalidDateCount} movimiento(s) descartados por fecha inválida en columna A.`);
  }
  return result;
}

function colNumberToLetter(n: number): string {
  let result = "";
  let num = n;
  while (num > 0) {
    const rem = (num - 1) % 26;
    result = String.fromCharCode(65 + rem) + result;
    num = Math.floor((num - 1) / 26);
  }
  return result;
}

async function readTaxonomia(warnings: string[]): Promise<TaxonomiaRow[]> {
  const rows = await readRangeOrEmpty("TaxonomíaExtendida!A2:F", warnings);
  return rows
    .filter((r) => r && r[0])
    .map((r): TaxonomiaRow => ({
      categoria: String(r[0] || ""),
      subcategoria: String(r[1] || ""),
      esencial: parseBool(r[2]),
      fijo: parseBool(r[3]),
      recurrentePorDefecto: parseBool(r[4]),
      tipoMovimiento: asTipoMovimiento(String(r[5] || "GastoReal")),
    }));
}

async function readPresupuesto(warnings: string[]): Promise<PresupuestoRow[]> {
  const rows = await readRangeOrEmpty("Presupuesto!A2:F", warnings);
  return rows
    .filter((r) => r && r[0])
    .map((r): PresupuestoRow => ({
      año: parseNumber(r[0]),
      mes: parseNumber(r[1]),
      categoria: String(r[2] || ""),
      subcategoria: String(r[3] || ""),
      montoCLP: parseNumber(r[4]),
      notas: String(r[5] || ""),
    }));
}

async function readTipoCambio(warnings: string[]): Promise<TipoCambioRow[]> {
  const rows = await readRangeOrEmpty("TipoCambio!A2:C", warnings);
  return rows
    .filter((r) => r && r[0])
    .map((r): TipoCambioRow => ({
      fecha: parseChileanDate(String(r[0])) ?? new Date(0),
      moneda: asMoneda(String(r[1])),
      valorCLP: parseNumber(r[2]),
    }));
}

async function readDeudasMaestro(warnings: string[]): Promise<DeudaMaestro[]> {
  const rows = await readRangeOrEmpty("Deudas_Maestro!A2:J", warnings);
  return rows
    .filter((r) => r && r[0])
    .map((r): DeudaMaestro => ({
      id: String(r[0] || ""),
      institucion: String(r[1] || ""),
      tipo: String(r[2] || ""),
      moneda: asMoneda(String(r[3])),
      saldoOriginal: parseNumber(r[4]),
      tasaAnual: parseNumber(r[5]),
      cuota: parseNumber(r[6]),
      cuotasRestantes: parseNumber(r[7]),
      proximoVencimiento: parseChileanDate(String(r[8])),
      activa: parseBool(r[9]),
    }));
}

async function readDeudasSnapshot(warnings: string[]): Promise<DeudaSnapshot[]> {
  const rows = await readRangeOrEmpty("Deudas_Snapshot!A2:F", warnings);
  return rows
    .filter((r) => r && r[0])
    .map((r): DeudaSnapshot => ({
      mes: String(r[0] || ""),
      id: String(r[1] || ""),
      saldoActual: parseNumber(r[2]),
      saldoCLP: parseNumber(r[3]),
      interesesPagadosMes: parseNumber(r[4]),
      capitalPagadoMes: parseNumber(r[5]),
    }));
}

async function readInversionesMaestro(warnings: string[]): Promise<InversionMaestro[]> {
  const rows = await readRangeOrEmpty("Inversiones_Maestro!A2:J", warnings);
  return rows
    .filter((r) => r && r[0])
    .map((r): InversionMaestro => {
      const liq = String(r[7] || "").trim();
      return {
        id: String(r[0] || ""),
        activo: String(r[1] || ""),
        clase: String(r[2] || ""),
        subclase: String(r[3] || ""),
        moneda: asMoneda(String(r[4])),
        pais: String(r[5] || ""),
        institucion: String(r[6] || ""),
        liquidez: (liq === "Alta" || liq === "Media" || liq === "Baja") ? liq : "",
        fechaInicio: parseChileanDate(String(r[8])),
        activa: parseBool(r[9]),
      };
    });
}

async function readInversionesSnapshot(warnings: string[]): Promise<InversionSnapshot[]> {
  const rows = await readRangeOrEmpty("Inversiones_Snapshot!A2:H", warnings);
  return rows
    .filter((r) => r && r[0])
    .map((r): InversionSnapshot => ({
      mes: String(r[0] || ""),
      id: String(r[1] || ""),
      aportesDelMes: parseNumber(r[2]),
      retirosDelMes: parseNumber(r[3]),
      valorMonedaOrig: parseNumber(r[4]),
      tipoCambioCierre: parseNumber(r[5]),
      valorCLP: parseNumber(r[6]),
      notas: String(r[7] || ""),
    }));
}

async function readInversionesObjetivo(warnings: string[]): Promise<InversionObjetivo[]> {
  const rows = await readRangeOrEmpty("InversionesObjetivo!A2:C", warnings);
  return rows
    .filter((r) => r && r[0])
    .map((r): InversionObjetivo => ({
      claseDeActivo: String(r[0] || ""),
      porcentajeObjetivo: parseNumber(r[1]),
      toleranciaPP: parseNumber(r[2]) || 5,
    }));
}

async function readActivosIliquidos(warnings: string[]): Promise<ActivoIliquido[]> {
  const rows = await readRangeOrEmpty("ActivosIlíquidos!A2:F", warnings);
  return rows
    .filter((r) => r && r[0])
    .map((r): ActivoIliquido => ({
      id: String(r[0] || ""),
      tipo: String(r[1] || ""),
      descripcion: String(r[2] || ""),
      valorEstimadoCLP: parseNumber(r[3]),
      fechaValuacion: parseChileanDate(String(r[4])),
      notas: String(r[5] || ""),
    }));
}

async function readPatrimonio(warnings: string[]): Promise<PatrimonioRow[]> {
  const rows = await readRangeOrEmpty("Patrimonio!A2:H", warnings);
  return rows
    .filter((r) => r && r[0])
    .map((r): PatrimonioRow => ({
      mes: String(r[0] || ""),
      cajaLiquida: parseNumber(r[1]),
      activosInvertidos: parseNumber(r[2]),
      activosIliquidos: parseNumber(r[3]),
      activosTotales: parseNumber(r[4]),
      pasivosTotales: parseNumber(r[5]),
      patrimonioNeto: parseNumber(r[6]),
      notas: String(r[7] || ""),
    }));
}

async function readMetas(warnings: string[]): Promise<MetaRow[]> {
  const rows = await readRangeOrEmpty("Metas!A2:F", warnings);
  return rows
    .filter((r) => r && r[0])
    .map((r): MetaRow => ({
      tipo: String(r[0] || ""),
      descripcion: String(r[1] || ""),
      valorObjetivoCLP: parseNumber(r[2]),
      fechaObjetivo: parseChileanDate(String(r[3])),
      valorActual: parseNumber(r[4]),
      porcentajeAvance: parseNumber(r[5]),
    }));
}

async function readIngresosEsperados(warnings: string[]): Promise<IngresoEsperado[]> {
  const rows = await readRangeOrEmpty("IngresosEsperados!A2:E", warnings);
  return rows
    .filter((r) => r && r[0])
    .map((r): IngresoEsperado => ({
      concepto: String(r[0] || ""),
      montoCLP: parseNumber(r[1]),
      fechaEstimada: parseChileanDate(String(r[2])),
      frecuencia: String(r[3] || ""),
      confirmado: parseBool(r[4]),
    }));
}

async function readEgresosEsperados(warnings: string[]): Promise<EgresoEsperado[]> {
  const rows = await readRangeOrEmpty("EgresosEsperados!A2:F", warnings);
  return rows
    .filter((r) => r && r[0])
    .map((r): EgresoEsperado => ({
      concepto: String(r[0] || ""),
      montoCLP: parseNumber(r[1]),
      fechaEstimada: parseChileanDate(String(r[2])),
      frecuencia: String(r[3] || ""),
      categoria: String(r[4] || ""),
      confirmado: parseBool(r[5]),
    }));
}

export async function loadDashboardData(): Promise<DashboardData> {
  const warnings: string[] = [];

  const taxonomia = await readTaxonomia(warnings);
  if (taxonomia.length === 0) {
    warnings.push("TaxonomíaExtendida vacía. Las clasificaciones esencial/fijo/tipo de movimiento usarán defaults conservadores.");
  }

  const [
    movimientos,
    presupuesto,
    tipoCambio,
    deudasMaestro,
    deudasSnapshot,
    inversionesMaestro,
    inversionesSnapshot,
    inversionesObjetivo,
    activosIliquidos,
    patrimonio,
    metas,
    ingresosEsperados,
    egresosEsperados,
  ] = await Promise.all([
    readMovimientos(taxonomia, warnings),
    readPresupuesto(warnings),
    readTipoCambio(warnings),
    readDeudasMaestro(warnings),
    readDeudasSnapshot(warnings),
    readInversionesMaestro(warnings),
    readInversionesSnapshot(warnings),
    readInversionesObjetivo(warnings),
    readActivosIliquidos(warnings),
    readPatrimonio(warnings),
    readMetas(warnings),
    readIngresosEsperados(warnings),
    readEgresosEsperados(warnings),
  ]);

  return {
    movimientos,
    taxonomia,
    presupuesto,
    tipoCambio,
    deudasMaestro,
    deudasSnapshot,
    inversionesMaestro,
    inversionesSnapshot,
    inversionesObjetivo,
    activosIliquidos,
    patrimonio,
    metas,
    ingresosEsperados,
    egresosEsperados,
    fetchedAt: new Date().toISOString(),
    warnings,
  };
}
