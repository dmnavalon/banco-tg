import { type ClassValue, clsx } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

const clpFmt = new Intl.NumberFormat("es-CL", {
  style: "currency",
  currency: "CLP",
  maximumFractionDigits: 0,
});

const clpFmtCompact = new Intl.NumberFormat("es-CL", {
  style: "currency",
  currency: "CLP",
  maximumFractionDigits: 0,
  notation: "compact",
});

export function formatCLP(n: number | null | undefined, compact = false): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return (compact ? clpFmtCompact : clpFmt).format(n);
}

export function formatPct(n: number | null | undefined, decimals = 1): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return `${(n * 100).toFixed(decimals)}%`;
}

export function formatNum(n: number | null | undefined, decimals = 1): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  return n.toFixed(decimals);
}

export function formatMonths(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  if (!Number.isFinite(n)) return "∞";
  return `${n.toFixed(1)} m`;
}

export function monthKey(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

export function parseChileanDate(s: string): Date | null {
  if (!s) return null;
  // formats: DD/MM/YYYY, YYYY-MM-DD, or already Date
  const m = s.match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})$/);
  if (m) {
    return new Date(Number(m[3]), Number(m[2]) - 1, Number(m[1]));
  }
  const iso = s.match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (iso) {
    return new Date(Number(iso[1]), Number(iso[2]) - 1, Number(iso[3]));
  }
  const t = Date.parse(s);
  if (!Number.isNaN(t)) return new Date(t);
  return null;
}

export function parseNumber(s: string | number | null | undefined): number {
  if (s === null || s === undefined || s === "") return 0;
  if (typeof s === "number") return s;
  // strip CLP formatting: "$1.234.567" or "1.234.567,89"
  const cleaned = String(s)
    .replace(/[$\s]/g, "")
    .replace(/\./g, "")
    .replace(",", ".");
  const n = Number(cleaned);
  return Number.isNaN(n) ? 0 : n;
}

export function parseBool(s: string | boolean | null | undefined): boolean {
  if (typeof s === "boolean") return s;
  if (!s) return false;
  const v = String(s).trim().toUpperCase();
  return v === "TRUE" || v === "VERDADERO" || v === "SÍ" || v === "SI" || v === "1";
}

/** Promedio de un array de números, ignorando NaN. Devuelve null si no hay datos. */
export function avg(arr: number[]): number | null {
  const filtered = arr.filter((n) => Number.isFinite(n));
  if (filtered.length === 0) return null;
  return filtered.reduce((s, n) => s + n, 0) / filtered.length;
}

export function deltaPct(actual: number, base: number | null): number | null {
  if (base === null || base === 0 || !Number.isFinite(base)) return null;
  return (actual - base) / Math.abs(base);
}

/** Devuelve la lista de meses YYYY-MM previos a `mes`, ordenados de más reciente a más antiguo. */
export function previousMonths(mes: string, count: number): string[] {
  const [y, m] = mes.split("-").map(Number);
  const result: string[] = [];
  let year = y;
  let month = m;
  for (let i = 0; i < count; i++) {
    month -= 1;
    if (month === 0) {
      month = 12;
      year -= 1;
    }
    result.push(`${year}-${String(month).padStart(2, "0")}`);
  }
  return result;
}
