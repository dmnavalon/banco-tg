"""Extiende el header del Google Sheet para incluir la columna 'MovementId'
en la posición 25 (col Y). Idempotente: si ya existe, no hace nada.

Después del header update, intenta backfillar el id en filas existentes que
tengan match exacto contra Firestore por triple (fecha, descripción, monto).
Las que no matcheen quedan con la celda vacía — el sync sucesivo via la
feature las completará automáticamente al hacer upsert.

Uso:
    cd "Gestión de Gastos"
    .venv/bin/python -m scripts.extend_sheet_movement_id           # ejecuta
    .venv/bin/python -m scripts.extend_sheet_movement_id --dry-run # reporta
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
    SHEET_HEADER,
    SHEET_NAME,
    SPREADSHEET_ID,
    _COL_DESCRIPCION,
    _COL_FECHA,
    _COL_MONTO,
    _COL_MOVEMENT_ID,
    _LETTER_MOVEMENT_ID,
    _norm_desc,
)


def _open_sheet():
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_file("data/gsheet_service_account.json", scopes=scopes)
    gc = gspread.authorize(creds)
    return gc.open_by_key(SPREADSHEET_ID).worksheet(SHEET_NAME)


def _ensure_header(sheet, dry_run: bool) -> bool:
    """Garantiza que la col 25 del header sea 'MovementId'. Devuelve True si
    el header ya estaba/quedó correcto, False si hay que abortar."""
    current = sheet.row_values(1)
    print(f"Header actual ({len(current)} cols): {current}")

    if len(current) >= _COL_MOVEMENT_ID and current[_COL_MOVEMENT_ID - 1] == "MovementId":
        print("✅ Header ya tiene MovementId en col 25 (Y). Nada que hacer en header.")
        return True

    if len(current) >= _COL_MOVEMENT_ID and current[_COL_MOVEMENT_ID - 1] not in ("", "MovementId"):
        print(f"\n⚠️  Col 25 ya está ocupada por '{current[_COL_MOVEMENT_ID - 1]}'.")
        print("    Mueve esa columna al final manualmente y reintenta.")
        return False

    print(f"\nAgregando 'MovementId' en col {_COL_MOVEMENT_ID} ({_LETTER_MOVEMENT_ID}, fila 1)…")
    if dry_run:
        print("  (dry-run, no se escribió)")
        return True

    sheet.update(f"{_LETTER_MOVEMENT_ID}1", [["MovementId"]], value_input_option="RAW")
    print("✅ Header extendido.")
    return True


def _backfill_ids(sheet, dry_run: bool) -> None:
    """Backfillea movement_id en filas existentes via match por triple."""
    print("\nLeyendo filas existentes del sheet…")
    all_rows = sheet.get_all_values()
    if len(all_rows) <= 1:
        print("Sheet vacío — nada que backfillar.")
        return
    print(f"  {len(all_rows) - 1} filas de datos.")

    print("Indexando movimientos de Firestore por (fecha, desc, monto)…")
    docs = db._db().collection("movements").get()
    by_triple: dict[tuple[str, str, float], str] = {}
    for d in docs:
        m = d.to_dict()
        date_iso = (m.get("date") or "").strip()
        try:
            from datetime import datetime
            dt = datetime.strptime(date_iso, "%Y-%m-%d")
            fecha_dmy = dt.strftime("%d/%m/%Y")
        except Exception:
            continue
        desc_norm = _norm_desc(m.get("description") or "")
        if desc_norm.startswith("'"):
            desc_norm = desc_norm[1:]
        try:
            monto = abs(float(m.get("amount") or 0))
        except Exception:
            continue
        by_triple[(fecha_dmy, desc_norm, round(monto, 2))] = m["id"]
    print(f"  {len(by_triple)} movimientos indexados.")

    updates: list[tuple[int, str]] = []
    not_matched = 0

    for i, row in enumerate(all_rows[1:], start=2):
        if len(row) >= _COL_MOVEMENT_ID:
            current_id = (row[_COL_MOVEMENT_ID - 1] or "").strip()
            if current_id and current_id != "MovementId":
                continue  # ya tiene id

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

        mov_id = by_triple.get((fecha, desc_norm, monto))
        if mov_id:
            updates.append((i, mov_id))
        else:
            not_matched += 1

    print(f"\nMatchear contra Firestore:")
    print(f"  {len(updates)} filas listas para backfillar movement_id")
    print(f"  {not_matched} filas sin match (se ignoran)")

    if dry_run:
        print("\n(dry-run) sample de updates:")
        for row_idx, mid in updates[:10]:
            print(f"  row {row_idx} → {mid}")
        return

    if not updates:
        print("Nada para escribir.")
        return

    # Aplicar en batch para no consumir cuota — un range por fila.
    batch = [
        {
            "range": f"{_LETTER_MOVEMENT_ID}{row_idx}",
            "values": [[mid]],
        }
        for row_idx, mid in updates
    ]
    print(f"\nEscribiendo {len(batch)} celdas en col {_LETTER_MOVEMENT_ID}…")
    # gspread.batch_update acepta hasta cierto límite — chunking por seguridad.
    chunk = 500
    for k in range(0, len(batch), chunk):
        sheet.batch_update(batch[k : k + chunk], value_input_option="RAW")
    print("✅ Backfill completado.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="No escribe, solo reporta.")
    parser.add_argument("--skip-backfill", action="store_true", help="Solo extiende el header, no backfillea ids.")
    args = parser.parse_args()

    sheet = _open_sheet()
    if not _ensure_header(sheet, args.dry_run):
        return 1

    if not args.skip_backfill:
        _backfill_ids(sheet, args.dry_run)

    return 0


if __name__ == "__main__":
    sys.exit(main())
