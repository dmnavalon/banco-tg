// Tipos compartidos entre route handlers, lib client y componentes para la
// feature "Movimientos". El backend Python (src/api/serializers.py) define la
// shape canónica — estos tipos espejan eso exactamente.

export type ReviewStatus =
  | "pending"
  | "approved"
  | "corrected_pending"
  | "corrected_approved"
  | "ignored"
  | "error";

export type SheetSyncStatus =
  | "not_ready"
  | "pending_sync"
  | "synced"
  | "sync_error";

export type ActionSource = "telegram" | "dashboard" | "system";

export interface Movimiento {
  id: string;
  date: string;                // YYYY-MM-DD
  description: string;
  amount: number;
  movement_type: string | null;
  account: string | null;
  bank: string;
  persona: string | null;

  suggested_category: string | null;
  suggested_subcategory: string | null;
  final_category: string | null;
  final_subcategory: string | null;
  comercio: string | null;
  comercio_final: string | null;

  confidence: number | null;
  classifier_source: string | null;
  tipo: string | null;
  requiere_revision: boolean | null;
  pregunta_sugerida: string | null;

  review_status: ReviewStatus;
  sheet_sync_status: SheetSyncStatus;
  version: number;
  status: string | null;       // legacy

  comment: string | null;
  ignore_reason: string | null;

  decided_by: string | null;
  decided_at: string | null;
  corrected_by: string | null;
  corrected_at: string | null;

  last_action_source: ActionSource;
  sheet_row_id: number | null;
  sync_error_message: string | null;

  cuotas_actual: number | null;
  cuotas_total: number | null;
  cuota_monto: number | null;
  saldo: number | null;

  tg_photo_file_id: string | null;
  notified_at: string | null;
  inserted_at: string | null;
  updated_at: string | null;
}

export interface AuditEvent {
  id: string;
  movement_id: string;
  action: string;
  prev_review_status: ReviewStatus | null;
  new_review_status: ReviewStatus | null;
  prev_sheet_sync_status: SheetSyncStatus | null;
  new_sheet_sync_status: SheetSyncStatus | null;
  actor: string;
  source: ActionSource;
  details: Record<string, unknown>;
  created_at: string;
}

export interface BulkResultItem {
  status: "ok" | "conflict" | "error";
  movement?: Movimiento;
  current_movement?: Movimiento;
  error?: string;
  kind?: string;
}

export type BulkResults = Record<string, BulkResultItem>;

export interface CategoriesResponse {
  taxonomy: Record<string, string[]>;
  income_categories: string[];
  extensible_categories: string[];
}

export interface CreateCategoryResponse {
  created: boolean;
  cat: string;
  sub: string;
  taxonomy: Record<string, string[]>;
  similar: { cats: string[]; subs: string[] };
}

// Filtros que el dashboard pasa al backend. Mapean 1:1 a query params.
export interface MovementsFilters {
  status?: string;             // "pending" | "pending,corrected_pending" | "all"
  from?: string;               // YYYY-MM-DD
  to?: string;
  bank?: string;
  persona?: string;
  categoria?: string;
  subcategoria?: string;
  min_amount?: number;
  max_amount?: number;
  confidence_min?: number;
  q?: string;                  // búsqueda en descripción
  comercio?: string;
  limit?: number;
}

export const TAB_TO_STATUS_FILTER: Record<string, string> = {
  pendientes: "pending,corrected_pending",
  corregidos: "corrected_pending",
  aprobados: "approved,corrected_approved",
  ignorados: "ignored",
  todos: "all",
};
