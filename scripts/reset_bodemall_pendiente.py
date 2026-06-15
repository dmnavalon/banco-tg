"""Deja PENDIENTES los traspasos que ENTRAN de Bodemall / Sociedad Comercial
Industrial (hoy en 'Gastos por rendir') para que Diego los categorice uno a uno
como INGRESO en la sección Movimientos.

Decisión Diego (2026-06-14): los traspasos entrantes de Bodemall son ingreso
suyo (sueldo/distribución), NO rendición. La auto-clasificación/migración los
había marcado como rendición. Los devolvemos a 'pendiente' SIN cambiar la
categoría (siguen excluidos de los KPIs como rendición hasta que él los
reclasifique — evita que neteen mal en el interín). Él decide la categoría final.

Solo toca los ABONOS (entrantes). Los cargos (compras) no se tocan.

Uso:
    cd "Gestión de Gastos"
    .venv/bin/python -m scripts.reset_bodemall_pendiente            # dry-run
    .venv/bin/python -m scripts.reset_bodemall_pendiente --apply
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import db  # noqa: E402

CATEGORIA = "Gastos por rendir"
ENTIDADES = {"Bodemall", "Sociedad Comercial Industrial As"}


def _cat(m: dict) -> str:
    return (m.get("final_category") or m.get("suggested_category") or "").strip()


def _sub(m: dict) -> str:
    return (m.get("final_subcategory") or m.get("suggested_subcategory") or "").strip()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    movs = db.export_movements(exclude_ignored=False)
    objetivo = [
        m for m in movs
        if _cat(m) == CATEGORIA
        and _sub(m) in ENTIDADES
        and float(m.get("amount") or 0) >= 0          # solo entrantes (abonos)
        and m.get("review_status") != "pending"
    ]

    print(f"{len(objetivo)} traspasos entrantes de Bodemall a dejar PENDIENTES:\n")
    for m in objetivo:
        monto = abs(float(m.get("amount") or 0))
        print(f"  {m.get('date')}  {monto:>12,.0f}  {_sub(m):28} [{m.get('review_status')}] | {(m.get('description') or '')[:42]}")

    if not args.apply:
        print(f"\n[dry-run] No se escribió nada. Corre con --apply para dejar {len(objetivo)} en pendiente.")
        return

    for m in objetivo:
        db._db().collection("movements").document(m["id"]).update({
            "review_status": "pending",
            "status": "pendiente",          # legacy, para el bot
            "sheet_sync_status": "not_ready",
            "last_action_source": "system",
            "updated_at": db._now(),
        })
    print(f"\n✅ {len(objetivo)} traspasos de Bodemall dejados en pendiente para categorizar a mano.")


if __name__ == "__main__":
    main()
