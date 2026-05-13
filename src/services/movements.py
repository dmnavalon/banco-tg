from __future__ import annotations

import threading
from typing import Any, Literal

from .. import db
from ..utils import get_logger
from . import audit
from .exceptions import (
    InvalidTransition,
    MovementNotFound,
    ValidationError,
    VersionConflict,
)

log = get_logger("services.movements")

Source = Literal["telegram", "dashboard", "system"]

# review_status legal values
PENDING = "pending"
APPROVED = "approved"
CORRECTED_PENDING = "corrected_pending"
CORRECTED_APPROVED = "corrected_approved"
IGNORED = "ignored"
ERROR = "error"

# sheet_sync_status legal values
NOT_READY = "not_ready"
PENDING_SYNC = "pending_sync"
SYNCED = "synced"
SYNC_ERROR = "sync_error"

# Mapeo dual-write: review_status nuevo → status legacy. El bot legacy y el
# código que aún consulta `status` siguen funcionando mientras los servicios
# nuevos escriben review_status. Hasta que la feature esté validada en prod
# no se elimina la columna legacy.
_LEGACY_STATUS_MAP = {
    PENDING: "pendiente",
    CORRECTED_PENDING: "pendiente",
    APPROVED: "aprobado",
    CORRECTED_APPROVED: "aprobado",
    IGNORED: "ignorado",
    ERROR: "pendiente",
}


# ── Locks de sync GSheet ─────────────────────────────────────────────────
# La sincronización a Google Sheets se hace fuera de la transacción Firestore
# (es I/O externo, distinto sistema). Para evitar que dos approve concurrentes
# del mismo mov escriban dos veces en el sheet (race entre TG y dashboard),
# usamos un lock por mov_id. El dict crece monótonamente — aceptable porque
# está acotado al número de movimientos únicos (~miles, no millones).

_sync_locks: dict[str, threading.Lock] = {}
_sync_locks_guard = threading.Lock()


def _get_sync_lock(mov_id: str) -> threading.Lock:
    with _sync_locks_guard:
        lock = _sync_locks.get(mov_id)
        if lock is None:
            lock = threading.Lock()
            _sync_locks[mov_id] = lock
    return lock


# ── Transition rules ─────────────────────────────────────────────────────

_ALLOWED_TRANSITIONS = {
    "approve":             {PENDING},
    "correct":             {PENDING, CORRECTED_PENDING, CORRECTED_APPROVED, APPROVED},
    "approve_correction":  {CORRECTED_PENDING},
    "ignore":              {PENDING, CORRECTED_PENDING, APPROVED, CORRECTED_APPROVED},
    "reopen":              {APPROVED, CORRECTED_APPROVED, IGNORED, ERROR},
}


def _check_transition(action: str, current: str, mov_id: str) -> None:
    allowed = _ALLOWED_TRANSITIONS.get(action, set())
    if current not in allowed:
        raise InvalidTransition(mov_id, current, action)


def _new_version(data: dict) -> int:
    """version puede no existir en docs legacy — default a 0 antes de incrementar."""
    return int(data.get("version") or 0) + 1


def _check_version(data: dict, expected: int | None, mov_id: str) -> None:
    """Si expected_version es None, omite el chequeo (uso bot, que no maneja
    version). Si está, debe coincidir con la versión actual del doc."""
    if expected is None:
        return
    current = int(data.get("version") or 0)
    if current != int(expected):
        raise VersionConflict(mov_id, expected=int(expected), current=current, current_doc=data)


def _ensure_exists(snap, mov_id: str) -> dict:
    if not snap.exists:
        raise MovementNotFound(mov_id)
    return snap.to_dict()


def _common_update_fields(actor: str, source: Source) -> dict:
    return {
        "updated_at": db._now(),
        "last_action_source": source,
    }


# ── Approve ──────────────────────────────────────────────────────────────


