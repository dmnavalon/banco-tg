from __future__ import annotations

from typing import Any

from flask import Blueprint, jsonify, request
from google.api_core import exceptions as gax_exceptions

from ... import db
from ...services import audit, movements as movement_service
from ...services.exceptions import (
    InvalidTransition,
    MovementNotFound,
    ValidationError,
    VersionConflict,
)
from ...classifier import get_taxonomy_meta
from ..auth import require_token
from ..serializers import serialize_audit_event, serialize_movement

bp = Blueprint("movements", __name__, url_prefix="/api/movements")


def _parse_status_param(raw: str | None) -> str | list[str] | None:
    """`status` viene como query string. Acepta:
    - "pending" (uno solo)
    - "pending,corrected_pending" (varios coma-separados)
    - "all" / vacío → None (sin filtro)"""
    if not raw or raw == "all":
        return None
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if len(parts) == 1:
        return parts[0]
    return parts


def _parse_float(raw: str | None) -> float | None:
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _parse_int(raw: str | None, default: int) -> int:
    try:
        return int(raw) if raw not in (None, "") else default
    except ValueError:
        return default


def _expected_version(payload: dict[str, Any]) -> int | None:
    """Extrae version del payload. Si no viene, salta el check (None).
    El dashboard SIEMPRE debería enviarla; bot/sistema pueden omitirla."""
    v = payload.get("version")
    if v is None or v == "":
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _conflict_response(exc: VersionConflict):
    return jsonify({
        "error": "version_conflict",
        "expected": exc.expected,
        "current": exc.current,
        "current_movement": serialize_movement(exc.current_doc),
    }), 409


def _service_error_response(exc: Exception):
    if isinstance(exc, VersionConflict):
        return _conflict_response(exc)
    if isinstance(exc, MovementNotFound):
        return jsonify({"error": "not_found"}), 404
    if isinstance(exc, InvalidTransition):
        return jsonify({"error": "invalid_transition", "message": str(exc),
                        "current": exc.current, "attempted": exc.attempted}), 422
    if isinstance(exc, ValidationError):
        return jsonify({"error": "validation_error", "message": str(exc)}), 422
    return jsonify({"error": "internal", "message": str(exc)}), 500


# ── List + detail ────────────────────────────────────────────────────────


def _parse_bool(raw: str | None) -> bool | None:
    if raw is None or raw == "":
        return None
    return raw.strip().lower() in ("true", "1", "yes")


@bp.get("")
@require_token
def list_movements():
    args = request.args
    status_filter = _parse_status_param(args.get("status"))
    # `tipo_movimiento` y `excluido` son campos CALCULADOS por el serializer
    # (no columnas Firestore), así que se filtran en Python tras serializar. Si
    # vienen, subimos el fetch interno para que el cap no trunque antes de filtrar.
    tipo_mov_raw = args.get("tipo_movimiento")
    tipos_wanted = {t.strip() for t in (tipo_mov_raw or "").split(",") if t.strip()}
    excluido_want = _parse_bool(args.get("excluido"))
    user_limit = _parse_int(args.get("limit"), 100)
    fetch_limit = 500 if (tipos_wanted or excluido_want is not None) else user_limit
    try:
        rows = db.query_movements(
            review_status=status_filter,
            date_from=args.get("from"),
            date_to=args.get("to"),
            bank=args.get("bank"),
            persona=args.get("persona"),
            final_category=args.get("categoria"),
            final_subcategory=args.get("subcategoria"),
            min_amount=_parse_float(args.get("min_amount")),
            max_amount=_parse_float(args.get("max_amount")),
            confidence_min=_parse_float(args.get("confidence_min")),
            description_contains=args.get("q"),
            comercio_contains=args.get("comercio"),
            limit=fetch_limit,
        )
    except gax_exceptions.FailedPrecondition as exc:
        # Combinación de filtros que exige un índice compuesto inexistente.
        # El mensaje de Firestore incluye el link de creación; los índices
        # vigentes viven en firestore.indexes.json (raíz) y se despliegan con
        # `firebase deploy --only firestore:indexes`.
        return jsonify({"error": "missing_index", "message": str(exc)}), 422
    meta = get_taxonomy_meta()
    items = [serialize_movement(r, meta) for r in rows]
    if tipos_wanted:
        items = [it for it in items if it["tipo_movimiento"] in tipos_wanted]
    if excluido_want is not None:
        items = [it for it in items if bool(it.get("excluido")) == excluido_want]
    if fetch_limit != user_limit:
        items = items[:user_limit] if user_limit else items
    return jsonify({"items": items, "count": len(items)})


