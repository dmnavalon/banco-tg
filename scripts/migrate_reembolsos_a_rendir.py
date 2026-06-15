"""Mueve los REEMBOLSOS de terceros (hoy en categoría "Reembolsos", subcats
"Reembolso empresa/familiar/de amigos") a "Gastos por rendir / <entidad>", para
que el dashboard los trate como rendiciones (fuera de ingresos/gastos) y se
neteen por entidad contra los gastos que adelantaste.

Las "Devolución comercio" y "Seguro reembolsado" NO se tocan: son devoluciones
reales y deben netear contra el gasto (se quedan como están).

La entidad se infiere del contraparte en la descripción ("Traspaso De: X").
Revisa el dry-run antes de aplicar — el mapeo de nombres es heurístico.

Uso:
    cd "Gestión de Gastos"
    .venv/bin/python -m scripts.migrate_reembolsos_a_rendir            # dry-run
    .venv/bin/python -m scripts.migrate_reembolsos_a_rendir --apply
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import db  # noqa: E402

# Subcategorías de "Reembolsos" que SÍ son rendiciones (se mueven).
SUBCATS_RENDICION = {"Reembolso empresa", "Reembolso familiar", "Reembolso de amigos"}

# Mapeo contraparte → entidad canónica (Gastos por rendir). Por palabra clave.
ENTIDAD_POR_KEYWORD = [
    ("faind", "Faind"),
    ("sociedad comercial", "Bodemall"),
    ("amplia", "Amplia"),
    ("francisco", "Papá"),          # Francisco Martinez
]


def _entidad(desc: str, subcat: str) -> str:
    d = (desc or "").lower()
    for kw, ent in ENTIDAD_POR_KEYWORD:
        if kw in d:
            return ent
    # Fallback: nombre tras "De:" (para amigos sin keyword conocida).
    m = re.search(r"de:\s*(.+)", desc or "", re.I)
    if m:
        nombre = m.group(1).strip().split()[:2]  # primeros 2 tokens
        if nombre:
            return " ".join(nombre).title()
    return "Por identificar"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    movs = db.export_movements(exclude_ignored=False)
    objetivo = [
        m for m in movs
        if (m.get("final_category") or m.get("suggested_category")) == "Reembolsos"
        and (m.get("final_subcategory") or m.get("suggested_subcategory")) in SUBCATS_RENDICION
    ]

    print(f"{len(objetivo)} reembolsos de terceros a mover a 'Gastos por rendir':\n")
    plan = []
    for m in objetivo:
        ent = _entidad(m.get("description") or "", m.get("final_subcategory") or m.get("suggested_subcategory") or "")
        plan.append((m, ent))
        monto = abs(float(m.get("amount") or 0))
        print(f"  {m.get('date')}  {monto:>12,.0f}  {(m.get('final_subcategory') or m.get('suggested_subcategory')):20} → Gastos por rendir / {ent:14} | {(m.get('description') or '')[:45]}")

    sin_ident = [p for p in plan if p[1] == "Por identificar"]
    if sin_ident:
        print(f"\n⚠️  {len(sin_ident)} sin entidad clara (quedan como 'Por identificar' — revísalos a mano luego).")

    if not args.apply:
        print(f"\n[dry-run] No se escribió nada. Corre con --apply para mover {len(plan)} movimientos.")
        return

    for m, ent in plan:
        db._db().collection("movements").document(m["id"]).update({
            "final_category": "Gastos por rendir",
            "final_subcategory": ent,
            "last_action_source": "system",
            "updated_at": db._now(),
        })
    print(f"\n✅ {len(plan)} movimientos movidos a Gastos por rendir.")


if __name__ == "__main__":
    main()