def approve_movement(
    mov_id: str,
    *,
    actor: str,
    source: Source,
    expected_version: int | None = None,
    final_category: str | None = None,
    final_subcategory: str | None = None,
    comercio_final: str | None = None,
    comment: str | None = None,
    skip_sync: bool = False,
) -> dict[str, Any]:
    """pending → approved. Después dispara sync a GSheet.

    Si el mov ya está approved (re-click en TG, doble request en dashboard)
    es idempotente: retorna el doc sin tocar. Esto evita que un doble-click
    fluctúe el state ni gatille una segunda escritura a GSheet."""
    ref = db.get_movement_ref(mov_id)

    def _logic(t):
        snap = next(iter(t.get(ref)))
        data = _ensure_exists(snap, mov_id)

        if data.get("review_status") in {APPROVED, CORRECTED_APPROVED}:
            return data, False

        _check_version(data, expected_version, mov_id)
        _check_transition("approve", data.get("review_status") or PENDING, mov_id)

        cat = final_category or data.get("final_category") or data.get("suggested_category")
        sub = final_subcategory if final_subcategory is not None else (
            data.get("final_subcategory") or data.get("suggested_subcategory")
        )

        new_data = {
            **_common_update_fields(actor, source),
            "review_status": APPROVED,
            "sheet_sync_status": PENDING_SYNC,
            "version": _new_version(data),
            "decided_by": actor,
            "decided_at": db._now(),
            "final_category": cat,
            "final_subcategory": sub,
            "comercio_final": comercio_final or data.get("comercio_final"),
            "comment": comment if comment is not None else data.get("comment"),
            "status": _LEGACY_STATUS_MAP[APPROVED],
        }
        t.update(ref, new_data)
        return {**data, **new_data}, True

    updated, changed = db.run_txn(_logic)

    if changed:
        audit.record_event(
            movement_id=mov_id,
            action=f"approved_from_{source}",
            prev_review_status=PENDING,
            new_review_status=APPROVED,
            prev_sheet_sync_status=NOT_READY,
            new_sheet_sync_status=PENDING_SYNC,
            actor=actor,
            source=source,
            details={"final_category": updated.get("final_category"),
                     "final_subcategory": updated.get("final_subcategory")},
        )
        from .. import screenshot_storage
        screenshot_storage.delete(mov_id)
        if not skip_sync:
            updated = sync_approved_movement_to_sheet(mov_id, actor=actor, source=source)
    return updated


# ── Approve corrected ────────────────────────────────────────────────────


def approve_corrected_movement(
    mov_id: str,
    *,
    actor: str,
    source: Source,
    expected_version: int | None = None,
    skip_sync: bool = False,
) -> dict[str, Any]:
    """corrected_pending → corrected_approved. Dispara sync."""
    ref = db.get_movement_ref(mov_id)

    def _logic(t):
        snap = next(iter(t.get(ref)))
        data = _ensure_exists(snap, mov_id)

        if data.get("review_status") == CORRECTED_APPROVED:
            return data, False

        _check_version(data, expected_version, mov_id)
        _check_transition("approve_correction", data.get("review_status") or PENDING, mov_id)

        new_data = {
            **_common_update_fields(actor, source),
            "review_status": CORRECTED_APPROVED,
            "sheet_sync_status": PENDING_SYNC,
            "version": _new_version(data),
            "decided_by": actor,
            "decided_at": db._now(),
            "status": _LEGACY_STATUS_MAP[CORRECTED_APPROVED],
        }
        t.update(ref, new_data)
        return {**data, **new_data}, True

    updated, changed = db.run_txn(_logic)

    if changed:
        audit.record_event(
            movement_id=mov_id,
            action=f"corrected_approved_from_{source}",
            prev_review_status=CORRECTED_PENDING,
            new_review_status=CORRECTED_APPROVED,
            prev_sheet_sync_status=NOT_READY,
            new_sheet_sync_status=PENDING_SYNC,
            actor=actor,
            source=source,
            details={},
        )
        from .. import screenshot_storage
        screenshot_storage.delete(mov_id)
        if not skip_sync:
            updated = sync_approved_movement_to_sheet(mov_id, actor=actor, source=source)
    return updated


# ── Correct (sin aprobar) ────────────────────────────────────────────────


