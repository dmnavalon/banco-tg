"""Seedea la colección Firestore `taxonomy_meta` desde la pestaña GSheet
"TaxonomíaExtendida" (A2:F). Esa metadata (tipo_movimiento / esencial / fijo /
recurrente_default por categoría) es la que el backend usa para derivar
`tipo_movimiento` por movimiento — fuente única para KPIs y vista Movimientos.

Replica la indexación del dashboard (lib/sheets.ts): keyea por CATEGORÍA y
gana la última fila vista para esa categoría.

Uso:
    cd "Gestión de Gastos"
    .venv/bin/python -m scripts.seed_taxonomy_meta --dry-run   # reporta (default)
    .venv/bin/python -m scripts.seed_taxonomy_meta --apply     # escribe Firestore
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import db  # noqa: E402
from src.classifier import TIPO_MOVIMIENTO_VALUES  # noqa: E402
from src.gsheet import SPREADSHEET_ID, _client  # noqa: E402

TAB = "TaxonomíaExtendida"


def _parse_bool(s: str) -> bool:
    v = (s or "").strip().upper()
    return v in ("TRUE", "VERDADERO", "SÍ", "SI", "1")


def _read_rows() -> list[list[str]]:
    ws = _client().open_by_key(SPREADSHEET_ID).worksheet(TAB)
    return ws.get_all_values()[1:]  # salta header


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="escribe a Firestore (default: dry-run)")
    ap.add_argument("--dry-run", action="store_true", help="solo reporta")
    args = ap.parse_args()
    apply = args.apply and not args.dry_run

    rows = _read_rows()
    # Última fila gana por categoría (igual que taxIndex en el dashboard).
    meta: dict[str, dict] = {}
    for r in rows:
        cat = (r[0] if len(r) > 0 else "").strip()
        if not cat:
            continue
        tm = (r[5] if len(r) > 5 else "").strip()
        meta[cat] = {
            "tipo_movimiento": tm if tm in TIPO_MOVIMIENTO_VALUES else "GastoReal",
            "esencial": _parse_bool(r[2] if len(r) > 2 else ""),
            "fijo": _parse_bool(r[3] if len(r) > 3 else ""),
            "recurrente_default": _parse_bool(r[4] if len(r) > 4 else ""),
        }

    print(f"{len(meta)} categorías leídas de '{TAB}':\n")
    for cat, m in sorted(meta.items()):
        print(f"  {cat:32} tipo={m['tipo_movimiento']:14} ese={m['esencial']!s:5} fijo={m['fijo']!s:5}")

    if not apply:
        print(f"\n[dry-run] No se escribió nada. Corre con --apply para persistir {len(meta)} docs.")
        return

    for cat, m in meta.items():
        db.set_taxonomy_meta_doc(cat, m)
    print(f"\n✅ {len(meta)} docs escritos en taxonomy_meta.")


if __name__ == "__main__":
    main()
