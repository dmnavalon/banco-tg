"""Verifica que toda fila de la pestaña GSheet "Movimientos" exista en Firestore.

Tras la migración el dashboard lee SOLO Firestore. La mayoría de las filas del
Sheet vinieron del sync (tienen MovementId en col Y), pero puede haber filas que
Diego tipeó a mano directo en el Sheet — esas hay que subirlas a Firestore.

Match en dos pasos (evita falsos faltantes y duplicados):
  1. Col Y (MovementId) presente → debe existir el doc en Firestore.
  2. Sin col Y → match por triple (fecha, |monto|, descripción canónica) contra
     el índice de Firestore. Solo se considera FALTANTE si ninguno matchea.

Uso:
    cd "Gestión de Gastos"
    .venv/bin/python -m scripts.verify_movements_firestore --dry-run  # reporta (default)
    .venv/bin/python -m scripts.verify_movements_firestore --apply    # inserta faltantes
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import db  # noqa: E402
from src.gsheet import SHEET_HEADER, SHEET_NAME, SPREADSHEET_ID, _client  # noqa: E402
from src.utils import canonical_description, movement_id, normalize  # noqa: E402

_H = {name: i for i, name in enumerate(SHEET_HEADER)}


def _date_iso(ddmmyyyy: str) -> str | None:
    p = (ddmmyyyy or "").strip().split("/")
    if len(p) != 3:
        return None
    d, m, y = p
    if len(y) == 2:
        y = "20" + y
    try:
        return f"{int(y):04d}-{int(m):02d}-{int(d):02d}"
    except ValueError:
        return None


def _num(s: str) -> float:
    s = (s or "").strip().replace("$", "").replace(".", "").replace(",", ".").replace(" ", "")
    try:
        return float(s)
    except ValueError:
        return 0.0


def _triple_key(date_iso: str, amount_abs: float, desc: str) -> str:
    return f"{date_iso}|{round(amount_abs)}|{normalize(canonical_description(desc))}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="inserta faltantes en Firestore")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    apply = args.apply and not args.dry_run

    # Índice Firestore: por id y por triple.
    fs = db.export_movements(exclude_ignored=False)
    by_id = {m.get("id") for m in fs}
    by_triple = set()
    for m in fs:
        amt = abs(float(m.get("amount") or 0))
        by_triple.add(_triple_key(m.get("date") or "", amt, m.get("description") or ""))

    ws = _client().open_by_key(SPREADSHEET_ID).worksheet(SHEET_NAME)
    rows = ws.get_all_values()[1:]

    missing: list[dict] = []
    anomalies: list[str] = []
    for r in rows:
        def col(name: str) -> str:
            i = _H.get(name, -1)
            return r[i].strip() if 0 <= i < len(r) else ""

        fecha_iso = _date_iso(col("Fecha"))
        if not fecha_iso or not col("Descripción"):
            continue
        mov_id_sheet = col("MovementId")
        monto_abs = _num(col("Monto"))
        tipo = col("Tipo")
        amount = monto_abs if tipo == "Abono" else -monto_abs
        desc = col("Descripción")

        if mov_id_sheet:
            if mov_id_sheet not in by_id:
                anomalies.append(f"MovementId {mov_id_sheet} ({fecha_iso} {desc[:30]}) en Sheet pero NO en Firestore")
            continue

        # Sin col Y: match por triple.
        if _triple_key(fecha_iso, monto_abs, desc) in by_triple:
            continue

        mid = movement_id(fecha_iso, amount, desc, col("Banco"), None)
        if mid in by_id:
            continue
        missing.append({
            "mov_id": mid,
            "date_iso": fecha_iso,
            "description": desc,
            "amount": amount,
            "bank": col("Banco"),
            "persona": col("Persona") or None,
            "categoria": col("Categoría") or None,
            "subcategoria": col("Subcategoría") or None,
            "moneda": col("Moneda") or "CLP",
            "monto_clp": _num(col("MontoCLP")) or None,
            "notas": col("Notas") or None,
        })

    print(f"Sheet: {len(rows)} filas · Firestore: {len(fs)} docs")
    print(f"Anomalías (col Y sin doc): {len(anomalies)}")
    for a in anomalies:
        print(f"  ⚠️  {a}")
    print(f"\nFaltantes (filas del Sheet ausentes en Firestore): {len(missing)}")
    for m in missing:
        print(f"  + {m['date_iso']} {m['description'][:40]:40} {m['amount']:>12,.0f}  [{m['categoria']}]")

    if not apply:
        print(f"\n[dry-run] No se escribió nada. Corre con --apply para insertar {len(missing)} movs.")
        return

    inserted = 0
    for m in missing:
        created = db.insert_movement(
            mov_id=m["mov_id"], date_iso=m["date_iso"], description=m["description"],
            amount=m["amount"], movement_type=("abono" if m["amount"] >= 0 else "cargo"),
            account=None, bank=m["bank"], raw_blob=None,
            suggested_category=m["categoria"], suggested_subcategory=m["subcategoria"],
            persona=m["persona"],
        )
        if not created:
            continue
        # Vienen del Sheet → son datos ya aprobados por Diego. Marcar approved +
        # final_category + campos manuales para que cuadren con los KPIs.
        db._db().collection("movements").document(m["mov_id"]).update({
            "review_status": "approved",
            "sheet_sync_status": "synced",
            "final_category": m["categoria"],
            "final_subcategory": m["subcategoria"],
            "moneda": m["moneda"],
            "monto_clp": m["monto_clp"],
            "notas": m["notas"],
            "last_action_source": "system",
        })
        inserted += 1
    print(f"\n✅ {inserted} movs insertados como approved.")


if __name__ == "__main__":
    main()