def correct_movement(
    mov_id: str,
    *,
    actor: str,
    source: Source,
    expected_version: int | None = None,
    final_category: str | None = None,
    final_subcategory: str | None = None,
    comercio_final: str | None = None,
    comment: str | None = None,
    # Re-clasificación de IA (opcional — útil cuando el bot re-clasifica con hint
    # del usuario y queremos persistir las nuevas suggestions).
    suggested_category: str | None = None,
    suggested_subcategory: str | None = None,
    confidence: float | None = None,
    classifier_source: str | None = None,
    comercio: str | None = None,
    tipo: str | None = None,
    requiere_revision: bool | None = None,
    pregunta_sugerida: str | None = None,
) -> dict[str, Any]:
    """pending|corrected_pending|approved|corrected_approved → corrected_pending.

    Si llega desde un estado approved, se "desaprueba" y queda en
    corrected_pending (sheet_sync_status vuelve a not_ready).

    Acepta tanto corrección de la decisión final (final_*) como de la
    sugerencia del clasificador (suggested_*) — el bot las actualiza ambas
    cuando re-clasifica con un hint del usuario."""
    ref = db.get_movement_ref(mov_id)

    def _logic(t):
        snap = next(iter(t.get(ref)))
        data = _ensure_exists(snap, mov_id)

        _check_version(data, expected_version, mov_id)
        current_review = data.get("review_status") or PENDING
        _check_transition("correct", current_review, mov_id)

        new_data: dict[str, Any] = {
            **_common_update_fields(actor, source),
            "review_status": CORRECTED_PENDING,
            "sheet_sync_status": NOT_READY,
            "version": _new_version(data),
            "corrected_by": actor,
            "corrected_at": db._now(),
            "status": _LEGACY_STATUS_MAP[CORRECTED_PENDING],
        }
        if final_category is not None:
            new_data["final_category"] = final_category
        if final_subcategory is not None:
            new_data["final_subcategory"] = final_subcategory
        if comercio_final is not None:
            new_data["comercio_final"] = comercio_final
        if comment is not None:
            new_data["comment"] = comment

        # Persistir re-clasificación si el caller la pasó.
        if suggested_category is not None:
            new_data["suggested_category"] = suggested_category
        if suggested_subcategory is not None:
            new_data["suggested_subcategory"] = suggested_subcategory
        if confidence is not None:
            new_data["confidence"] = confidence
        if classifier_source is not None:
            new_data["classifier_source"] = classifier_source
        if comercio is not None:
            new_data["comercio"] = comercio
        if tipo is not None:
            new_data["tipo"] = tipo
        if requiere_revision is not None:
            new_data["requiere_revision"] = requiere_revision
        if pregunta_sugerida is not None:
            new_data["pregunta_sugerida"] = pregunta_sugerida

        t.update(ref, new_data)
        return {**data, **new_data}, current_review

    updated, prev_status = db.run_txn(_logic)

    audit.record_event(
        movement_id=mov_id,
        action=f"corrected_from_{source}",
        prev_review_status=prev_status,
        new_review_status=CORRECTED_PENDING,
        prev_sheet_sync_status=updated.get("sheet_sync_status"),
        new_sheet_sync_status=NOT_READY,
        actor=actor,
        source=source,
        details={"final_category": updated.get("final_category"),
                 "final_subcategory": updated.get("final_subcategory")},
    )
    return updated


# ── Ignore ───────────────────────────────────────────────────────────────


def ignore_movement(
    mov_id: str,
    *,
    actor: str,
    source: Source,
    reason: str,
    expected_version: int | None = None,
) -> dict[str, Any]:
    """Cualquier estado salvo ignored → ignored. Reason es obligatorio (no vacío
    tras strip)."""
    if not (reason or "").strip():
        raise ValidationError("ignore requiere reason no vacío")

    ref = db.get_movement_ref(mov_id)

    def _logic(t):
        snap = next(iter(t.get(ref)))
        data = _ensure_exists(snap, mov_id)

        if data.get("review_status") == IGNORED:
            return data, None

        _check_version(data, expected_version, mov_id)
        _check_transition("ignore", data.get("review_status") or PENDING, mov_id)

        new_data = {
            **_common_update_fields(actor, source),
            "review_status": IGNORED,
            "sheet_sync_status": NOT_READY,
            "version": _new_version(data),
            "ignore_reason": reason.strip(),
            "decided_by": actor,
            "decided_at": db._now(),
            "status": _LEGACY_STATUS_MAP[IGNORED],
        }
        t.update(ref, new_data)
        return {**data, **new_data}, data.get("review_status")

    updated, prev = db.run_txn(_logic)

    if prev is not None:
        audit.record_event(
            movement_id=mov_id,
            action=f"ignored_from_{source}",
            prev_review_status=prev,
            new_review_status=IGNORED,
            prev_sheet_sync_status=updated.get("sheet_sync_status"),
            new_sheet_sync_status=NOT_READY,
            actor=actor,
            source=source,
            details={"reason": reason[:200]},
        )
        from .. import screenshot_storage
        screenshot_storage.delete(mov_id)
    return updated


# ── Reopen ───────────────────────────────────────────────────────────────


