"""Backfill no destructivo de los nuevos campos de la feature Movimientos:
- review_status (a partir de status legacy)
- sheet_sync_status (a partir de status + match en GSheet)
- version (= 1 si nunca había)
- updated_at (= inserted_at)
- sheet_row_id (cuando se encuentra fila en GSheet por triple)

Idempotente: si el doc ya tiene review_status, lo skipea. NO toca status legacy.

Uso:
    cd "Gestión de Gastos"
    .venv/bin/python -m scripts.backfill_movement_status --dry-run
    .venv/bin/python -m scripts.backfill_movement_status
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import gspread  # noqa: E402
from google.oauth2.service_account import Credentials  # noqa: E402

from src import db  # noqa: E402
from src.gsheet import (  # noqa: E402
    SHEET_NAME,
    SPREADSHEET_ID,
    _COL_DESCRIPCION,
    _COL_FECHA,
    _COL_MONTO,
    _norm_desc,
)


_LEGACY_TO_REVIEW = {
    "pendiente": "pending",
    "aprobado": "approved",
    "ignorado": "ignorado",  # se sobrescribe abajo a "ignored"
}


def _legacy_to_review(status: str | None) -> str:
    s = (status or "").lower()
    if s == "pendiente":
        return "pending"
    if s == "aprobado":
        return "approved"
    if s == "ignorado":
        return "ignored"
    return "pending"


def _build_sheet_index() -> dict[tuple[str, str, float], int]:
    """Lee el GSheet y devuelve un dict (fecha_dmy, desc_norm, monto_abs) → row_idx."""
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_file("data/gsheet_service_account.json", scopes=scopes)
    gc = gspread.authorize(creds)
    sheet = gc.open_by_key(SPREADSHEET_ID).worksheet(SHEET_NAME)
    rows = sheet.get_all_values()
    out: dict[tuple[str, str, float], int] = {}
    for i, row in enumerate(rows[1:], start=2):
        if len(row) < max(_COL_FECHA, _COL_DESCRIPCION, _COL_MONTO):
            continue
        fecha = row[_COL_FECHA - 1].strip()
        desc_norm = _norm_desc(row[_COL_DESCRIPCION - 1])
        if desc_norm.startswith("'"):
            desc_norm = desc_norm[1:]
        m_raw = row[_COL_MONTO - 1].strip().replace(".", "").replace(",", ".")
        try:
            monto = round(abs(float(m_raw)), 2)
        except (ValueError, TypeError):
            continue
        out[(fecha, desc_norm, monto)] = i
    return out


def _doc_triple(m: dict) -> tuple[str, str, float] | None:
    """Devuelve la triple (fecha_dmy, desc_norm, monto_abs) del documento."""
    from datetime import datetime
    date_iso = (m.get("date") or "").strip()
    try:
        dt = datetime.strptime(date_iso, "%Y-%m-%d")
        fecha = dt.strftime("%d/%m/%Y")
    except Exception:
        return None
    desc_norm = _norm_desc(m.get("description") or "")
    if desc_norm.startswith("'"):
        desc_norm = desc_norm[1:]
    try:
        monto = round(abs(float(m.get("amount") or 0)), 2)
    except Exception:
        return None
    return (fecha, desc_norm, monto)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    print("Leyendo movimientos de Firestore…")
    docs = list(db._db().collection("movements").get())
    print(f"  {len(docs)} documentos.")

    print("Leyendo Google Sheet…")
    sheet_index = _build_sheet_index()
    print(f"  {len(sheet_index)} filas indexadas por triple.")

    counts = {"skipped": 0, "pending": 0, "approved_synced": 0,
              "approved_orphan": 0, "ignored": 0, "errors": 0}
    updates: list[tuple[str, dict]] = []

    for d in docs:
        m = d.to_dict()
        if m.get("review_status"):
            counts["skipped"] += 1
            continue

        review = _legacy_to_review(m.get("status"))
        payload: dict = {
            "review_status": review,
            "version": int(m.get("version") or 1),
            "updated_at": m.get("updated_at") or m.get("inserted_at"),
            "comercio_final": m.get("comercio_final"),
            "comment": m.get("comment"),
            "last_action_source": m.get("last_action_source") or "system",
            "corrected_at": m.get("corrected_at"),
            "corrected_by": m.get("corrected_by"),
            "sheet_row_id": m.get("sheet_row_id"),
            "sync_error_message": m.get("sync_error_message"),
        }

        if review == "approved":
            triple = _doc_triple(m)
            row_idx = sheet_index.get(triple) if triple else None
            if row_idx:
                payload["sheet_sync_status"] = "synced"
                payload["sheet_row_id"] = row_idx
                counts["approved_synced"] += 1
            else:
                payload["sheet_sync_status"] = "sync_error"
                payload["sync_error_message"] = "legacy approved sin fila en GSheet (no se encontró por triple)"
                counts["approved_orphan"] += 1
        elif review == "ignored":
            payload["sheet_sync_status"] = "not_ready"
            counts["ignored"] += 1
        else:
            payload["sheet_sync_status"] = "not_ready"
            counts["pending"] += 1

        updates.append((m["id"], payload))

    print("\nResumen del backfill:")
    for k, v in counts.items():
        print(f"  {k:20} {v}")
    print(f"  {'updates_a_aplicar':20} {len(updates)}")

    if args.dry_run:
        print("\n(dry-run) sample:")
        for mid, p in updates[:5]:
            print(f"  {mid}: review={p['review_status']} sync={p['sheet_sync_status']}")
        return 0

    if not updates:
        print("Nada para escribir.")
        return 0

    print(f"\nAplicando {len(updates)} updates a Firestore (batches de 400)…")
    client = db._db()
    chunk = 400
    for k in range(0, len(updates), chunk):
        batch = client.batch()
        for mid, payload in updates[k : k + chunk]:
            ref = client.collection("movements").document(mid)
            batch.update(ref, payload)
        batch.commit()
        print(f"  commit {min(k+chunk, len(updates))}/{len(updates)}")

    print("✅ Backfill completado.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
