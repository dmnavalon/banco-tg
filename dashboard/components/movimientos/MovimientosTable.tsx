"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type {
  AuditEvent,
  CategoriesResponse,
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
}

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
};

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

export function MovimientosTable() {
  const [tab, setTab] = useState<TabKey>("pendientes");
  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS);
  const [items, setItems] = useState<Movimiento[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [categories, setCategories] = useState<CategoriesResponse | null>(null);
  const [editingRow, setEditingRow] = useState<string | null>(null);
  const [pendingMutations, setPendingMutations] = useState<Set<string>>(new Set());
  const [ignoreTarget, setIgnoreTarget] = useState<{ ids: string[]; bulk: boolean } | null>(null);
  const [auditTarget, setAuditTarget] = useState<string | null>(null);
  const [bulkCategorize, setBulkCategorize] = useState(false);
  const [toast, setToast] = useState<string | null>(null);

  // Carga taxonomía una vez al montar.
  useEffect(() => {
    fetch("/api/categorias")
      .then((r) => (r.ok ? r.json() : null))
      .then((j) => setCategories(j))
      .catch(() => setCategories(null));
  }, []);

  const buildQuery = useCallback(() => {
    const sp = new URLSearchParams();
    sp.set("status", TAB_TO_FILTER[tab]);
    sp.set("limit", "200");
    for (const [k, v] of Object.entries(filters)) {
      if (v) sp.set(k, v);
    }
    return sp.toString();
  }, [tab, filters]);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await fetch(`/api/movimientos?${buildQuery()}`, { cache: "no-store" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
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

  // Auto-refresh cada 30s. Lo pausamos si hay edición inline o algún modal
  // abierto, para no perder el contexto del usuario.
  const editingRef = useRef(editingRow);
  editingRef.current = editingRow;
  useEffect(() => {
    if (!autoRefresh) return;
    const id = setInterval(() => {
      if (editingRef.current === null && ignoreTarget === null && auditTarget === null && !bulkCategorize) {
        refresh();
      }
    }, REFRESH_MS);
    return () => clearInterval(id);
  }, [autoRefresh, refresh, ignoreTarget, auditTarget, bulkCategorize]);

  const toggleSelected = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const toggleSelectAll = () => {
    if (selected.size === items.length) {
      setSelected(new Set());
    } else {
      setSelected(new Set(items.map((m) => m.id)));
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
    action: "approve" | "approve-correction" | "correct" | "ignore" | "reopen" | "sync",
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
      setEditingRow(null);
      showToast("Categoría actualizada (corrected_pending)");
    } else if (res.err === "version_conflict") {
      showToast("Conflicto: actualiza la tabla antes de guardar.");
      refresh();
    } else {
      showToast(`Error al guardar: ${res.err}`);
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
      const okCount = Object.values(j.results ?? {}).filter((x) => (x as { status: string }).status === "ok").length;
      const conflictCount = Object.values(j.results ?? {}).filter((x) => (x as { status: string }).status === "conflict").length;
      showToast(`Aprobados ${okCount}/${ids.length}` + (conflictCount ? ` · ${conflictCount} conflictos` : ""));
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
            onClick={() => setTab(k)}
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
      <div className="overflow-auto rounded border border-slate-200 bg-white">
        <table className="w-full text-xs">
          <thead className="border-b bg-slate-50 text-left text-slate-600">
            <tr>
              <th className="w-8 px-2 py-2">
                <input
                  type="checkbox"
                  checked={items.length > 0 && selected.size === items.length}
                  onChange={toggleSelectAll}
                />
              </th>
              <th className="px-2 py-2">Estado</th>
              <th className="px-2 py-2">Fecha</th>
              <th className="px-2 py-2">Banco</th>
              <th className="px-2 py-2">Texto original</th>
              <th className="px-2 py-2">Comercio</th>
              <th className="px-2 py-2 text-right">Monto</th>
              <th className="px-2 py-2">Tipo</th>
              <th className="px-2 py-2">Categoría → Sub</th>
              <th className="px-2 py-2">IA</th>
              <th className="px-2 py-2">Persona</th>
              <th className="px-2 py-2">Comentario</th>
              <th className="px-2 py-2">Origen</th>
              <th className="px-2 py-2">Updated</th>
              <th className="px-2 py-2">Acciones</th>
            </tr>
          </thead>
          <tbody>
            {items.length === 0 && !loading && (
              <tr>
                <td colSpan={15} className="px-2 py-8 text-center text-slate-500">
                  Sin movimientos en esta vista.
                </td>
              </tr>
            )}
            {items.map((m) => {
              const r = reviewBadge(m.review_status);
              const s = syncBadge(m.sheet_sync_status);
              const c = confidenceBadge(m.confidence);
              const isSelected = selected.has(m.id);
              const isPending = pendingMutations.has(m.id);
              const cat = m.final_category ?? m.suggested_category ?? "";
              const sub = m.final_subcategory ?? m.suggested_subcategory ?? "";
              const ignored = m.review_status === "ignored";
              const approved = m.review_status === "approved" || m.review_status === "corrected_approved";
              return (
                <tr
                  key={m.id}
                  className={`border-b border-slate-100 ${isSelected ? "bg-blue-50/40" : "hover:bg-slate-50"}`}
                >
                  <td className="px-2 py-1.5">
                    <input
                      type="checkbox"
                      checked={isSelected}
                      onChange={() => toggleSelected(m.id)}
                    />
                  </td>
                  <td className="px-2 py-1.5">
                    <div className="flex flex-col gap-0.5">
                      <span className={`inline-block rounded px-1.5 py-0.5 text-[10px] font-medium ${r.cls}`}>
                        {r.label}
                      </span>
                      {s && (
                        <span className={`inline-block rounded px-1.5 py-0.5 text-[10px] ${s.cls}`}>
                          {s.label}
                        </span>
                      )}
                    </div>
                  </td>
                  <td className="px-2 py-1.5 whitespace-nowrap text-slate-700">{formatDate(m.date)}</td>
                  <td className="px-2 py-1.5 capitalize text-slate-700">{m.bank}</td>
                  <td className="px-2 py-1.5 max-w-[260px] truncate" title={m.description}>
                    {m.description}
                  </td>
                  <td className="px-2 py-1.5 max-w-[140px] truncate text-slate-600" title={m.comercio_final ?? m.comercio ?? ""}>
                    {m.comercio_final ?? m.comercio ?? ""}
                  </td>
                  <td className="px-2 py-1.5 text-right whitespace-nowrap font-mono">
                    {formatCLP(m.amount)}
                  </td>
                  <td className="px-2 py-1.5 text-slate-700">{m.tipo ?? ""}</td>
                  <td className="px-2 py-1.5">
                    {editingRow === m.id ? (
                      <InlineCategoryEditor
                        categories={categories}
                        defaultCat={cat}
                        defaultSub={sub}
                        onCancel={() => setEditingRow(null)}
                        onSave={(c2, s2) => onSaveInlineCategory(m, c2, s2)}
                      />
                    ) : (
                      <button
                        onClick={() => setEditingRow(m.id)}
                        className="text-left text-slate-700 hover:underline"
                        title="Click para editar"
                      >
                        <div>{cat || <span className="text-slate-400">(sin)</span>}</div>
                        <div className="text-[10px] text-slate-500">{sub}</div>
                      </button>
                    )}
                  </td>
                  <td className="px-2 py-1.5 whitespace-nowrap">
                    {c && <span className={c.cls}>{c.label}</span>}
                  </td>
                  <td className="px-2 py-1.5 text-slate-600">{m.persona ?? ""}</td>
                  <td className="px-2 py-1.5 max-w-[140px] truncate" title={m.comment ?? m.ignore_reason ?? ""}>
                    {m.comment ?? (m.ignore_reason ? `🚫 ${m.ignore_reason}` : "")}
                  </td>
                  <td className="px-2 py-1.5 text-[10px] uppercase text-slate-500">{m.last_action_source}</td>
                  <td className="px-2 py-1.5 whitespace-nowrap text-[10px] text-slate-500">
                    {m.updated_at?.slice(0, 16) ?? ""}
                  </td>
                  <td className="px-2 py-1.5">
                    <div className="flex flex-wrap gap-1">
                      {!approved && !ignored && (
                        <button
                          onClick={() => onApprove(m)}
                          disabled={isPending}
                          className="rounded bg-emerald-600 px-2 py-0.5 text-[10px] text-white hover:bg-emerald-700 disabled:opacity-50"
                        >
                          Aprobar
                        </button>
                      )}
                      {!ignored && (
                        <button
                          onClick={() => setIgnoreTarget({ ids: [m.id], bulk: false })}
                          disabled={isPending}
                          className="rounded bg-slate-600 px-2 py-0.5 text-[10px] text-white hover:bg-slate-700 disabled:opacity-50"
                        >
                          Ignorar
                        </button>
                      )}
                      {(approved || ignored) && (
                        <button
                          onClick={() => onReopen(m)}
                          disabled={isPending}
                          className="rounded border border-slate-300 px-2 py-0.5 text-[10px] hover:bg-slate-50 disabled:opacity-50"
                        >
                          Reabrir
                        </button>
                      )}
                      {m.sheet_sync_status === "sync_error" && (
                        <button
                          onClick={() => onRetrySync(m)}
                          disabled={isPending}
                          className="rounded bg-amber-500 px-2 py-0.5 text-[10px] text-white hover:bg-amber-600 disabled:opacity-50"
                          title={m.sync_error_message ?? ""}
                        >
                          Reintentar sync
                        </button>
                      )}
                      <button
                        onClick={() => setAuditTarget(m.id)}
                        className="rounded border border-slate-300 px-2 py-0.5 text-[10px] hover:bg-slate-50"
                      >
                        Audit
                      </button>
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

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

function InlineCategoryEditor({
  categories,
  defaultCat,
  defaultSub,
  onCancel,
  onSave,
}: {
  categories: CategoriesResponse | null;
  defaultCat: string;
  defaultSub: string;
  onCancel: () => void;
  onSave: (cat: string, sub: string | null) => void;
}) {
  const [cat, setCat] = useState(defaultCat);
  const [sub, setSub] = useState(defaultSub);
  const subs = useMemo(() => (categories?.taxonomy?.[cat] ?? []), [categories, cat]);
  const isExtensible = categories?.extensible_categories.includes(cat) ?? false;

  return (
    <div className="flex flex-col gap-1">
      <select
        value={cat}
        onChange={(e) => {
          setCat(e.target.value);
          setSub("");
        }}
        className="rounded border border-slate-300 px-1 py-0.5 text-xs"
        autoFocus
      >
        <option value="">— Categoría —</option>
        {categories &&
          Object.keys(categories.taxonomy).map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
      </select>
      {isExtensible ? (
        <input
          value={sub}
          onChange={(e) => setSub(e.target.value)}
          placeholder="Subcategoría (libre)"
          className="rounded border border-slate-300 px-1 py-0.5 text-xs"
        />
      ) : (
        <select
          value={sub}
          onChange={(e) => setSub(e.target.value)}
          className="rounded border border-slate-300 px-1 py-0.5 text-xs"
        >
          <option value="">— Sub —</option>
          {subs.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
      )}
      <div className="flex gap-1">
        <button
          onClick={() => onSave(cat, sub || null)}
          disabled={!cat}
          className="flex-1 rounded bg-blue-600 px-2 py-0.5 text-[10px] text-white hover:bg-blue-700 disabled:opacity-50"
        >
          Guardar
        </button>
        <button
          onClick={onCancel}
          className="rounded border border-slate-300 px-2 py-0.5 text-[10px] hover:bg-slate-50"
        >
          ✕
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
}: {
  categories: CategoriesResponse | null;
  count: number;
  onCancel: () => void;
  onConfirm: (cat: string, sub: string | null) => void;
}) {
  const [cat, setCat] = useState("");
  const [sub, setSub] = useState("");
  const subs = useMemo(() => (categories?.taxonomy?.[cat] ?? []), [categories, cat]);
  const isExtensible = categories?.extensible_categories.includes(cat) ?? false;
  return (
    <div className="fixed inset-0 z-30 flex items-center justify-center bg-black/30 p-4">
      <div className="w-full max-w-md rounded-lg bg-white p-4 shadow-xl">
        <h2 className="text-base font-semibold text-slate-900">
          Recategorizar {count} movimiento{count === 1 ? "" : "s"}
        </h2>
        <p className="mt-1 text-xs text-slate-500">
          Quedarán como corrected_pending — apruébalos individual o en bulk para enviarlos a Google Sheet.
        </p>
        <select
          value={cat}
          onChange={(e) => {
            setCat(e.target.value);
            setSub("");
          }}
          className="mt-3 w-full rounded border border-slate-300 px-2 py-1 text-sm"
        >
          <option value="">— Categoría —</option>
          {categories &&
            Object.keys(categories.taxonomy).map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
        </select>
        {isExtensible ? (
          <input
            value={sub}
            onChange={(e) => setSub(e.target.value)}
            placeholder="Subcategoría (libre)"
            className="mt-2 w-full rounded border border-slate-300 px-2 py-1 text-sm"
          />
        ) : (
          <select
            value={sub}
            onChange={(e) => setSub(e.target.value)}
            className="mt-2 w-full rounded border border-slate-300 px-2 py-1 text-sm"
          >
            <option value="">— Subcategoría —</option>
            {subs.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        )}
        <div className="mt-3 flex justify-end gap-2">
          <button
            onClick={onCancel}
            className="rounded border border-slate-300 px-3 py-1 text-sm hover:bg-slate-50"
          >
            Cancelar
          </button>
          <button
            onClick={() => onConfirm(cat, sub || null)}
            disabled={!cat}
            className="rounded bg-violet-600 px-3 py-1 text-sm text-white hover:bg-violet-700 disabled:opacity-50"
          >
            Aplicar
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
