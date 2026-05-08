export type Moneda = "CLP" | "USD" | "UF";

export type TipoMovimiento =
  | "Ingreso"
  | "GastoReal"
  | "MovimientoInterno"
  | "PagoDeuda"
  | "Ahorro"
  | "AporteInversión"
  | "RetiroInversión"
  | "Devolución"
  | "Impuesto"
  | "GastoPorRendir";

export interface Movimiento {
  idx: number;
  fecha: Date;
  fechaISO: string;
  banco: string;
  persona: string;
  descripcion: string;
  monto: number;
  montoCLP: number;
  tipo: "Abono" | "Cargo" | "";
  saldo: number | null;
  categoria: string;
  subcategoria: string;
  moneda: Moneda;
  esencial: boolean;
  fijo: boolean;
  recurrente: boolean;
  extraordinario: boolean;
  excluido: boolean;
  notas: string;
  tipoMovimiento: TipoMovimiento;
}

export interface TaxonomiaRow {
  categoria: string;
  subcategoria: string;
  esencial: boolean;
  fijo: boolean;
  recurrentePorDefecto: boolean;
  tipoMovimiento: TipoMovimiento;
}

export interface PresupuestoRow {
  año: number;
  mes: number;
  categoria: string;
  subcategoria: string;
  montoCLP: number;
  notas: string;
}

export interface TipoCambioRow {
  fecha: Date;
  moneda: Moneda;
  valorCLP: number;
}

export interface DeudaMaestro {
  id: string;
  institucion: string;
  tipo: string;
  moneda: Moneda;
  saldoOriginal: number;
  tasaAnual: number;
  cuota: number;
  cuotasRestantes: number;
  proximoVencimiento: Date | null;
  activa: boolean;
}

export interface DeudaSnapshot {
  mes: string; // YYYY-MM
  id: string;
  saldoActual: number;
  saldoCLP: number;
  interesesPagadosMes: number;
  capitalPagadoMes: number;
}

export interface InversionMaestro {
  id: string;
  activo: string;
  clase: string;
  subclase: string;
  moneda: Moneda;
  pais: string;
  institucion: string;
  liquidez: "Alta" | "Media" | "Baja" | "";
  fechaInicio: Date | null;
  activa: boolean;
}

export interface InversionSnapshot {
  mes: string;
  id: string;
  aportesDelMes: number;
  retirosDelMes: number;
  valorMonedaOrig: number;
  tipoCambioCierre: number;
  valorCLP: number;
  notas: string;
}

export interface InversionObjetivo {
  claseDeActivo: string;
  porcentajeObjetivo: number;
  toleranciaPP: number;
}

export interface ActivoIliquido {
  id: string;
  tipo: string;
  descripcion: string;
  valorEstimadoCLP: number;
  fechaValuacion: Date | null;
  notas: string;
}

export interface PatrimonioRow {
  mes: string;
  cajaLiquida: number;
  activosInvertidos: number;
  activosIliquidos: number;
  activosTotales: number;
  pasivosTotales: number;
  patrimonioNeto: number;
  notas: string;
}

export interface MetaRow {
  tipo: string;
  descripcion: string;
  valorObjetivoCLP: number;
  fechaObjetivo: Date | null;
  valorActual: number;
  porcentajeAvance: number;
}

export interface IngresoEsperado {
  concepto: string;
  montoCLP: number;
  fechaEstimada: Date | null;
  frecuencia: string;
  confirmado: boolean;
}

export interface EgresoEsperado {
  concepto: string;
  montoCLP: number;
  fechaEstimada: Date | null;
  frecuencia: string;
  categoria: string;
  confirmado: boolean;
}

export interface DashboardData {
  movimientos: Movimiento[];
  taxonomia: TaxonomiaRow[];
  presupuesto: PresupuestoRow[];
  tipoCambio: TipoCambioRow[];
  deudasMaestro: DeudaMaestro[];
  deudasSnapshot: DeudaSnapshot[];
  inversionesMaestro: InversionMaestro[];
  inversionesSnapshot: InversionSnapshot[];
  inversionesObjetivo: InversionObjetivo[];
  activosIliquidos: ActivoIliquido[];
  patrimonio: PatrimonioRow[];
  metas: MetaRow[];
  ingresosEsperados: IngresoEsperado[];
  egresosEsperados: EgresoEsperado[];
  fetchedAt: string;
  warnings: string[];
}