@bp.get("/export")
@require_token
def export_movements():
    """Todos los movimientos no-ignorados, sin cap, para el cálculo de KPIs del
    dashboard. Una sola lectura full-collection por carga (revalidate corto en
    el lado Next evita martillar Firestore)."""
    rows = db.export_movements(exclude_ignored=True)
    meta = get_taxonomy_meta()
    return jsonify({"items": [serialize_movement(r, meta) for r in rows], "count": len(rows)})


@bp.get("/<mov_id>")
@require_token
def get_movement(mov_id: str):
    mov = db.get_movement_by_id(mov_id)
    if not mov:
        return jsonify({"error": "not_found"}), 404
    return jsonify({"movement": serialize_movement(mov)})


@bp.get("/<mov_id>/audit")
@require_token
def get_audit(mov_id: str):
    events = audit.list_for_movement(mov_id)
    return jsonify({"events": [serialize_audit_event(e) for e in events]})


# ── Single mutations ─────────────────────────────────────────────────────


@bp.post("/<mov_id>/approve")
@require_token
def approve(mov_id: str):
    payload = request.get_json(silent=True) or {}
    actor = payload.get("actor") or "dashboard"
    try:
        updated = movement_service.approve_movement(
            mov_id,
            actor=actor,
            source="dashboard",
            expected_version=_expected_version(payload),
            final_category=payload.get("final_category"),
            final_subcategory=payload.get("final_subcategory"),
            comercio_final=payload.get("comercio_final"),
            comment=payload.get("comment"),
        )
    except Exception as e:
        return _service_error_response(e)
    return jsonify({"movement": serialize_movement(updated)})


@bp.post("/<mov_id>/correct")
@require_token
def correct(mov_id: str):
    payload = request.get_json(silent=True) or {}
    actor = payload.get("actor") or "dashboard"
    final_category = payload.get("final_category")
    if not final_category:
        return jsonify({"error": "validation_error", "message": "final_category requerida"}), 422
    try:
        updated = movement_service.correct_movement(
            mov_id,
            actor=actor,
            source="dashboard",
            expected_version=_expected_version(payload),
            final_category=final_category,
            final_subcategory=payload.get("final_subcategory"),
            comercio_final=payload.get("comercio_final"),
            comment=payload.get("comment"),
        )
    except Exception as e:
        return _service_error_response(e)
    return jsonify({"movement": serialize_movement(updated)})


@bp.post("/<mov_id>/approve-correction")
@require_token
def approve_correction(mov_id: str):
    payload = request.get_json(silent=True) or {}
    actor = payload.get("actor") or "dashboard"
    try:
        updated = movement_service.approve_corrected_movement(
            mov_id,
            actor=actor,
            source="dashboard",
            expected_version=_expected_version(payload),
        )
    except Exception as e:
        return _service_error_response(e)
    return jsonify({"movement": serialize_movement(updated)})


@bp.post("/<mov_id>/ignore")
@require_token
def ignore(mov_id: str):
    payload = request.get_json(silent=True) or {}
    reason = (payload.get("reason") or "").strip()
    if not reason:
        return jsonify({"error": "validation_error", "message": "reason obligatorio"}), 422
    actor = payload.get("actor") or "dashboard"
    try:
        updated = movement_service.ignore_movement(
            mov_id,
            actor=actor,
            source="dashboard",
            reason=reason,
            expected_version=_expected_version(payload),
        )
    except Exception as e:
        return _service_error_response(e)
    return jsonify({"movement": serialize_movement(updated)})


@bp.post("/<mov_id>/reopen")
@require_token
def reopen(mov_id: str):
    payload = request.get_json(silent=True) or {}
    actor = payload.get("actor") or "dashboard"
    try:
        updated = movement_service.reopen_movement(
            mov_id,
            actor=actor,
            source="dashboard",
            expected_version=_expected_version(payload),
        )
    except Exception as e:
        return _service_error_response(e)
    return jsonify({"movement": serialize_movement(updated)})


@bp.post("/<mov_id>/flags")
@require_token
def update_flags(mov_id: str):
    """Edita los campos manuales (moneda, monto_clp, esencial, fijo,
    recurrente, extraordinario, excluido, notas) sin tocar review_status.
    Payload: {"fields": {...}, "version": n, "actor": "..."}. Un valor null
    limpia el campo (el dashboard vuelve a derivar su default)."""
    payload = request.get_json(silent=True) or {}
    fields = payload.get("fields")
    if not isinstance(fields, dict) or not fields:
        return jsonify({"error": "validation_error", "message": "fields (dict) requerido"}), 422
    actor = payload.get("actor") or "dashboard"
    try:
        updated = movement_service.update_manual_fields(
            mov_id,
            actor=actor,
            source="dashboard",
            fields=fields,
            expected_version=_expected_version(payload),
        )
    except Exception as e:
        return _service_error_response(e)
    return jsonify({"movement": serialize_movement(updated)})


