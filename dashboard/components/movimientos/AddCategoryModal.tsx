"use client";

import { useEffect, useMemo, useState } from "react";

import { normalizeText } from "@/components/movimientos/CategoryComboboxPopover";
import type { CategoriesResponse, CreateCategoryResponse } from "@/lib/movimientos-types";

interface Props {
  categories: CategoriesResponse | null;
  defaultCat: string;
  defaultSub: string;
  onCancel: () => void;
  onCreated: (cat: string, sub: string) => void;
}

function similarStrings(input: string, candidates: string[], maxResults = 3): string[] {
  const ni = normalizeText(input.trim());
  if (ni.length < 3) return [];
  return candidates
    .filter((c) => {
      const nc = normalizeText(c);
      if (nc === ni) return false;
      return nc.includes(ni) || ni.includes(nc);
    })
    .slice(0, maxResults);
}

export function AddCategoryModal({
  categories,
  defaultCat,
  defaultSub,
  onCancel,
  onCreated,
}: Props) {
  const [cat, setCat] = useState(defaultCat);
  const [sub, setSub] = useState(defaultSub);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [warnSimilar, setWarnSimilar] = useState<{ cats: string[]; subs: string[] } | null>(null);

  const taxonomy = categories?.taxonomy ?? {};
  const allCats = useMemo(
    () => Object.keys(taxonomy).sort((a, b) => a.localeCompare(b, "es")),
    [taxonomy],
  );
  const subsOfCat = useMemo(() => taxonomy[cat] ?? [], [taxonomy, cat]);

  const exactCombo = useMemo(() => {
    const c = cat.trim();
    const s = sub.trim();
    if (!c || !s) return false;
    const subs = taxonomy[c];
    return Array.isArray(subs) && subs.includes(s);
  }, [cat, sub, taxonomy]);

  const localSimilarCats = useMemo(() => similarStrings(cat, allCats), [cat, allCats]);
  const localSimilarSubs = useMemo(() => similarStrings(sub, subsOfCat), [sub, subsOfCat]);

  useEffect(() => {
    setError(null);
    setWarnSimilar(null);
  }, [cat, sub]);

  const useExisting = () => {
    onCreated(cat.trim(), sub.trim());
  };

  const handleSubmit = async () => {
    if (submitting) return;
    if (!cat.trim() || !sub.trim()) {
      setError("Categoría y subcategoría son obligatorias");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const r = await fetch("/api/categorias", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ cat: cat.trim(), sub: sub.trim() }),
      });
      const j = (await r.json().catch(() => null)) as
        | CreateCategoryResponse
        | { error: string; message?: string }
        | null;
      if (!r.ok || !j || "error" in j) {
        const msg = j && "message" in j && j.message ? j.message : `HTTP ${r.status}`;
        setError(msg);
        return;
      }
      const ok = j as CreateCategoryResponse;
      const sim = ok.similar;
      if (sim && (sim.cats.length > 0 || sim.subs.length > 0)) {
        setWarnSimilar(sim);
      }
      onCreated(ok.cat, ok.sub);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-40 flex items-center justify-center bg-black/30 p-4">
      <div className="w-full max-w-md rounded-lg bg-white p-4 shadow-xl">
        <h2 className="text-base font-semibold text-slate-900">
          Agregar categoría y subcategoría
        </h2>
        <p className="mt-1 text-xs text-slate-500">
          Crea una combinación nueva. Si ya existe, te ofreceré usarla.
        </p>

        <label className="mt-3 block text-xs font-medium text-slate-700">Categoría</label>
        <input
          list="add-cat-options"
          value={cat}
          onChange={(e) => setCat(e.target.value)}
          placeholder="Ej: Hogar y alimentación"
          className="mt-1 w-full rounded border border-slate-300 px-2 py-1 text-sm"
          autoFocus
        />
        <datalist id="add-cat-options">
          {allCats.map((c) => (
            <option key={c} value={c} />
          ))}
        </datalist>
        {localSimilarCats.length > 0 && (
          <p className="mt-1 text-[11px] text-amber-700">
            Categorías parecidas ya existentes:{" "}
            {localSimilarCats.map((c, i) => (
              <button
                key={c}
                onClick={() => setCat(c)}
                className="underline hover:no-underline"
                type="button"
              >
                {c}
                {i < localSimilarCats.length - 1 ? ", " : ""}
              </button>
            ))}
          </p>
        )}

        <label className="mt-3 block text-xs font-medium text-slate-700">Subcategoría</label>
        <input
          list="add-sub-options"
          value={sub}
          onChange={(e) => setSub(e.target.value)}
          placeholder="Ej: Supermercado"
          className="mt-1 w-full rounded border border-slate-300 px-2 py-1 text-sm"
        />
        <datalist id="add-sub-options">
          {subsOfCat.map((s) => (
            <option key={s} value={s} />
          ))}
        </datalist>
        {localSimilarSubs.length > 0 && (
          <p className="mt-1 text-[11px] text-amber-700">
            Subcategorías parecidas en {cat}:{" "}
            {localSimilarSubs.map((s, i) => (
              <button
                key={s}
                onClick={() => setSub(s)}
                className="underline hover:no-underline"
                type="button"
              >
                {s}
                {i < localSimilarSubs.length - 1 ? ", " : ""}
              </button>
            ))}
          </p>
        )}

        {exactCombo && (
          <div className="mt-3 rounded border border-emerald-200 bg-emerald-50 p-2 text-xs text-emerald-800">
            Esta combinación ya existe.{" "}
            <button
              onClick={useExisting}
              className="font-medium underline hover:no-underline"
              type="button"
            >
              Usar combinación existente
            </button>
          </div>
        )}

        {warnSimilar &&
          (warnSimilar.cats.length > 0 || warnSimilar.subs.length > 0) && (
            <div className="mt-3 rounded border border-amber-200 bg-amber-50 p-2 text-xs text-amber-800">
              Guardado, pero hay combinaciones parecidas:{" "}
              {warnSimilar.cats.length > 0 && <>cats: {warnSimilar.cats.join(", ")}; </>}
              {warnSimilar.subs.length > 0 && <>subs: {warnSimilar.subs.join(", ")}</>}
            </div>
          )}

        {error && (
          <div className="mt-3 rounded border border-red-200 bg-red-50 p-2 text-xs text-red-800">
            {error}
          </div>
        )}

        <div className="mt-4 flex justify-end gap-2">
          <button
            onClick={onCancel}
            disabled={submitting}
            className="rounded border border-slate-300 px-3 py-1 text-sm hover:bg-slate-50 disabled:opacity-50"
          >
            Cancelar
          </button>
          <button
            onClick={handleSubmit}
            disabled={submitting || !cat.trim() || !sub.trim() || exactCombo}
            className="rounded bg-violet-600 px-3 py-1 text-sm text-white hover:bg-violet-700 disabled:opacity-50"
          >
            {submitting ? "Guardando…" : "Guardar y usar"}
          </button>
        </div>
      </div>
    </div>
  );
}
