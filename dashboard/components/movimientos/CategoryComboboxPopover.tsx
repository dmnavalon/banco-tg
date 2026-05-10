"use client";

import { Plus, Search, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import type { CategoriesResponse } from "@/lib/movimientos-types";

export interface ComboOption {
  cat: string;
  sub: string;
  label: string;
}

export function normalizeText(s: string): string {
  return s.toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "");
}

export function buildComboOptions(taxonomy: Record<string, string[]>): ComboOption[] {
  const opts: ComboOption[] = [];
  for (const [cat, subs] of Object.entries(taxonomy)) {
    if (!subs || subs.length === 0) {
      opts.push({ cat, sub: "", label: cat });
      continue;
    }
    for (const sub of subs) {
      opts.push({ cat, sub, label: `${cat} / ${sub}` });
    }
  }
  return opts.sort((a, b) => a.label.localeCompare(b.label, "es"));
}

function filterOptions(opts: ComboOption[], query: string): ComboOption[] {
  const q = query.trim();
  if (!q) return opts;
  const nq = normalizeText(q);
  return opts.filter((o) => normalizeText(o.label).includes(nq));
}

interface ContentProps {
  categories: CategoriesResponse | null;
  defaultCat: string;
  defaultSub: string;
  onSelect: (cat: string, sub: string | null) => void;
  onAddNew: (suggestedQuery: string) => void;
}

export function CategoryComboboxContent({
  categories,
  defaultCat,
  defaultSub,
  onSelect,
  onAddNew,
}: ContentProps) {
  const [query, setQuery] = useState("");
  const inputRef = useRef<HTMLInputElement | null>(null);

  const options = useMemo(
    () => buildComboOptions(categories?.taxonomy ?? {}),
    [categories],
  );
  const filtered = useMemo(() => filterOptions(options, query), [options, query]);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Enter" && filtered.length > 0) {
        const o = filtered[0];
        onSelect(o.cat, o.sub || null);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [filtered, onSelect]);

  return (
    <div className="flex flex-col">
      <div className="flex items-center gap-1 border-b border-slate-100 bg-white px-2 py-2">
        <Search className="h-3.5 w-3.5 shrink-0 text-slate-400" />
        <input
          ref={inputRef}
          type="text"
          placeholder="Buscar categoría o subcategoría..."
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          className="flex-1 bg-transparent text-xs outline-none placeholder:text-slate-400"
          aria-label="Buscar categoría"
        />
        {query && (
          <button
            onClick={() => setQuery("")}
            className="text-slate-400 hover:text-slate-600"
            aria-label="Limpiar búsqueda"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        )}
      </div>
      <ul role="listbox" className="max-h-[250px] overflow-y-auto py-1">
        {filtered.length === 0 && (
          <li className="px-3 py-4 text-center text-xs text-slate-400">
            Sin coincidencias.
          </li>
        )}
        {filtered.map((o) => {
          const isCurrent = o.cat === defaultCat && (o.sub || "") === (defaultSub || "");
          return (
            <li key={o.label}>
              <button
                role="option"
                aria-selected={isCurrent}
                onClick={() => onSelect(o.cat, o.sub || null)}
                className={`flex w-full items-center justify-between gap-2 px-3 py-1.5 text-left text-xs transition hover:bg-emerald-50 ${
                  isCurrent ? "bg-slate-100 font-medium text-slate-900" : "text-slate-700"
                }`}
              >
                <span className="min-w-0 truncate">
                  <span className="text-slate-500">{o.cat}</span>
                  {o.sub && (
                    <>
                      <span className="text-slate-300"> / </span>
                      <span className="text-slate-900">{o.sub}</span>
                    </>
                  )}
                </span>
                {isCurrent && (
                  <span className="shrink-0 text-[10px] text-emerald-600">actual</span>
                )}
              </button>
            </li>
          );
        })}
      </ul>
      <div className="border-t border-slate-100 bg-slate-50 p-2">
        <button
          onClick={() => onAddNew(query)}
          className="flex w-full items-center justify-center gap-1.5 rounded bg-violet-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-violet-700"
        >
          <Plus className="h-3.5 w-3.5" />
          Agregar categoría y subcategoría
        </button>
      </div>
    </div>
  );
}

const POPOVER_HEIGHT = 360;
const POPOVER_WIDTH = 320;

interface PopoverProps extends ContentProps {
  anchorRect: DOMRect;
  onClose: () => void;
}

export function CategoryComboboxPopover(props: PopoverProps) {
  const { anchorRect, onClose, ...content } = props;
  const containerRef = useRef<HTMLDivElement | null>(null);

  const placeAbove =
    anchorRect.bottom + POPOVER_HEIGHT + 8 > window.innerHeight &&
    anchorRect.top > POPOVER_HEIGHT + 8;
  const top = placeAbove ? anchorRect.top - POPOVER_HEIGHT - 4 : anchorRect.bottom + 4;
  const maxLeft = window.innerWidth - POPOVER_WIDTH - 8;
  const left = Math.max(8, Math.min(maxLeft, anchorRect.left));

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    const onMouseDown = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        onClose();
      }
    };
    const onScroll = () => onClose();
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
      aria-label="Selector de categoría"
      className="fixed z-50 w-[320px] rounded-lg border border-slate-200 bg-white shadow-xl"
      style={{ top, left }}
    >
      <CategoryComboboxContent {...content} />
    </div>
  );
}
