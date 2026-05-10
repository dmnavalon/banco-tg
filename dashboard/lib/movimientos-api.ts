// Cliente HTTP para la API "Movimientos" del backend Python (Railway). Solo
// se invoca desde route handlers (server-side) — el token NUNCA se expone al
// browser. El navegador pega exclusivamente a `/api/movimientos/*` del propio
// dashboard, que actúa de proxy.

import type {
  AuditEvent,
  BulkResults,
  CategoriesResponse,
  MovementsFilters,
  Movimiento,
} from "./movimientos-types";

function backendUrl(): string {
  const url = process.env.BACKEND_API_URL;
  if (!url) {
    throw new Error("BACKEND_API_URL no configurado en env.local del dashboard");
  }
  return url.replace(/\/$/, "");
}

function backendToken(): string {
  const t = process.env.BACKEND_API_TOKEN;
  if (!t) {
    throw new Error("BACKEND_API_TOKEN no configurado en env.local del dashboard");
  }
  return t;
}

async function fetchBackend(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  headers.set("Authorization", `Bearer ${backendToken()}`);
  if (init.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const r = await fetch(`${backendUrl()}${path}`, {
    ...init,
    headers,
    // No cachear; los datos cambian en tiempo real con TG/dashboard.
    cache: "no-store",
  });
  return r;
}

function buildQuery(filters: MovementsFilters): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(filters)) {
    if (v === undefined || v === null || v === "") continue;
    p.set(k, String(v));
  }
  const q = p.toString();
  return q ? `?${q}` : "";
}

export async function listMovements(
  filters: MovementsFilters = {},
): Promise<{ items: Movimiento[]; count: number }> {
  const r = await fetchBackend(`/api/movements${buildQuery(filters)}`);
  if (!r.ok) throw new Error(`listMovements ${r.status}: ${await r.text()}`);
  return r.json();
}

export async function getMovement(id: string): Promise<{ movement: Movimiento }> {
  const r = await fetchBackend(`/api/movements/${encodeURIComponent(id)}`);
  if (!r.ok) throw new Error(`getMovement ${r.status}`);
  return r.json();
}

export async function getAudit(id: string): Promise<{ events: AuditEvent[] }> {
  const r = await fetchBackend(`/api/movements/${encodeURIComponent(id)}/audit`);
  if (!r.ok) throw new Error(`getAudit ${r.status}`);
  return r.json();
}

export async function getCategories(): Promise<CategoriesResponse> {
  const r = await fetchBackend(`/api/categories`);
  if (!r.ok) throw new Error(`getCategories ${r.status}`);
  return r.json();
}

interface MutationResult {
  status: number;
  body: unknown;
}

export async function postSingle(
  id: string,
  action: "approve" | "correct" | "approve-correction" | "ignore" | "reopen" | "sync",
  payload: Record<string, unknown>,
): Promise<MutationResult> {
  const r = await fetchBackend(
    `/api/movements/${encodeURIComponent(id)}/${action}`,
    { method: "POST", body: JSON.stringify(payload) },
  );
  let body: unknown;
  try {
    body = await r.json();
  } catch {
    body = { error: "non_json_response" };
  }
  return { status: r.status, body };
}

export async function postBulk(
  op: "approve" | "categorize" | "ignore" | "comment" | "reopen",
  payload: Record<string, unknown>,
): Promise<{ status: number; results?: BulkResults; error?: string }> {
  const r = await fetchBackend(`/api/movements/bulk/${op}`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
  if (!r.ok) {
    let msg: string;
    try {
      const j = await r.json();
      msg = JSON.stringify(j);
    } catch {
      msg = await r.text();
    }
    return { status: r.status, error: msg };
  }
  const data = await r.json();
  return { status: r.status, results: data.results };
}