def reopen_movement(
    mov_id: str,
    *,
    actor: str,
    source: Source,
    expected_version: int | None = None,
) -> dict[str, Any]:
    """approved|corrected_approved|ignored|error → pending. Vuelve a la cola
    de revisión y deja sheet_sync_status=not_ready (no se elimina la fila del
    sheet — Diego decide si la borra a mano si correspondía). El campo
    `sheet_row_id` queda como referencia histórica."""
    ref = db.get_movement_ref(mov_id)

    def _logic(t):
        snap = next(iter(t.get(ref)))
        data = _ensure_exists(snap, mov_id)

        if data.get("review_status") == PENDING:
            return data, None

        _check_version(data, expected_version, mov_id)
        _check_transition("reopen", data.get("review_status") or PENDING, mov_id)

        new_data = {
            **_common_update_fields(actor, source),
            "review_status": PENDING,
            "sheet_sync_status": NOT_READY,
            "version": _new_version(data),
            "status": _LEGACY_STATUS_MAP[PENDING],
        }
        t.update(ref, new_data)
        return {**data, **new_data}, data.get("review_status")

    updated, prev = db.run_txn(_logic)

    if prev is not None:
        audit.record_event(
            movement_id=mov_id,
            action="reopened",
            prev_review_status=prev,
            new_review_status=PENDING,
            prev_sheet_sync_status=updated.get("sheet_sync_status"),
            new_sheet_sync_status=NOT_READY,
            actor=actor,
            source=source,
            details={},
        )
    return updated


# ── Sync a Google Sheets ─────────────────────────────────────────────────


def sync_approved_movement_to_sheet(
    mov_id: str,
    *,
    actor: str = "system",
    source: Source = "system",
) -> dict[str, Any]:
    """Toma un mov en (approved|corrected_approved, pending_sync|sync_error)
    y lo escribe en GSheet vía gsheet.upsert_movement. La idempotencia la
    garantiza el upsert (lookup por movement_id en col Y, fallback al triple).

    Tras la escritura:
    - éxito → tx Firestore: sheet_sync_status=synced, sheet_row_id, sync_error_message=None
    - error → tx Firestore: sheet_sync_status=sync_error + sync_error_message

    El lock per-mov_id evita doble escritura concurrente (TG aprueba +
    dashboard aprueba). Lazy import de gsheet para no acoplar el módulo a
    Google Sheets en imports — facilita tests con mockfirestore."""
    from .. import gsheet as _gsheet

    lock = _get_sync_lock(mov_id)
    with lock:
        mov = db.get_movement_by_id(mov_id)
        if not mov:
            raise MovementNotFound(mov_id)

        if mov.get("review_status") not in {APPROVED, CORRECTED_APPROVED}:
            log.warning(f"sync skipped {mov_id}: review_status={mov.get('review_status')}")
            return mov

        ref = db.get_movement_ref(mov_id)
        prev_sync = mov.get("sheet_sync_status")

        try:
            _gsheet.upsert_movement(mov)
        except Exception as e:
            err_msg = f"{type(e).__name__}: {str(e)[:400]}"
            log.exception(f"GSheet sync falló para {mov_id}")
            new_data = {
                "sheet_sync_status": SYNC_ERROR,
                "sync_error_message": err_msg,
                "version": _new_version(mov),
                "updated_at": db._now(),
                "last_action_source": source,
            }
            db._with_retry(lambda: ref.update(new_data))
            audit.record_event(
                movement_id=mov_id,
                action="sync_failed",
                prev_review_status=mov.get("review_status"),
                new_review_status=mov.get("review_status"),
                prev_sheet_sync_status=prev_sync,
                new_sheet_sync_status=SYNC_ERROR,
                actor=actor,
                source=source,
                details={"error": err_msg},
            )
            return {**mov, **new_data}

        # Éxito. No tenemos `sheet_row_id` real porque gsheet.upsert_movement
        # no lo retorna — extender después. Por ahora marcar synced y dejar
        # row_id como hint del último known (None si nunca se supo).
        new_data = {
            "sheet_sync_status": SYNCED,
            "sync_error_message": None,
            "version": _new_version(mov),
            "updated_at": db._now(),
            "last_action_source": source,
        }
        db._with_retry(lambda: ref.update(new_data))
        audit.record_event(
            movement_id=mov_id,
            action="synced_to_sheet",
            prev_review_status=mov.get("review_status"),
            new_review_status=mov.get("review_status"),
            prev_sheet_sync_status=prev_sync,
            new_sheet_sync_status=SYNCED,
            actor=actor,
            source=source,
            details={},
        )
        return {**mov, **new_data}


