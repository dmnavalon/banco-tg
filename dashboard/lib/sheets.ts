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

async function readMovimientos(taxonomia: TaxonomiaRow[]): Promise<Movimiento[]> {
  // Header oficial 24 col post-migración 2026-05-08:
  //  A=0  Fecha           M=12 Subcategoría        S=18 Esencial
  //  B=1  Día             N=13 Cuota actual        T=19 Fijo
  //  C=2  Mes             O=14 Cuotas total        U=20 Recurrente
  //  D=3  Año             P=15 Cuota a pagar       V=21 Extraordinario
  //  E=4  Día Semana      Q=16 Moneda              W=22 Excluido
  //  F=5  Banco           R=17 MontoCLP            X=23 Notas
  //  G=6  Persona
  //  H=7  Descripción
  //  I=8  Monto
  //  J=9  Tipo
  //  K=10 Saldo
  //  L=11 Categoría
  const rows = await readRange("Movimientos!A2:X");
  const taxIndex = new Map<string, TaxonomiaRow>();
  for (const t of taxonomia) {
    taxIndex.set(t.categoria, t);
  }

  return rows
    .filter((r) => r && r.length > 0 && r[0])
    .map((r, i): Movimiento => {
      const fechaISO = String(r[0] || "");
      const fecha = parseChileanDate(fechaISO) ?? new Date(0);
      const categoria = String(r[11] || "").trim();
      const subcategoria = String(r[12] || "").trim();

      // Cuotas
      const cuotaActualRaw = r[13];
      const cuotasTotalRaw = r[14];
      const cuotaAPagarRaw = r[15];
      const cuotaActual = cuotaActualRaw && String(cuotaActualRaw).trim() !== "" ? parseNumber(cuotaActualRaw) : null;
      const cuotasTotal = cuotasTotalRaw && String(cuotasTotalRaw).trim() !== "" ? parseNumber(cuotasTotalRaw) : null;
      const cuotaAPagar = cuotaAPagarRaw && String(cuotaAPagarRaw).trim() !== "" ? parseNumber(cuotaAPagarRaw) : null;

      const moneda = asMoneda(String(r[16] || "CLP"));
      const monto = parseNumber(r[8]);
      const montoCLP = parseNumber(r[17]) || monto;
      const tipoStr = String(r[9] || "");

      // Monto efectivo del mes:
      //   Si Cuotas total > 1 → cuotaAPagar (si existe) o montoCLP/cuotasTotal (aproximación).
      //   Si no → montoCLP.
      const montoMesCLP =
        cuotasTotal && cuotasTotal > 1
          ? cuotaAPagar !== null
            ? cuotaAPagar
            : montoCLP / cuotasTotal
          : montoCLP;

      const tax = taxIndex.get(categoria);
      const esencial = parseBool(r[18]) || (tax?.esencial ?? false);
      const fijo = parseBool(r[19]) || (tax?.fijo ?? false);
      const recurrente = parseBool(r[20]);
      const extraordinario = parseBool(r[21]);
      const excluido = parseBool(r[22]);
      const tipoMovimiento = tax?.tipoMovimiento ?? (tipoStr === "Abono" ? "Ingreso" : "GastoReal");

      return {
        idx: i,
        fecha,
        fechaISO,
        banco: String(r[5] || ""),
        persona: String(r[6] || ""),
        descripcion: String(r[7] || ""),
        monto,
        montoCLP,
        montoMesCLP,
        tipo: (tipoStr === "Abono" || tipoStr === "Cargo") ? tipoStr : "",
        saldo: r[10] ? parseNumber(r[10]) : null,
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
        notas: String(r[23] || ""),
        tipoMovimiento,
      };
    });
}

async function readTaxonomia(): Promise<TaxonomiaRow[]> {
  const rows = await readRange("TaxonomíaExtendida!A2:F");
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

async function readPresupuesto(): Promise<PresupuestoRow[]> {
  const rows = await readRange("Presupuesto!A2:F");
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

async function readTipoCambio(): Promise<TipoCambioRow[]> {
  const rows = await readRange("TipoCambio!A2:C");
  return rows
    .filter((r) => r && r[0])
    .map((r): TipoCambioRow => ({
      fecha: parseChileanDate(String(r[0])) ?? new Date(0),
      moneda: asMoneda(String(r[1])),
      valorCLP: parseNumber(r[2]),
    }));
}

async function readDeudasMaestro(): Promise<DeudaMaestro[]> {
  const rows = await readRange("Deudas_Maestro!A2:J");
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

async function readDeudasSnapshot(): Promise<DeudaSnapshot[]> {
  const rows = await readRange("Deudas_Snapshot!A2:F");
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

async function readInversionesMaestro(): Promise<InversionMaestro[]> {
  const rows = await readRange("Inversiones_Maestro!A2:J");
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

async function readInversionesSnapshot(): Promise<InversionSnapshot[]> {
  const rows = await readRange("Inversiones_Snapshot!A2:H");
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

async function readInversionesObjetivo(): Promise<InversionObjetivo[]> {
  const rows = await readRange("InversionesObjetivo!A2:C");
  return rows
    .filter((r) => r && r[0])
    .map((r): InversionObjetivo => ({
      claseDeActivo: String(r[0] || ""),
      porcentajeObjetivo: parseNumber(r[1]),
      toleranciaPP: parseNumber(r[2]) || 5,
    }));
}

async function readActivosIliquidos(): Promise<ActivoIliquido[]> {
  const rows = await readRange("ActivosIlíquidos!A2:F");
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

async function readPatrimonio(): Promise<PatrimonioRow[]> {
  const rows = await readRange("Patrimonio!A2:H");
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

async function readMetas(): Promise<MetaRow[]> {
  const rows = await readRange("Metas!A2:F");
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

async function readIngresosEsperados(): Promise<IngresoEsperado[]> {
  const rows = await readRange("IngresosEsperados!A2:E");
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

async function readEgresosEsperados(): Promise<EgresoEsperado[]> {
  const rows = await readRange("EgresosEsperados!A2:F");
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

  const taxonomia = await readTaxonomia();
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
    readMovimientos(taxonomia),
    readPresupuesto(),
    readTipoCambio(),
    readDeudasMaestro(),
    readDeudasSnapshot(),
    readInversionesMaestro(),
    readInversionesSnapshot(),
    readInversionesObjetivo(),
    readActivosIliquidos(),
    readPatrimonio(),
    readMetas(),
    readIngresosEsperados(),
    readEgresosEsperados(),
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
