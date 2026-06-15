"use client";

import {
  Check,
  ChevronDown,
  ChevronsUpDown,
  ListTree,
  RefreshCw,
  RotateCcw,
  SlidersHorizontal,
  X,
} from "lucide-react";
import {
  type MouseEvent,
  type ReactNode,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import { AddCategoryModal } from "@/components/movimientos/AddCategoryModal";
import {
  CategoryComboboxContent,
  CategoryComboboxPopover,
} from "@/components/movimientos/CategoryComboboxPopover";
import type {
  AuditEvent,
  CategoriesResponse,
  ManualFields,
  Movimiento,
  ReviewStatus,
} from "@/lib/movimientos-types";

type TabKey = "pendientes" | "corregidos" | "aprobados" | "ignorados" | "todos";

const TAB_TO_FILTER: Record<TabKey, string> = {
  pendientes: "pending,corrected_pending",
  corregidos: "corrected_pending",
  aprobados: "approved,corrected_approved",
  ignorados: "ignored",
  todos: "all",
};

const TAB_LABELS: Record<TabKey, string> = {
  pendientes: "Pendientes",
  corregidos: "Corregidos sin aprobar",
  aprobados: "Aprobados",
  ignorados: "Ignorados",
  todos: "Todos",
};

const REFRESH_MS = 30_000;

interface Filters {
  q: string;
  bank: string;
  from: string;
  to: string;
  min_amount: string;
  max_amount: string;
  confidence_min: string;
  comercio: string;
  persona: string;
  categoria: string;
  tipoMovimiento: string;
  excluido: string;
}

// Clasificación contable derivada por el backend (campo tipo_movimiento). Es la
// misma que usan los KPIs, por eso filtrar por acá cuadra con las tarjetas.
const TIPO_MOVIMIENTO_OPTS = [
  "Ingreso", "GastoReal", "GastoPorRendir", "Devolución", "PagoDeuda",
  "Ahorro", "AporteInversión", "RetiroInversión", "MovimientoInterno", "Impuesto",
] as const;

const EMPTY_FILTERS: Filters = {
  q: "",
  bank: "",
  from: "",
  to: "",
  min_amount: "",
  max_amount: "",
  confidence_min: "",
  comercio: "",
  persona: "",
  categoria: "",
  tipoMovimiento: "",
  excluido: "",
};

type SortKey = "estado" | "date" | "amount" | "confidence";
interface SortState {
  key: SortKey;
  dir: 1 | -1;
}

function formatCLP(amount: number | null): string {
  if (amount === null || Number.isNaN(amount)) return "—";
  return new Intl.NumberFormat("es-CL", { style: "currency", currency: "CLP", maximumFractionDigits: 0 })
    .format(Math.round(amount));
}

function formatDate(iso: string | null): string {
  if (!iso) return "";
  const m = iso.match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (!m) return iso;
  return `${m[3]}/${m[2]}/${m[1].slice(2)}`;
}

function reviewBadge(status: ReviewStatus | null): { label: string; cls: string } {
  switch (status) {
    case "pending":
      return { label: "Pendiente", cls: "bg-amber-100 text-amber-800" };
    case "corrected_pending":
      return { label: "Corregido", cls: "bg-violet-100 text-violet-800" };
    case "approved":
      return { label: "Aprobado", cls: "bg-emerald-100 text-emerald-800" };
    case "corrected_approved":
      return { label: "Corr. aprobado", cls: "bg-emerald-100 text-emerald-900" };
    case "ignored":
      return { label: "Ignorado", cls: "bg-slate-200 text-slate-700" };
    case "error":
      return { label: "Error", cls: "bg-red-100 text-red-800" };
    default:
      return { label: status ?? "?", cls: "bg-slate-100 text-slate-600" };
  }
}

function syncBadge(status: string | null): { label: string; cls: string } | null {
  switch (status) {
    case "synced":
      return { label: "✓ Sheet", cls: "bg-emerald-50 text-emerald-700" };
    case "pending_sync":
      return { label: "⏳ sync", cls: "bg-amber-50 text-amber-700" };
    case "sync_error":
      return { label: "⚠ sync", cls: "bg-red-50 text-red-700" };
    case "not_ready":
      return null;
    default:
      return null;
  }
}

function confidenceBadge(c: number | null): { label: string; cls: string } | null {
  if (c === null) return null;
  const pct = Math.round(c * 100);
  if (pct >= 90) return { label: `🟢 ${pct}%`, cls: "text-emerald-700" };
  if (pct >= 75) return { label: `🟡 ${pct}%`, cls: "text-amber-700" };
  if (pct >= 50) return { label: `🟠 ${pct}%`, cls: "text-orange-700" };
  return { label: `🔴 ${pct}%`, cls: "text-red-700" };
}

/** Badges compactos de campos manuales activos (para la columna "Campos"). */
function manualBadges(m: Movimiento): { label: string; cls: string; title: string }[] {
  const out: { label: string; cls: string; title: string }[] = [];
  if (m.excluido === true) out.push({ label: "EXCL", cls: "bg-red-100 text-red-700", title: "Excluido de KPIs" });
  if (m.recurrente === true) out.push({ label: "REC", cls: "bg-sky-100 text-sky-700", title: "Recurrente" });
  if (m.extraordinario === true) out.push({ label: "EXT", cls: "bg-orange-100 text-orange-700", title: "Extraordinario" });
  if (m.esencial === true) out.push({ label: "ES", cls: "bg-emerald-100 text-emerald-700", title: "Esencial (override manual)" });
  if (m.fijo === true) out.push({ label: "FI", cls: "bg-violet-100 text-violet-700", title: "Fijo (override manual)" });
  if (m.notas) out.push({ label: "📝", cls: "bg-slate-100 text-slate-700", title: `Notas: ${m.notas}` });
  return out;
}

export function MovimientosTable() {
  const [tab, setTab] = useState<TabKey>("pendientes");
  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS);
  const [items, setItems] = useState<Movimiento[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [categories, setCategories] = useState<CategoriesResponse | null>(null);
  const [comboTarget, setComboTarget] = useState<{ id: string; rect: DOMRect } | null>(null);
  const [flagsTarget, setFlagsTarget] = useState<{ id: string; rect: DOMRect } | null>(null);
  const [pendingMutations, setPendingMutations] = useState<Set<string>>(new Set());
  const [ignoreTarget, setIgnoreTarget] = useState<{ ids: string[]; bulk: boolean } | null>(null);
  const [auditTarget, setAuditTarget] = useState<string | null>(null);
  const [bulkCategorize, setBulkCategorize] = useState(false);
  const [addCatModal, setAddCatModal] = useState<{
    movId: string | null;
    suggestedCat: string;
    suggestedSub: string;
  } | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [sort, setSort] = useState<SortState | null>(null);
  // Status explícito (coma-lista) que llega por URL desde las tarjetas del
  // dashboard. Sobrescribe el status derivado del tab para que las filas
  // mostradas cuadren EXACTO con el KPI (que excluye ignorados). null = usar tab.
  const [statusOverride, setStatusOverride] = useState<string | null>(null);

  // Orden client-side sobre el set ya cargado. Click en header: 1º asc, 2º desc.
  const toggleSort = (key: SortKey) => {
    setSort((prev) =>
      prev?.key === key ? { key, dir: prev.dir === 1 ? -1 : 1 } : { key, dir: 1 },
    );
  };

  const reloadCategories = useCallback(() => {
    fetch("/api/categorias")
      .then((r) => (r.ok ? r.json() : null))
      .then((j) => {
        if (j) setCategories(j);
      })
      .catch(() => {});
  }, []);

  // Prefiltrado vía URL (?tab=todos&tipo=Ingreso&from=2026-06-01&to=2026-06-30…),
  // usado por las tarjetas del dashboard que abren esta vista en otra pestaña.
  useEffect(() => {
    const sp = new URLSearchParams(window.location.search);
    const t = sp.get("tab");
    if (t && t in TAB_LABELS) setTab(t as TabKey);
    const status = sp.get("status");
    if (status) setStatusOverride(status);
    const init: Partial<Filters> = {};
    for (const k of Object.keys(EMPTY_FILTERS) as (keyof Filters)[]) {
      const v = sp.get(k);
      if (v) init[k] = v;
    }
    if (Object.keys(init).length > 0) setFilters((f) => ({ ...f, ...init }));
  }, []);

  // Carga taxonomía una vez al montar.
  useEffect(() => {
    fetch("/api/categorias")
      .then((r) => (r.ok ? r.json() : null))
      .then((j) => setCategories(j))
      .catch(() => setCategories(null));
  }, []);

  const buildQuery = useCallback(() => {
    const sp = new URLSearchParams();
    sp.set("status", statusOverride ?? TAB_TO_FILTER[tab]);
    sp.set("limit", "200");
    for (const [k, v] of Object.entries(filters)) {
      if (!v) continue;
      // El backend espera snake_case para el campo calculado.
      sp.set(k === "tipoMovimiento" ? "tipo_movimiento" : k, v);
    }
    return sp.toString();
  }, [tab, filters, statusOverride]);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await fetch(`/api/movimientos?${buildQuery()}`, { cache: "no-store" });
      if (!r.ok) {
        let msg = `HTTP ${r.status}`;
        try {
          const body = await r.json();
          if (body?.message) msg = `${msg} — ${body.message}`;
        } catch {
          // body no-JSON: nos quedamos con el status.
        }
        throw new Error(msg);
      }
      const j = await r.json();
      setItems(j.items as Movimiento[]);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [buildQuery]);

  useEffect(() => {
    refresh();
    setSelected(new Set());
  }, [refresh]);

  // Auto-refresh cada 30s. Lo pausamos si hay popover/modal abierto, para no
  // perder el contexto del usuario.
  const comboRef = useRef(comboTarget);
  comboRef.current = comboTarget;
  const flagsRef = useRef(flagsTarget);
  flagsRef.current = flagsTarget;
  useEffect(() => {
    if (!autoRefresh) return;
    const id = setInterval(() => {
      if (
        comboRef.current === null &&
        flagsRef.current === null &&
        ignoreTarget === null &&
        auditTarget === null &&
        !bulkCategorize &&
        addCatModal === null
      ) {
        refresh();
      }
    }, REFRESH_MS);
    return () => clearInterval(id);
  }, [autoRefresh, refresh, ignoreTarget, auditTarget, bulkCategorize, addCatModal]);

  const toggleSelected = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  // tipoMovimiento y excluido se filtran server-side (backend), así que `items`
  // ya viene filtrado.
  const visible = items;

  const sortedVisible = useMemo(() => {
    if (!sort) return visible;
    const { key, dir } = sort;
    const val = (m: Movimiento): string | number =>
      key === "estado"
        ? m.review_status
        : key === "date"
          ? m.date ?? ""
          : key === "amount"
            ? m.amount ?? 0
            : m.confidence ?? -1;
    return [...visible].sort((a, b) => {
      const va = val(a);
      const vb = val(b);
      if (typeof va === "number" && typeof vb === "number") return (va - vb) * dir;
      return String(va).localeCompare(String(vb)) * dir;
    });
  }, [visible, sort]);

  const toggleSelectAll = () => {
    if (selected.size === visible.length) {
      setSelected(new Set());
    } else {
      setSelected(new Set(visible.map((m) => m.id)));
    }
  };

  const showToast = (msg: string) => {
    setToast(msg);
    window.setTimeout(() => setToast(null), 3500);
  };

  const markPending = (id: string, on: boolean) => {
    setPendingMutations((prev) => {
      const next = new Set(prev);
      if (on) next.add(id);
      else next.delete(id);
      return next;
    });
  };

  // ── Mutations ─────────────────────────────────────────────────────────

  const callSingle = async (
    id: string,
    action: "approve" | "approve-correction" | "correct" | "ignore" | "reopen" | "sync" | "flags",
    payload: Record<string, unknown>,
  ): Promise<{ ok: boolean; mov?: Movimiento; err?: string; conflict?: Movimiento }> => {
    markPending(id, true);
    try {
      const r = await fetch(`/api/movimientos/${encodeURIComponent(id)}/${action}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const j = await r.json().catch(() => ({}));
      if (r.status === 409) {
        return { ok: false, err: "version_conflict", conflict: j.current_movement };
      }
      if (!r.ok) return { ok: false, err: j.message ?? `HTTP ${r.status}` };
      return { ok: true, mov: j.movement };
    } catch (e) {
      return { ok: false, err: e instanceof Error ? e.message : String(e) };
    } finally {
      markPending(id, false);
    }
  };

  const onApprove = async (mov: Movimiento) => {
    const action = mov.review_status === "corrected_pending" ? "approve-correction" : "approve";
    const res = await callSingle(mov.id, action, { version: mov.version });
    if (res.ok && res.mov) {
      replaceItem(res.mov);
      showToast(`Aprobado: ${res.mov.final_category ?? ""}`);
    } else if (res.err === "version_conflict") {
      showToast("Conflicto: actualiza la tabla antes de guardar.");
      refresh();
    } else {
      showToast(`No pude aprobar: ${res.err}`);
    }
  };

  const onSaveInlineCategory = async (mov: Movimiento, cat: string, sub: string | null) => {
    const res = await callSingle(mov.id, "correct", {
      version: mov.version,
      final_category: cat,
      final_subcategory: sub,
    });
    if (res.ok && res.mov) {
      replaceItem(res.mov);
      setComboTarget(null);
      showToast("Categoría actualizada (corrected_pending)");
    } else if (res.err === "version_conflict") {
      showToast("Conflicto: actualiza la tabla antes de guardar.");
      refresh();
    } else {
      showToast(`Error al guardar: ${res.err}`);
    }
  };

  const onSaveFlags = async (mov: Movimiento, fields: ManualFields) => {
    if (Object.keys(fields).length === 0) {
      setFlagsTarget(null);
      return;
    }
    const res = await callSingle(mov.id, "flags", {
      fields,
      version: mov.version,
      actor: "dashboard",
    });
    if (res.ok && res.mov) {
      replaceItem(res.mov);
      setFlagsTarget(null);
      showToast("Campos manuales guardados.");
    } else if (res.err === "version_conflict") {
      setFlagsTarget(null);
      showToast("Conflicto: actualiza la tabla antes de guardar.");
      refresh();
    } else {
      showToast(`Error al guardar campos: ${res.err}`);
    }
  };

  const onIgnoreConfirm = async (reason: string) => {
    if (!ignoreTarget) return;
    const ids = ignoreTarget.ids;
    if (ignoreTarget.bulk) {
      const versions = Object.fromEntries(
        items.filter((m) => ids.includes(m.id)).map((m) => [m.id, m.version]),
      );
      const r = await fetch(`/api/movimientos/bulk/ignore`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ids, versions, reason, actor: "diego" }),
      });
      const j = await r.json().catch(() => ({}));
      if (r.ok) {
        const okCount = Object.values(j.results ?? {}).filter((x) => (x as { status: string }).status === "ok").length;
        showToast(`Ignorados: ${okCount}/${ids.length}`);
      } else {
        showToast(`Error bulk ignore: ${j.message ?? r.status}`);
      }
      setSelected(new Set());
      setIgnoreTarget(null);
      refresh();
      return;
    }
    const id = ids[0];
    const mov = items.find((m) => m.id === id);
    const res = await callSingle(id, "ignore", { version: mov?.version, reason });
    setIgnoreTarget(null);
    if (res.ok && res.mov) {
      replaceItem(res.mov);
      showToast("Ignorado.");
    } else {
      showToast(`Error: ${res.err}`);
    }
  };

  const onReopen = async (mov: Movimiento) => {
    const res = await callSingle(mov.id, "reopen", { version: mov.version });
    if (res.ok && res.mov) {
      replaceItem(res.mov);
      showToast("Reabierto.");
    } else if (res.err === "version_conflict") {
      showToast("Conflicto: actualiza la tabla antes de guardar.");
      refresh();
    } else {
      showToast(`Error: ${res.err}`);
    }
  };

  const onRetrySync = async (mov: Movimiento) => {
    const res = await callSingle(mov.id, "sync", {});
    if (res.ok && res.mov) {
      replaceItem(res.mov);
      showToast(res.mov.sheet_sync_status === "synced" ? "Sync OK." : `Sync: ${res.mov.sheet_sync_status}`);
    } else {
      showToast(`Sync falló: ${res.err}`);
    }
  };

  const onBulkApprove = async () => {
    const ids = Array.from(selected);
    if (!ids.length) return;
    const versions = Object.fromEntries(
      items.filter((m) => ids.includes(m.id)).map((m) => [m.id, m.version]),
    );
    const r = await fetch(`/api/movimientos/bulk/approve`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ids, versions, actor: "diego" }),
    });
    const j = await r.json().catch(() => ({}));
    if (r.ok) {
      const statuses = Object.values(j.results ?? {}).map((x) => (x as { status: string }).status);
      const okCount = statuses.filter((s) => s === "ok").length;
      const conflictCount = statuses.filter((s) => s === "conflict").length;
      const errorCount = statuses.filter((s) => s === "error").length;
      showToast(
        `Aprobados ${okCount}/${ids.length}` +
          (conflictCount ? ` · ${conflictCount} conflictos` : "") +
          (errorCount ? ` · ${errorCount} con error` : ""),
      );
    } else {
      showToast(`Error bulk approve: ${j.message ?? r.status}`);
    }
    setSelected(new Set());
    refresh();
  };

  const onBulkCategorize = async (cat: string, sub: string | null) => {
    const ids = Array.from(selected);
    if (!ids.length || !cat) return;
    const versions = Object.fromEntries(
      items.filter((m) => ids.includes(m.id)).map((m) => [m.id, m.version]),
    );
    const r = await fetch(`/api/movimientos/bulk/categorize`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ids, versions, final_category: cat, final_subcategory: sub, actor: "diego" }),
    });
    const j = await r.json().catch(() => ({}));
    if (r.ok) {
      const okCount = Object.values(j.results ?? {}).filter((x) => (x as { status: string }).status === "ok").length;
      showToast(`Categorizados ${okCount}/${ids.length}`);
    } else {
      showToast(`Error bulk categorize: ${j.message ?? r.status}`);
    }
    setSelected(new Set());
    setBulkCategorize(false);
    refresh();
  };

  const onBulkReopen = async () => {
    const ids = Array.from(selected);
    if (!ids.length) return;
    const versions = Object.fromEntries(
      items.filter((m) => ids.includes(m.id)).map((m) => [m.id, m.version]),
    );
    const r = await fetch(`/api/movimientos/bulk/reopen`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ids, versions, actor: "diego" }),
    });
    if (r.ok) {
      showToast(`Reabiertos ${ids.length}`);
    }
    setSelected(new Set());
    refresh();
  };

  const replaceItem = (mov: Movimiento) => {
    setItems((prev) => prev.map((m) => (m.id === mov.id ? mov : m)));
  };

  // ── Render ────────────────────────────────────────────────────────────

  return (
    <div className="space-y-4">
      {/* Tabs */}
      <div className="flex flex-wrap gap-1 border-b border-slate-200">
        {(Object.keys(TAB_LABELS) as TabKey[]).map((k) => (
          <button
            key={k}
            onClick={() => { setTab(k); setStatusOverride(null); }}
            className={`px-4 py-2 text-sm font-medium border-b-2 -mb-px transition ${
              tab === k
                ? "border-blue-600 text-blue-700"
                : "border-transparent text-slate-500 hover:text-slate-700"
            }`}
          >
            {TAB_LABELS[k]}
          </button>
        ))}
        <div className="ml-auto flex items-center gap-2 px-2">
          <label className="flex items-center gap-1 text-xs text-slate-600">
            <input
              type="checkbox"
              checked={autoRefresh}
              onChange={(e) => setAutoRefresh(e.target.checked)}
              className="h-3 w-3"
            />
            Auto-refresh 30s
          </label>
          <button
            onClick={refresh}
            className="rounded border border-slate-300 bg-white px-3 py-1 text-xs hover:bg-slate-50"
            disabled={loading}
          >
            {loading ? "Cargando…" : "Refrescar"}
          </button>
        </div>
      </div>

      {/* Filtros */}
      <details className="rounded border border-slate-200 bg-white">
        <summary className="cursor-pointer px-3 py-2 text-sm font-medium text-slate-700">Filtros</summary>
        <div className="grid grid-cols-2 gap-2 border-t border-slate-100 p-3 md:grid-cols-4">
          <input
            placeholder="Buscar en descripción…"
            value={filters.q}
            onChange={(e) => setFilters({ ...filters, q: e.target.value })}
            className="rounded border border-slate-300 px-2 py-1 text-sm"
          />
          <input
            placeholder="Comercio"
            value={filters.comercio}
            onChange={(e) => setFilters({ ...filters, comercio: e.target.value })}
            className="rounded border border-slate-300 px-2 py-1 text-sm"
          />
          <select
            value={filters.bank}
            onChange={(e) => setFilters({ ...filters, bank: e.target.value })}
            className="rounded border border-slate-300 px-2 py-1 text-sm"
          >
            <option value="">Banco (todos)</option>
            <option value="falabella">Falabella</option>
            <option value="bancochile">BancoChile</option>
          </select>
          <select
            value={filters.persona}
            onChange={(e) => setFilters({ ...filters, persona: e.target.value })}
            className="rounded border border-slate-300 px-2 py-1 text-sm"
          >
            <option value="">Persona (todas)</option>
            <option value="Titular">Titular</option>
            <option value="Adicional">Adicional</option>
          </select>
          <input
            type="date"
            value={filters.from}
            onChange={(e) => setFilters({ ...filters, from: e.target.value })}
            className="rounded border border-slate-300 px-2 py-1 text-sm"
          />
          <input
            type="date"
            value={filters.to}
            onChange={(e) => setFilters({ ...filters, to: e.target.value })}
            className="rounded border border-slate-300 px-2 py-1 text-sm"
          />
          <input
            placeholder="Monto mín"
            type="number"
            value={filters.min_amount}
            onChange={(e) => setFilters({ ...filters, min_amount: e.target.value })}
            className="rounded border border-slate-300 px-2 py-1 text-sm"
          />
          <input
            placeholder="Monto máx"
            type="number"
            value={filters.max_amount}
            onChange={(e) => setFilters({ ...filters, max_amount: e.target.value })}
            className="rounded border border-slate-300 px-2 py-1 text-sm"
          />
          <select
            value={filters.tipoMovimiento}
            onChange={(e) => setFilters({ ...filters, tipoMovimiento: e.target.value })}
            className="rounded border border-slate-300 px-2 py-1 text-sm"
          >
            <option value="">Tipo mov. (todos)</option>
            {TIPO_MOVIMIENTO_OPTS.map((t) => (
              <option key={t} value={t}>{t}</option>
            ))}
          </select>
          <select
            value={filters.categoria}
            onChange={(e) => setFilters({ ...filters, categoria: e.target.value })}
            className="rounded border border-slate-300 px-2 py-1 text-sm"
          >
            <option value="">Categoría (todas)</option>
            {categories &&
              Object.keys(categories.taxonomy).map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
          </select>
          <input
            placeholder="Confianza mín (0-1)"
            type="number"
            step="0.05"
            min="0"
            max="1"
            value={filters.confidence_min}
            onChange={(e) => setFilters({ ...filters, confidence_min: e.target.value })}
            className="rounded border border-slate-300 px-2 py-1 text-sm"
          />
          <button
            onClick={() => setFilters(EMPTY_FILTERS)}
            className="rounded border border-slate-300 bg-white px-3 py-1 text-sm hover:bg-slate-50"
          >
            Limpiar
          </button>
        </div>
      </details>

      {/* Bulk actions bar */}
      {selected.size > 0 && (
        <div className="flex flex-wrap items-center gap-2 rounded border border-blue-200 bg-blue-50 px-3 py-2 text-sm text-blue-900">
          <span>{selected.size} seleccionado{selected.size === 1 ? "" : "s"}</span>
          <div className="ml-2 flex flex-wrap gap-1">
            <button
              onClick={onBulkApprove}
              className="rounded bg-emerald-600 px-3 py-1 text-white hover:bg-emerald-700"
            >
              Aprobar
            </button>
            <button
              onClick={() => setBulkCategorize(true)}
              className="rounded bg-violet-600 px-3 py-1 text-white hover:bg-violet-700"
            >
              Cambiar categoría
            </button>
            <button
              onClick={() => setIgnoreTarget({ ids: Array.from(selected), bulk: true })}
              className="rounded bg-slate-600 px-3 py-1 text-white hover:bg-slate-700"
            >
              Ignorar
            </button>
            <button
              onClick={onBulkReopen}
              className="rounded border border-slate-300 bg-white px-3 py-1 hover:bg-slate-50"
            >
              Reabrir
            </button>
            <button
              onClick={() => setSelected(new Set())}
              className="rounded border border-slate-300 bg-white px-3 py-1 hover:bg-slate-50"
            >
              Limpiar selección
            </button>
          </div>
        </div>
      )}

      {error && (
        <div className="rounded border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
          Error cargando datos: {error}
        </div>
      )}

      {/* Tabla */}
      <div className="relative max-h-[calc(100vh-300px)] min-h-[300px] overflow-auto rounded border border-slate-200 bg-white">
        <table className="w-full min-w-[900px] text-xs">
          <thead className="sticky top-0 z-10 bg-slate-50 text-left text-[11px] uppercase tracking-wide text-slate-500 shadow-[0_1px_0_0_rgb(226_232_240)]">
            <tr>
              <th className="w-8 px-2 py-2">
                <input
                  type="checkbox"
                  checked={visible.length > 0 && selected.size === visible.length}
                  onChange={toggleSelectAll}
                />
              </th>
              <SortTh label="Estado" sortKey="estado" sort={sort} onSort={toggleSort} className="min-w-[104px]" />
              <SortTh label="Fecha" sortKey="date" sort={sort} onSort={toggleSort} className="min-w-[64px]" />
              <th className="min-w-[260px] px-2 py-2">Movimiento</th>
              <SortTh label="Monto" sortKey="amount" sort={sort} onSort={toggleSort} className="min-w-[96px]" align="right" />
              <th className="min-w-[170px] px-2 py-2">Categoría</th>
              <th className="min-w-[150px] px-2 py-2 text-right">Acciones</th>
            </tr>
          </thead>
          <tbody>
            {visible.length === 0 && !loading && (
              <tr>
                <td colSpan={7} className="px-2 py-8 text-center text-slate-500">
                  Sin movimientos en esta vista.
                </td>
              </tr>
            )}
            {sortedVisible.map((m) => {
              const r = reviewBadge(m.review_status);
              const s = syncBadge(m.sheet_sync_status);
              const c = confidenceBadge(m.confidence);
              const isSelected = selected.has(m.id);
              const isPending = pendingMutations.has(m.id);
              const cat = m.final_category ?? m.suggested_category ?? "";
              const sub = m.final_subcategory ?? m.suggested_subcategory ?? "";
              const comercio = m.comercio_final ?? m.comercio ?? "";
              const ignored = m.review_status === "ignored";
              const approved = m.review_status === "approved" || m.review_status === "corrected_approved";
              const badges = manualBadges(m);
              // Movimiento en dos líneas: comercio — descripción arriba, metadata
              // abajo. El tooltip de cada línea entrega el texto completo, así la
              // tabla se mantiene angosta sin perder información.
              const line1 = comercio ? `${comercio} — ${m.description}` : m.description;
              const metaShort = [
                c?.label,
                m.bank,
                m.persona,
                m.last_action_source,
                m.updated_at?.slice(0, 16),
                s?.label,
              ]
                .filter(Boolean)
                .join(" · ");
              const metaFull = [
                metaShort,
                m.tipo_movimiento ? `Tipo: ${m.tipo_movimiento}` : "",
                m.correction_hint ? `IA: «${m.correction_hint}»` : "",
                m.comment ? `Comentario: ${m.comment}` : "",
                m.ignore_reason ? `Ignorado: ${m.ignore_reason}` : "",
              ]
                .filter(Boolean)
                .join(" · ");
              return (
                <tr
                  key={m.id}
                  className={`border-b border-slate-100 ${isSelected ? "bg-blue-50/40" : "hover:bg-slate-50"}`}
                >
                  <td className="px-2 py-1.5 align-top">
                    <input
                      type="checkbox"
                      checked={isSelected}
                      onChange={() => toggleSelected(m.id)}
                    />
                  </td>
                  <td className="px-2 py-1.5 align-top">
                    <div className="flex flex-col gap-0.5">
                      <span className={`inline-block w-fit rounded px-1.5 py-0.5 text-[10px] font-medium ${r.cls}`}>
                        {r.label}
                      </span>
                      {s && (
                        <span className={`inline-block w-fit rounded px-1.5 py-0.5 text-[10px] ${s.cls}`}>
                          {s.label}
                        </span>
                      )}
                    </div>
                  </td>
                  <td className="px-2 py-1.5 align-top whitespace-nowrap text-slate-700">{formatDate(m.date)}</td>
                  <td className="max-w-[360px] px-2 py-1.5 align-top">
                    <Tip text={m.description} className="block max-w-full truncate font-medium text-slate-900">
                      {line1}
                    </Tip>
                    <Tip text={metaFull} className="mt-0.5 block max-w-full truncate text-[11px] capitalize text-slate-400">
                      {metaShort}
                    </Tip>
                    {badges.length > 0 && (
                      <div className="mt-1 flex flex-wrap gap-0.5">
                        {badges.map((b) => (
                          <span
                            key={b.label}
                            title={b.title}
                            className={`rounded px-1 py-0.5 text-[9px] font-medium ${b.cls}`}
                          >
                            {b.label}
                          </span>
                        ))}
                      </div>
                    )}
                  </td>
                  <td className="px-2 py-1.5 align-top text-right whitespace-nowrap font-mono">
                    {formatCLP(m.amount)}
                  </td>
                  <td className="px-2 py-1.5 align-top">
                    <button
                      onClick={(e) =>
                        setComboTarget({ id: m.id, rect: e.currentTarget.getBoundingClientRect() })
                      }
                      className={`group inline-flex max-w-[160px] items-center gap-1 rounded-md border px-2 py-1 text-left text-xs transition ${
                        cat
                          ? "border-emerald-200 bg-emerald-50 text-emerald-900 hover:bg-emerald-100"
                          : "border-dashed border-slate-300 bg-slate-50 text-slate-500 hover:bg-slate-100"
                      }`}
                      title="Click para cambiar categoría"
                    >
                      <span className="flex min-w-0 flex-col leading-tight">
                        <span className="truncate font-medium">{cat || "Sin categoría"}</span>
                        {sub && <span className="truncate text-[10px] opacity-75">{sub}</span>}
                      </span>
                      <ChevronDown className="h-3 w-3 shrink-0 opacity-60 group-hover:opacity-100" />
                    </button>
                  </td>
                  <td className="px-2 py-1.5 align-top">
                    <div className="flex items-center justify-end gap-1">
                      {!approved && !ignored && (
                        <IconBtn onClick={() => onApprove(m)} disabled={isPending} title="Aprobar" tone="ok">
                          <Check className="h-3.5 w-3.5" />
                        </IconBtn>
                      )}
                      {!ignored && (
                        <IconBtn
                          onClick={() => setIgnoreTarget({ ids: [m.id], bulk: false })}
                          disabled={isPending}
                          title="Ignorar"
                          tone="no"
                        >
                          <X className="h-3.5 w-3.5" />
                        </IconBtn>
                      )}
                      {(approved || ignored) && (
                        <IconBtn onClick={() => onReopen(m)} disabled={isPending} title="Reabrir">
                          <RotateCcw className="h-3.5 w-3.5" />
                        </IconBtn>
                      )}
                      {m.sheet_sync_status === "sync_error" && (
                        <IconBtn
                          onClick={() => onRetrySync(m)}
                          disabled={isPending}
                          title={m.sync_error_message ?? "Reintentar sync"}
                          tone="warn"
                        >
                          <RefreshCw className="h-3.5 w-3.5" />
                        </IconBtn>
                      )}
                      <IconBtn
                        onClick={(e) =>
                          setFlagsTarget({ id: m.id, rect: e.currentTarget.getBoundingClientRect() })
                        }
                        disabled={isPending}
                        title="Campos manuales (recurrente, extraordinario, excluido, esencial, fijo, notas)"
                      >
                        <SlidersHorizontal className="h-3.5 w-3.5" />
                      </IconBtn>
                      <IconBtn onClick={() => setAuditTarget(m.id)} title="Auditoría">
                        <ListTree className="h-3.5 w-3.5" />
                      </IconBtn>
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {comboTarget && (
        <CategoryComboboxPopover
          categories={categories}
          anchorRect={comboTarget.rect}
          defaultCat={items.find((m) => m.id === comboTarget.id)?.final_category ?? ""}
          defaultSub={items.find((m) => m.id === comboTarget.id)?.final_subcategory ?? ""}
          onSelect={(c2, s2) => {
            const mov = items.find((m) => m.id === comboTarget.id);
            if (mov) onSaveInlineCategory(mov, c2, s2);
          }}
          onClose={() => setComboTarget(null)}
          onAddNew={(query) => {
            const movId = comboTarget.id;
            setComboTarget(null);
            setAddCatModal({ movId, suggestedCat: query, suggestedSub: "" });
          }}
        />
      )}

      {flagsTarget &&
        (() => {
          const mov = items.find((m) => m.id === flagsTarget.id);
          if (!mov) return null;
          return (
            <FlagsPopover
              key={mov.id}
              mov={mov}
              anchorRect={flagsTarget.rect}
              saving={pendingMutations.has(mov.id)}
              onSave={(fields) => onSaveFlags(mov, fields)}
              onClose={() => setFlagsTarget(null)}
            />
          );
        })()}

      {ignoreTarget && (
        <IgnoreModal
          count={ignoreTarget.ids.length}
          onCancel={() => setIgnoreTarget(null)}
          onConfirm={onIgnoreConfirm}
        />
      )}

      {bulkCategorize && (
        <BulkCategorizeModal
          categories={categories}
          count={selected.size}
          onCancel={() => setBulkCategorize(false)}
          onConfirm={onBulkCategorize}
          onAddNew={(query) => {
            setBulkCategorize(false);
            setAddCatModal({ movId: null, suggestedCat: query, suggestedSub: "" });
          }}
        />
      )}

      {addCatModal && (
        <AddCategoryModal
          categories={categories}
          defaultCat={addCatModal.suggestedCat}
          defaultSub={addCatModal.suggestedSub}
          onCancel={() => setAddCatModal(null)}
          onCreated={(cat, sub) => {
            reloadCategories();
            const target = addCatModal;
            setAddCatModal(null);
            if (target.movId) {
              const mov = items.find((m) => m.id === target.movId);
              if (mov) onSaveInlineCategory(mov, cat, sub);
            } else {
              onBulkCategorize(cat, sub);
            }
          }}
        />
      )}

      {auditTarget && (
        <AuditDrawer movId={auditTarget} onClose={() => setAuditTarget(null)} />
      )}

      {toast && (
        <div className="fixed bottom-4 right-4 rounded bg-slate-900 px-4 py-2 text-sm text-white shadow-lg">
          {toast}
        </div>
      )}
    </div>
  );
}

// ── Sub-components ───────────────────────────────────────────────────────

/** Header de columna ordenable. */
function SortTh({
  label,
  sortKey,
  sort,
  onSort,
  className,
  align,
}: {
  label: string;
  sortKey: SortKey;
  sort: SortState | null;
  onSort: (k: SortKey) => void;
  className?: string;
  align?: "right";
}) {
  const active = sort?.key === sortKey;
  return (
    <th className={`px-2 py-2 ${className ?? ""}`}>
      <button
        onClick={() => onSort(sortKey)}
        className={`flex w-full items-center gap-1 uppercase tracking-wide hover:text-slate-800 ${
          align === "right" ? "justify-end" : ""
        } ${active ? "text-slate-800" : ""}`}
      >
        {label}
        {!active ? (
          <ChevronsUpDown className="h-3 w-3 opacity-30" />
        ) : sort!.dir === 1 ? (
          <ChevronDown className="h-3 w-3 rotate-180 text-blue-600" />
        ) : (
          <ChevronDown className="h-3 w-3 text-blue-600" />
        )}
      </button>
    </th>
  );
}

/** Botón de acción compacto e iconográfico. */
function IconBtn({
  children,
  onClick,
  disabled,
  title,
  tone,
}: {
  children: ReactNode;
  onClick: (e: MouseEvent<HTMLButtonElement>) => void;
  disabled?: boolean;
  title: string;
  tone?: "ok" | "no" | "warn";
}) {
  const toneCls =
    tone === "ok"
      ? "border-emerald-200 text-emerald-700 hover:bg-emerald-50"
      : tone === "no"
        ? "border-slate-200 text-slate-500 hover:bg-red-50 hover:text-red-700"
        : tone === "warn"
          ? "border-amber-200 text-amber-600 hover:bg-amber-50"
          : "border-slate-200 text-slate-500 hover:bg-slate-100 hover:text-slate-800";
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      title={title}
      aria-label={title}
      className={`inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md border bg-white transition disabled:opacity-40 ${toneCls}`}
    >
      {children}
    </button>
  );
}

/**
 * Texto con tooltip: hover en desktop, tap en mobile. El globo se posiciona
 * fixed (escapa del overflow de la tabla) calculando el rect del ancla.
 */
function Tip({
  text,
  children,
  className,
}: {
  text: string;
  children: ReactNode;
  className?: string;
}) {
  const ref = useRef<HTMLSpanElement | null>(null);
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);

  // Mobile: tap fuera del ancla cierra el tooltip.
  useEffect(() => {
    if (!pos) return;
    const onDoc = (e: globalThis.MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setPos(null);
    };
    document.addEventListener("click", onDoc);
    return () => document.removeEventListener("click", onDoc);
  }, [pos]);

  if (!text) return <span className={className}>{children}</span>;

  const open = () => {
    const rect = ref.current?.getBoundingClientRect();
    if (rect) {
      setPos({
        top: rect.bottom + 6,
        left: Math.min(rect.left, window.innerWidth - 300),
      });
    }
  };
  const close = () => setPos(null);

  return (
    <>
      <span
        ref={ref}
        className={`${className ?? ""} cursor-help`}
        onMouseEnter={open}
        onMouseLeave={close}
        onClick={(e) => {
          e.stopPropagation();
          pos ? close() : open();
        }}
      >
        {children}
      </span>
      {pos && (
        <div
          role="tooltip"
          className="fixed z-50 max-w-[280px] rounded-lg bg-slate-900 px-3 py-2 text-[11px] normal-case leading-relaxed text-white shadow-xl"
          style={{ top: pos.top, left: Math.max(8, pos.left) }}
        >
          {text}
        </div>
      )}
    </>
  );
}

const FLAGS_POPOVER_HEIGHT = 400;
const FLAGS_POPOVER_WIDTH = 288;

/**
 * Popover de edición de campos manuales (fuente de verdad: Firestore).
 * - Recurrente / Extraordinario / Excluido: booleans explícitos.
 * - Esencial / Fijo: override solo-TRUE — apagado envía null y el backend
 *   deriva el valor por categoría (el dashboard de KPIs no soporta override FALSE).
 * - Notas: texto libre; vacío envía null (limpia el campo).
 * Envía únicamente los campos que cambiaron respecto del movimiento.
 */
function FlagsPopover({
  mov,
  anchorRect,
  saving,
  onSave,
  onClose,
}: {
  mov: Movimiento;
  anchorRect: DOMRect;
  saving: boolean;
  onSave: (fields: ManualFields) => void;
  onClose: () => void;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [recurrente, setRecurrente] = useState(mov.recurrente === true);
  const [extraordinario, setExtraordinario] = useState(mov.extraordinario === true);
  const [excluido, setExcluido] = useState(mov.excluido === true);
  const [esencial, setEsencial] = useState(mov.esencial === true);
  const [fijo, setFijo] = useState(mov.fijo === true);
  const [notas, setNotas] = useState(mov.notas ?? "");

  const buildDiff = (): ManualFields => {
    const fields: ManualFields = {};
    if (recurrente !== (mov.recurrente === true)) fields.recurrente = recurrente;
    if (extraordinario !== (mov.extraordinario === true)) fields.extraordinario = extraordinario;
    if (excluido !== (mov.excluido === true)) fields.excluido = excluido;
    if (esencial !== (mov.esencial === true)) fields.esencial = esencial ? true : null;
    if (fijo !== (mov.fijo === true)) fields.fijo = fijo ? true : null;
    const notasOrig = (mov.notas ?? "").trim();
    const notasNew = notas.trim();
    if (notasNew !== notasOrig) fields.notas = notasNew === "" ? null : notasNew;
    return fields;
  };
  const dirty = Object.keys(buildDiff()).length > 0;

  const placeAbove =
    anchorRect.bottom + FLAGS_POPOVER_HEIGHT + 8 > window.innerHeight &&
    anchorRect.top > FLAGS_POPOVER_HEIGHT + 8;
  const maxLeft = window.innerWidth - FLAGS_POPOVER_WIDTH - 8;
  const left = Math.max(8, Math.min(maxLeft, anchorRect.left));
  const position = placeAbove
    ? { bottom: window.innerHeight - anchorRect.top + 4, left }
    : { top: anchorRect.bottom + 4, left };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    const onMouseDown = (e: globalThis.MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        onClose();
      }
    };
    const onScroll = (e: Event) => {
      // No cerrar por scroll interno (p.ej. dentro del textarea de notas).
      if (containerRef.current && e.target instanceof Node && containerRef.current.contains(e.target)) return;
      onClose();
    };
    window.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onMouseDown);
    window.addEventListener("scroll", onScroll, true);
    return () => {
      window.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onMouseDown);
      window.removeEventListener("scroll", onScroll, true);
    };
  }, [onClose]);

  return (
    <div
      ref={containerRef}
      role="dialog"
      aria-label="Campos manuales"
      className="fixed z-50 w-[288px] rounded-lg border border-slate-200 bg-white shadow-xl"
      style={position}
    >
      <div className="border-b border-slate-100 px-3 py-2">
        <h3 className="text-sm font-semibold text-slate-900">Campos manuales</h3>
        <p className="mt-0.5 truncate text-[10px] text-slate-500" title={mov.description}>
          {formatDate(mov.date)} · {mov.description}
        </p>
      </div>
      <div className="space-y-1.5 px-3 py-2">
        <label className="flex items-center gap-2 text-xs text-slate-700">
          <input
            type="checkbox"
            checked={recurrente}
            onChange={(e) => setRecurrente(e.target.checked)}
          />
          Recurrente
        </label>
        <label className="flex items-center gap-2 text-xs text-slate-700">
          <input
            type="checkbox"
            checked={extraordinario}
            onChange={(e) => setExtraordinario(e.target.checked)}
          />
          Extraordinario
        </label>
        <label className="flex items-center gap-2 text-xs text-slate-700">
          <input
            type="checkbox"
            checked={excluido}
            onChange={(e) => setExcluido(e.target.checked)}
          />
          Excluido
          <span className="text-[10px] text-slate-400">(fuera de KPIs)</span>
        </label>
        <div className="space-y-1.5 border-t border-slate-100 pt-2">
          <p className="text-[10px] text-slate-400">
            Overrides — apagado: se deriva por categoría
          </p>
          <label className="flex items-center gap-2 text-xs text-slate-700">
            <input
              type="checkbox"
              checked={esencial}
              onChange={(e) => setEsencial(e.target.checked)}
            />
            Marcar como esencial
          </label>
          <label className="flex items-center gap-2 text-xs text-slate-700">
            <input
              type="checkbox"
              checked={fijo}
              onChange={(e) => setFijo(e.target.checked)}
            />
            Marcar como fijo
          </label>
        </div>
        <div className="border-t border-slate-100 pt-2">
          <label className="text-[10px] font-medium text-slate-500" htmlFor={`notas-${mov.id}`}>
            Notas
          </label>
          <textarea
            id={`notas-${mov.id}`}
            value={notas}
            onChange={(e) => setNotas(e.target.value)}
            placeholder="Notas del movimiento…"
            className="mt-1 h-16 w-full rounded border border-slate-300 p-1.5 text-xs"
          />
        </div>
      </div>
      <div className="flex justify-end gap-2 border-t border-slate-100 px-3 py-2">
        <button
          onClick={onClose}
          className="rounded border border-slate-300 px-3 py-1 text-xs hover:bg-slate-50"
        >
          Cancelar
        </button>
        <button
          onClick={() => onSave(buildDiff())}
          disabled={!dirty || saving}
          className="rounded bg-blue-600 px-3 py-1 text-xs text-white hover:bg-blue-700 disabled:opacity-50"
        >
          {saving ? "Guardando…" : "Guardar"}
        </button>
      </div>
    </div>
  );
}

function IgnoreModal({
  count,
  onCancel,
  onConfirm,
}: {
  count: number;
  onCancel: () => void;
  onConfirm: (reason: string) => void;
}) {
  const [reason, setReason] = useState("");
  return (
    <div className="fixed inset-0 z-30 flex items-center justify-center bg-black/30 p-4">
      <div className="w-full max-w-md rounded-lg bg-white p-4 shadow-xl">
        <h2 className="text-base font-semibold text-slate-900">
          Ignorar {count} movimiento{count === 1 ? "" : "s"}
        </h2>
        <p className="mt-1 text-xs text-slate-500">
          La razón es obligatoria — queda registrada en auditoría.
        </p>
        <textarea
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          placeholder="¿Por qué los ignoras?"
          className="mt-3 h-24 w-full rounded border border-slate-300 p-2 text-sm"
          autoFocus
        />
        <div className="mt-3 flex justify-end gap-2">
          <button
            onClick={onCancel}
            className="rounded border border-slate-300 px-3 py-1 text-sm hover:bg-slate-50"
          >
            Cancelar
          </button>
          <button
            onClick={() => onConfirm(reason.trim())}
            disabled={!reason.trim()}
            className="rounded bg-slate-700 px-3 py-1 text-sm text-white hover:bg-slate-800 disabled:opacity-50"
          >
            Ignorar
          </button>
        </div>
      </div>
    </div>
  );
}

function BulkCategorizeModal({
  categories,
  count,
  onCancel,
  onConfirm,
  onAddNew,
}: {
  categories: CategoriesResponse | null;
  count: number;
  onCancel: () => void;
  onConfirm: (cat: string, sub: string | null) => void;
  onAddNew: (suggestedQuery: string) => void;
}) {
  return (
    <div className="fixed inset-0 z-30 flex items-center justify-center bg-black/30 p-4">
      <div className="w-full max-w-md rounded-lg bg-white shadow-xl">
        <div className="border-b border-slate-100 px-4 py-3">
          <h2 className="text-base font-semibold text-slate-900">
            Recategorizar {count} movimiento{count === 1 ? "" : "s"}
          </h2>
          <p className="mt-1 text-xs text-slate-500">
            Quedarán como corrected_pending — apruébalos individual o en bulk para enviarlos a Google Sheet.
          </p>
        </div>
        <CategoryComboboxContent
          categories={categories}
          defaultCat=""
          defaultSub=""
          onSelect={(cat, sub) => onConfirm(cat, sub)}
          onAddNew={onAddNew}
        />
        <div className="flex justify-end gap-2 border-t border-slate-100 px-4 py-3">
          <button
            onClick={onCancel}
            className="rounded border border-slate-300 px-3 py-1 text-sm hover:bg-slate-50"
          >
            Cancelar
          </button>
        </div>
      </div>
    </div>
  );
}

function AuditDrawer({ movId, onClose }: { movId: string; onClose: () => void }) {
  const [events, setEvents] = useState<AuditEvent[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch(`/api/movimientos/${encodeURIComponent(movId)}/audit`, { cache: "no-store" })
      .then((r) => r.json())
      .then((j) => setEvents(j.events ?? []))
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, [movId]);

  return (
    <div className="fixed inset-0 z-30 flex justify-end bg-black/30">
      <div className="h-full w-full max-w-md overflow-auto bg-white p-4 shadow-xl">
        <div className="flex items-center justify-between">
          <h2 className="text-base font-semibold text-slate-900">Auditoría</h2>
          <button onClick={onClose} className="text-sm text-slate-500 hover:text-slate-800">✕</button>
        </div>
        <p className="mt-1 text-xs text-slate-500">Movimiento {movId}</p>
        {error && <p className="mt-3 text-sm text-red-600">Error: {error}</p>}
        {!events && !error && <p className="mt-3 text-sm text-slate-500">Cargando…</p>}
        {events && events.length === 0 && <p className="mt-3 text-sm text-slate-500">Sin eventos.</p>}
        {events && events.length > 0 && (
          <ul className="mt-3 space-y-2 text-xs">
            {events.map((e) => (
              <li key={e.id} className="rounded border border-slate-200 p-2">
                <div className="flex justify-between">
                  <span className="font-medium text-slate-800">{e.action}</span>
                  <span className="text-slate-500">{e.created_at}</span>
                </div>
                <div className="mt-1 text-slate-600">
                  {e.prev_review_status} → <span className="font-medium">{e.new_review_status}</span>
                  {e.prev_sheet_sync_status !== e.new_sheet_sync_status && (
                    <span className="ml-2 text-slate-500">
                      sync: {e.prev_sheet_sync_status} → {e.new_sheet_sync_status}
                    </span>
                  )}
                </div>
                <div className="mt-1 text-[10px] uppercase text-slate-500">
                  {e.source} · {e.actor}
                </div>
                {e.details && Object.keys(e.details).length > 0 && (
                  <pre className="mt-1 overflow-auto rounded bg-slate-50 p-1 text-[10px] text-slate-600">
                    {JSON.stringify(e.details, null, 2)}
                  </pre>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
