from __future__ import annotations

from typing import Any, Literal

from .. import db

Source = Literal["telegram", "dashboard", "system"]

_COLLECTION = "movement_audit"


def record_event(
    *,
    movement_id: str,
    action: str,
    prev_review_status: str | None,
    new_review_status: str | None,
    prev_sheet_sync_status: str | None,
    new_sheet_sync_status: str | None,
    actor: str,
    source: Source,
    details: dict[str, Any] | None = None,
) -> str:
    """Registra un evento de auditoría. Devuelve el id del documento creado.

    Se llama después de cada transición exitosa (approve, correct, ignore,
    reopen, sync). Errores se registran como acciones `*_failed` con el
    mensaje en `details`."""
    client = db._db()
    ref = client.collection(_COLLECTION).document()
    payload = {
        "movement_id": movement_id,
        "action": action,
        "prev_review_status": prev_review_status,
        "new_review_status": new_review_status,
        "prev_sheet_sync_status": prev_sheet_sync_status,
        "new_sheet_sync_status": new_sheet_sync_status,
        "actor": actor,
        "source": source,
        "details": details or {},
        "created_at": db._now(),
    }
    db._with_retry(lambda: ref.set(payload))
    return ref.id


def list_for_movement(movement_id: str, limit: int = 100) -> list[dict[str, Any]]:
    """Devuelve los eventos de un movimiento ordenados desc por created_at."""
    docs = (
        db._db().collection(_COLLECTION)
        .where("movement_id", "==", movement_id)
        .limit(limit)
        .get()
    )
    rows = [d.to_dict() | {"id": d.id} for d in docs]
    rows.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return rows