@bp.post("/<mov_id>/sync")
@require_token
def retry_sync(mov_id: str):
    """Reintenta sincronizar a GSheet. Útil cuando un mov quedó en sync_error
    y queremos forzar un retry desde el dashboard."""
    try:
        updated = movement_service.sync_approved_movement_to_sheet(
            mov_id, actor="dashboard", source="dashboard",
        )
    except Exception as e:
        return _service_error_response(e)
    return jsonify({"movement": serialize_movement(updated)})


# ── Bulk ─────────────────────────────────────────────────────────────────


def _bulk_payload() -> tuple[list[str], dict[str, int], dict[str, Any]]:
    p = request.get_json(silent=True) or {}
    ids = p.get("ids") or []
    if not isinstance(ids, list):
        ids = []
    versions = p.get("versions") or {}
    if not isinstance(versions, dict):
        versions = {}
    # Cast versiones a int donde se pueda.
    versions_int: dict[str, int] = {}
    for k, v in versions.items():
        try:
            versions_int[str(k)] = int(v)
        except (TypeError, ValueError):
            continue
    return ids, versions_int, p


def _serialize_bulk(results: dict[str, dict[str, Any]]):
    out = {}
    for mid, r in results.items():
        item = {"status": r.get("status")}
        if "movement" in r:
            item["movement"] = serialize_movement(r["movement"])
        if "current" in r:
            item["current_movement"] = serialize_movement(r["current"])
        if "error" in r:
            item["error"] = r["error"]
        if "kind" in r:
            item["kind"] = r["kind"]
        out[mid] = item
    return out


@bp.post("/bulk/approve")
@require_token
def bulk_approve_route():
    ids, versions, payload = _bulk_payload()
    actor = payload.get("actor") or "dashboard"
    results = movement_service.bulk_approve(
        ids, actor=actor, source="dashboard", versions=versions,
    )
    return jsonify({"results": _serialize_bulk(results)})


@bp.post("/bulk/categorize")
@require_token
def bulk_categorize_route():
    ids, versions, payload = _bulk_payload()
    actor = payload.get("actor") or "dashboard"
    final_category = payload.get("final_category")
    if not final_category:
        return jsonify({"error": "validation_error", "message": "final_category requerida"}), 422
    try:
        results = movement_service.bulk_categorize(
            ids, actor=actor, source="dashboard",
            final_category=final_category,
            final_subcategory=payload.get("final_subcategory"),
            versions=versions,
        )
    except ValidationError as e:
        return jsonify({"error": "validation_error", "message": str(e)}), 422
    return jsonify({"results": _serialize_bulk(results)})


@bp.post("/bulk/ignore")
@require_token
def bulk_ignore_route():
    ids, versions, payload = _bulk_payload()
    reason = (payload.get("reason") or "").strip()
    if not reason:
        return jsonify({"error": "validation_error", "message": "reason obligatorio"}), 422
    actor = payload.get("actor") or "dashboard"
    try:
        results = movement_service.bulk_ignore(
            ids, actor=actor, source="dashboard", reason=reason, versions=versions,
        )
    except ValidationError as e:
        return jsonify({"error": "validation_error", "message": str(e)}), 422
    return jsonify({"results": _serialize_bulk(results)})


@bp.post("/bulk/comment")
@require_token
def bulk_comment_route():
    ids, versions, payload = _bulk_payload()
    comment = payload.get("comment")
    if comment is None:
        return jsonify({"error": "validation_error", "message": "comment requerido"}), 422
    actor = payload.get("actor") or "dashboard"
    results = movement_service.bulk_comment(
        ids, actor=actor, source="dashboard", comment=comment, versions=versions,
    )
    return jsonify({"results": _serialize_bulk(results)})


@bp.post("/bulk/reopen")
@require_token
def bulk_reopen_route():
    ids, versions, payload = _bulk_payload()
    actor = payload.get("actor") or "dashboard"
    results = movement_service.bulk_reopen(
        ids, actor=actor, source="dashboard", versions=versions,
    )
    return jsonify({"results": _serialize_bulk(results)})
