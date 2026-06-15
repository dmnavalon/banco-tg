"""Espejo Firestore de sheets_writer: escribe inversiones_maestro e
inversiones_snapshots. El dashboard lee Firestore (fuente única desde la
migración 2026-06); el Google Sheet queda como respaldo. El runner llama a
ambos writers en cada corrida.

Identidad: maestro por `id`; snapshot por par (mes, id). El runner sobrescribe
el row del mes actual cada corrida; los meses pasados quedan inmutables.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from .. import db
from ..utils import get_logger
from .adapters.base import Holding

log = get_logger("patrimonio.firestore_writer")


def upsert_maestro(maestro_dict: dict) -> None:
    """Upsert idempotente del maestro de un sitio. Mismo dict que produce
    `adapter.maestro_row()`."""
    db.set_inversion_maestro(maestro_dict["id"], {
        "activo": maestro_dict.get("activo", ""),
        "clase": maestro_dict.get("clase", ""),
        "subclase": maestro_dict.get("subclase", ""),
        "moneda": maestro_dict.get("moneda", "CLP"),
        "pais": maestro_dict.get("pais", ""),
        "institucion": maestro_dict.get("institucion", ""),
        "liquidez": maestro_dict.get("liquidez", ""),
        "fecha_inicio": maestro_dict.get("fecha_inicio", ""),
        "activa": bool(maestro_dict.get("activa", True)),
    })
    log.info("inversiones_maestro upsert: %s", maestro_dict.get("id"))


def upsert_snapshot(holding: Holding, mes: Optional[str] = None) -> None:
    """Upsert del snapshot (mes, id). `mes` por defecto = YYYY-MM de holding.fecha."""
    if mes is None:
        mes = holding.fecha.strftime("%Y-%m")
    db.set_inversion_snapshot(mes, holding.inversion_id, {
        "aportes_del_mes": 0,
        "retiros_del_mes": 0,
        "valor_moneda_orig": holding.valor_moneda_orig,
        "tipo_cambio_cierre": holding.tipo_cambio,
        "valor_clp": holding.valor_clp,
        "notas": holding.notas_para_sheet(),
    })
    log.info("inversiones_snapshots upsert: %s/%s = %s", mes, holding.inversion_id, holding.valor_clp)


def mark_snapshot_error(inversion_id: str, error: str, mes: Optional[str] = None) -> None:
    """Marca error en el snapshot del mes sin tocar el último valor conocido
    (merge solo notas). Espejo de sheets_writer.mark_snapshot_error."""
    if mes is None:
        mes = datetime.now().strftime("%Y-%m")
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")
    note = f"act:{ts} · scraper:error · {error[:120]}"
    db.set_inversion_snapshot_notas(mes, inversion_id, note)
    log.info("inversiones_snapshots error marcado: %s/%s — %s", mes, inversion_id, error)