export type Confianza = "Real" | "Estimado" | "Incompleto" | "NoHayData";
export type Estado = "Sano" | "Atención" | "Crítico" | "Sin data";

export interface PasoCalculo {
  etiqueta: string;
  valor: number | null;
  formato?: "CLP" | "PCT" | "MESES" | "NUM" | "RATIO";
  // Si este paso a su vez está hecho de movimientos, sus idxs:
  breakdownIdxs?: number[];
  fuenteDatos?: string;
}

export interface Kpi {
  nombre: string;
  valor: number | null;
  formato: "CLP" | "PCT" | "MESES" | "NUM" | "RATIO";
  confianza: Confianza;
  estado: Estado;
  comparaciones?: {
    mesAnterior?: number | null;
    avg3m?: number | null;
    avg6m?: number | null;
    avg12m?: number | null;
    mismoMesAnioAnterior?: number | null;
  };
  meta?: number | null;
  presupuesto?: number | null;
  recomendacion?: string;
  formula?: string;
  /** Idxs de los movimientos que componen este KPI (si aplica). */
  breakdownIdxs?: number[];
  /** Para KPIs derivados, los pasos del cálculo. */
  pasosCalculo?: PasoCalculo[];
  /** Si el KPI viene de otra pestaña (Patrimonio, Deudas, etc.). */
  fuenteDatos?: string;
}

export interface SerieMensual {
  mes: string; // YYYY-MM
  valor: number;
  label?: string;
}

export interface CategoriaGasto {
  categoria: string;
  subcategoria?: string;
  montoCLP: number;
  cantidad: number;
  esencial: boolean;
  fijo: boolean;
}

export interface DesviacionCategoria {
  categoria: string;
  actual: number;
  promedioHistorico: number;
  diferenciaAbsoluta: number;
  diferenciaPorcentual: number;
  explicacion: string;
}

export interface Alerta {
  id: string;
  severidad: "alta" | "media" | "baja";
  categoria: "gastos" | "flujo" | "deuda" | "inversiones" | "datos";
  titulo: string;
  evidencia: string;
  accion: string;
  /** Id de la sección a la que llevar al usuario al clickear la alerta. */
  seccionDestino?: string;
}

export interface DashboardKPIs {
  resumen: {
    ingresosNetos: Kpi;
    gastosTotales: Kpi;
    flujoLibre: Kpi;
    tasaAhorro: Kpi;
    patrimonioNeto: Kpi;
    variacionPatrimonio: Kpi;
    mesesFondoEmergencia: Kpi;
    endeudamientoMensual: Kpi;
    gastoEsencialPct: Kpi;
    gastoDiscrecionalPct: Kpi;
    gastoFijoPct: Kpi;
    gastoVariablePct: Kpi;
    estadoGeneral: Estado;
  };
  evolucion: {
    ingresos: SerieMensual[];
    gastos: SerieMensual[];
    flujoLibre: SerieMensual[];
    patrimonio: SerieMensual[];
  };
  gastos: {
    porCategoria: CategoriaGasto[];
    porSubcategoria: CategoriaGasto[];
    porComercio: CategoriaGasto[];
    porPersona: CategoriaGasto[];
    desviaciones: DesviacionCategoria[];
    extraordinarios: Movimiento[];
    recurrentes: Movimiento[];
    posiblesDuplicados: Movimiento[][];
    sinClasificar: Movimiento[];
  };
  calidadDatos: {
    totalMovimientos: number;
    clasificados: number;
    sinCategoria: number;
    duplicados: number;
    excluidos: number;
    pctCompletos: number;
    issues: { tipo: string; count: number; movimientos: Movimiento[] }[];
  };
  alertas: Alerta[];
  mesActual: string;
  mesesConData: string[];
}
