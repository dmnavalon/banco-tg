from __future__ import annotations

from typing import Any

# Conjunto de campos que el dashboard puede leer del documento Firestore. Lo
# explicitamos para evitar exponer accidentalmente datos sensibles si en el
# futuro se agregan campos privados (ej. credenciales pegadas mal a mov).
_FIELDS = (
    "id", "date", "description", "amount", "movement_type", "account", "bank",
    "persona",
    "suggested_category", "suggested_subcategory", "final_category", "final_subcategory",
    "comercio", "comercio_final",
    "confidence", "classifier_source", "tipo", "requiere_revision", "pregunta_sugerida",
    "review_status", "sheet_sync_status", "version",
    "status",  # legacy, útil para debug del dashboard
    "comment", "ignore_reason",
    "decided_by", "decided_at", "corrected_by", "corrected_at",
    "last_action_source", "sheet_row_id", "sync_error_message",
    "cuotas_actual", "cuotas_total", "cuota_monto", "saldo",
    "tg_photo_file_id", "notified_at",
    "inserted_at", "updated_at",
)


def serialize_movement(mov: dict[str, Any]) -> dict[str, Any]:
    """Devuelve un dict JSON-serializable con solo los campos públicos."""
    out: dict[str, Any] = {}
    for f in _FIELDS:
        v = mov.get(f)
        # Firestore Timestamp ya viene como string ISO porque _now() los guarda
        # así. Si llega un datetime real (p.ej. SERVER_TIMESTAMP), to-isoformat.
        if hasattr(v, "isoformat"):
            v = v.isoformat()
        out[f] = v
    return out


def serialize_audit_event(ev: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": ev.get("id"),
        "movement_id": ev.get("movement_id"),
        "action": ev.get("action"),
        "prev_review_status": ev.get("prev_review_status"),
        "new_review_status": ev.get("new_review_status"),
        "prev_sheet_sync_status": ev.get("prev_sheet_sync_status"),
        "new_sheet_sync_status": ev.get("new_sheet_sync_status"),
        "actor": ev.get("actor"),
        "source": ev.get("source"),
        "details": ev.get("details") or {},
        "created_at": ev.get("created_at"),
    }
