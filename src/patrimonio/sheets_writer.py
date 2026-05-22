"""Escritura idempotente a las hojas Inversiones_Maestro e Inversiones_Snapshot.

Reusa el cliente gspread de `src.gsheet`. Mismo SPREADSHEET_ID.

Convención de identidad:
- Inversiones_Maestro: el `id` (col A) es la clave única.
- Inversiones_Snapshot: el par `(mes, id)` es la clave única. `mes` es un
  string YYYY-MM (ej. "2026-05"). El runner sobreescribe el row del mes
  actual cada corrida; el del mes pasado queda inmutable como histórico.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

import gspread

from ..gsheet import _client, SPREADSHEET_ID, _safe_text
from ..utils import get_logger
from .adapters.base import Holding

log = get_logger("patrimonio.sheets_writer")

SHEET_MAESTRO = "Inversiones_Maestro"
SHEET_SNAPSHOT = "Inversiones_Snapshot"

# Columnas (1-indexed) según lib/sheets.ts:328-363 del dashboard
# Inversiones_Maestro: id, activo, clase, subclase, moneda, pais, institucion, liquidez, fechaInicio, activa
COL_M_ID = 1
COL_M_ACTIVO = 2
COL_M_CLASE = 3
COL_M_SUBCLASE = 4
COL_M_MONEDA = 5
COL_M_PAIS = 6
COL_M_INSTITUCION = 7
COL_M_LIQUIDEZ = 8
COL_M_FECHA_INICIO = 9
COL_M_ACTIVA = 10

# Inversiones_Snapshot: mes, id, aportes, retiros, valorMonedaOrig, tipoCambio, valorCLP, notas
COL_S_MES = 1
COL_S_ID = 2
COL_S_APORTES = 3
COL_S_RETIROS = 4
COL_S_VALOR_ORIG = 5
COL_S_TIPO_CAMBIO = 6
COL_S_VALOR_CLP = 7
COL_S_NOTAS = 8


def _open_or_create_worksheet(client: gspread.Client, name: str, headers: list[str]) -> gspread.Worksheet:
    """Abre la hoja. Si no existe, la crea con headers. NO machaca headers existentes."""
    spreadsheet = client.open_by_key(SPREADSHEET_ID)
    try:
        return spreadsheet.worksheet(name)
    except gspread.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title=name, rows=200, cols=len(headers) + 2)
        ws.update("A1", [headers])
        log.info("Hoja %s creada con headers.", name)
        return ws


def _maestro_headers() -> list[str]:
    return ["id", "activo", "clase", "subclase", "moneda", "pais",
            "institucion", "liquidez", "fechaInicio", "activa"]


def _snapshot_headers() -> list[str]:
    return ["mes", "id", "aportesDelMes", "retirosDelMes",
            "valorMonedaOrig", "tipoCambioCierre", "valorCLP", "notas"]


def ensure_maestro_row(maestro_dict: dict) -> None:
    """Append a Inversiones_Maestro si el id no existe ya. Idempotente."""
    client = _client()
    ws = _open_or_create_worksheet(client, SHEET_MAESTRO, _maestro_headers())
    existing = ws.col_values(COL_M_ID)  # incluye header
    target_id = maestro_dict["id"]
    if target_id in existing[1:]:
        return
    row = [
        _safe_text(maestro_dict["id"]),
        _safe_text(maestro_dict["activo"]),
        _safe_text(maestro_dict["clase"]),
        _safe_text(maestro_dict["subclase"]),
        _safe_text(maestro_dict["moneda"]),
        _safe_text(maestro_dict["pais"]),
        _safe_text(maestro_dict["institucion"]),
        _safe_text(maestro_dict["liquidez"]),
        maestro_dict["fecha_inicio"],
        "TRUE" if maestro_dict.get("activa", True) else "FALSE",
    ]
    ws.append_row(row, value_input_option="USER_ENTERED")
    log.info("Inversiones_Maestro: alta %s", target_id)


def upsert_snapshot(holding: Holding, mes: Optional[str] = None) -> None:
    """Upsert del row (mes, id) en Inversiones_Snapshot.

    `mes` por defecto = YYYY-MM del campo `holding.fecha`. Si pasas otro,
    sobrescribe el row de ese mes (útil para `edit` manual con fecha pasada).
    """
    client = _client()
    ws = _open_or_create_worksheet(client, SHEET_SNAPSHOT, _snapshot_headers())
    if mes is None:
        mes = holding.fecha.strftime("%Y-%m")

    all_rows = ws.get_all_values()
    target_id = holding.inversion_id
    target_mes = mes

    target_row_idx: Optional[int] = None
    for i, row in enumerate(all_rows[1:], start=2):
        if len(row) < max(COL_S_MES, COL_S_ID):
            continue
        if row[COL_S_MES - 1].strip() == target_mes and row[COL_S_ID - 1].strip() == target_id:
            target_row_idx = i
            break

    values = [
        _safe_text(target_mes),
        _safe_text(target_id),
        0,                                  # aportes
        0,                                  # retiros
        holding.valor_moneda_orig,
        holding.tipo_cambio,
        holding.valor_clp,
        _safe_text(holding.notas_para_sheet()),
    ]

    if target_row_idx is None:
        ws.append_row(values, value_input_option="USER_ENTERED")
        log.info("Inversiones_Snapshot append: %s/%s = %s", target_mes, target_id, holding.valor_clp)
    else:
        # Update A:H del row
        ws.update(
            range_name=f"A{target_row_idx}:H{target_row_idx}",
            values=[values],
            value_input_option="USER_ENTERED",
        )
        log.info("Inversiones_Snapshot update row %d: %s/%s = %s",
                 target_row_idx, target_mes, target_id, holding.valor_clp)


def mark_snapshot_error(inversion_id: str, error: str, mes: Optional[str] = None) -> None:
    """Marca un error sin tocar el valor (lo que estaba sigue ahí).

    Caso de uso: la corrida del Domingo falló para Fintual; queremos que el
    dashboard muestre el badge de error pero conservando el último valor
    conocido del scraper exitoso anterior.
    """
    client = _client()
    ws = _open_or_create_worksheet(client, SHEET_SNAPSHOT, _snapshot_headers())
    if mes is None:
        mes = datetime.now().strftime("%Y-%m")

    all_rows = ws.get_all_values()
    for i, row in enumerate(all_rows[1:], start=2):
        if len(row) < max(COL_S_MES, COL_S_ID):
            continue
        if row[COL_S_MES - 1].strip() == mes and row[COL_S_ID - 1].strip() == inversion_id:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M")
            new_notes = f"act:{ts} · scraper:error · {error[:120]}"
            ws.update(
                range_name=f"H{i}",
                values=[[_safe_text(new_notes)]],
                value_input_option="USER_ENTERED",
            )
            log.info("Snapshot error marcado: %s/%s — %s", mes, inversion_id, error)
            return
    log.warning("No había row previo para marcar error: %s/%s", mes, inversion_id)