# ── Bulk operations ──────────────────────────────────────────────────────
# Cada operación bulk itera por id, captura excepciones por item, y devuelve
# un dict con el resultado por id. NO hay transacción multi-mov — cada mov
# es independiente para que un version_conflict en uno no bloquee el resto.


BulkResult = dict[str, dict[str, Any]]


def _bulk_apply(
    ids: list[str],
    versions: dict[str, int] | None,
    fn,
) -> BulkResult:
    out: BulkResult = {}
    for mov_id in ids:
        v = (versions or {}).get(mov_id)
        try:
            result = fn(mov_id, v)
            out[mov_id] = {"status": "ok", "movement": result}
        except VersionConflict as e:
            out[mov_id] = {"status": "conflict", "error": str(e), "current": e.current_doc}
        except (MovementNotFound, InvalidTransition, ValidationError) as e:
            out[mov_id] = {"status": "error", "error": str(e), "kind": type(e).__name__}
        except Exception as e:
            log.exception(f"bulk error en {mov_id}")
            out[mov_id] = {"status": "error", "error": str(e), "kind": type(e).__name__}
    return out


def bulk_approve(
    ids: list[str],
    *,
    actor: str,
    source: Source,
    versions: dict[str, int] | None = None,
) -> BulkResult:
    return _bulk_apply(
        ids, versions,
        lambda mid, v: approve_movement(mid, actor=actor, source=source, expected_version=v),
    )


def bulk_categorize(
    ids: list[str],
    *,
    actor: str,
    source: Source,
    final_category: str,
    final_subcategory: str | None = None,
    versions: dict[str, int] | None = None,
) -> BulkResult:
    if not (final_category or "").strip():
        raise ValidationError("bulk_categorize requiere final_category")
    return _bulk_apply(
        ids, versions,
        lambda mid, v: correct_movement(
            mid, actor=actor, source=source, expected_version=v,
            final_category=final_category, final_subcategory=final_subcategory,
        ),
    )


def bulk_ignore(
    ids: list[str],
    *,
    actor: str,
    source: Source,
    reason: str,
    versions: dict[str, int] | None = None,
) -> BulkResult:
    if not (reason or "").strip():
        raise ValidationError("bulk_ignore requiere reason")
    return _bulk_apply(
        ids, versions,
        lambda mid, v: ignore_movement(mid, actor=actor, source=source, reason=reason, expected_version=v),
    )


def bulk_comment(
    ids: list[str],
    *,
    actor: str,
    source: Source,
    comment: str,
    versions: dict[str, int] | None = None,
) -> BulkResult:
    """Agregar comentario sin cambiar review_status. Hace una update directa
    con version check, sin transición de estado."""
    out: BulkResult = {}
    for mov_id in ids:
        v = (versions or {}).get(mov_id)
        try:
            ref = db.get_movement_ref(mov_id)

            def _logic(t, _v=v):
                snap = next(iter(t.get(ref)))
                data = _ensure_exists(snap, mov_id)
                _check_version(data, _v, mov_id)
                new_data = {
                    "comment": comment,
                    "version": _new_version(data),
                    "updated_at": db._now(),
                    "last_action_source": source,
                }
                t.update(ref, new_data)
                return {**data, **new_data}

            updated = db.run_txn(_logic)
            audit.record_event(
                movement_id=mov_id,
                action=f"commented_from_{source}",
                prev_review_status=updated.get("review_status"),
                new_review_status=updated.get("review_status"),
                prev_sheet_sync_status=updated.get("sheet_sync_status"),
                new_sheet_sync_status=updated.get("sheet_sync_status"),
                actor=actor,
                source=source,
                details={"comment": comment[:200]},
            )
            out[mov_id] = {"status": "ok", "movement": updated}
        except VersionConflict as e:
            out[mov_id] = {"status": "conflict", "error": str(e), "current": e.current_doc}
        except (MovementNotFound, ValidationError) as e:
            out[mov_id] = {"status": "error", "error": str(e), "kind": type(e).__name__}
        except Exception as e:
            log.exception(f"bulk_comment error en {mov_id}")
            out[mov_id] = {"status": "error", "error": str(e), "kind": type(e).__name__}
    return out


def bulk_reopen(
    ids: list[str],
    *,
    actor: str,
    source: Source,
    versions: dict[str, int] | None = None,
) -> BulkResult:
    return _bulk_apply(
        ids, versions,
        lambda mid, v: reopen_movement(mid, actor=actor, source=source, expected_version=v),
    )
