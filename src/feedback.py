from __future__ import annotations

import re

from . import classifier, db, telegram_notify
from .services import movements as movement_service
from .services.exceptions import ServiceError
from .utils import get_logger, normalize

log = get_logger("feedback")


def _last_batch_ids() -> list[str]:
    payload = db.get_last_batch_payload()
    if not payload:
        return []
    return [p.strip() for p in payload.split(",") if p.strip()]


def apply(text: str, chat_id: str) -> str:
    """Procesa un mensaje libre del usuario contra el último batch.

    Devuelve un mensaje en español describiendo el resultado para enviar a TG.
    Toda la decisión pasa por la capa de servicios (services.movements) que
    maneja status dual, version, audit y sync GSheet.
    """
    text = (text or "").strip()
    if not text:
        return "Mensaje vacío."

    lower = text.lower()
    ids = _last_batch_ids()

    if lower in {"todo ok", "todos ok"}:
        if not ids:
            return "No hay batch reciente al cual aplicar 'todo ok'."
        return _approve_all(ids, chat_id)

    if not ids:
        return "No hay batch reciente. Manda /pending para reenviar."

    m = re.match(r"^(\d+)\s+(.+)$", text)
    if not m:
        return ("No entendí. Formatos: «1 ok», «2 supermercado», "
                "«2 alimentacion/restaurant», «3 ignorar», «todo ok».")

    try:
        idx = int(m.group(1))
    except ValueError:
        return "Número inválido."

    if idx < 1 or idx > len(ids):
        return f"Índice fuera de rango. El último batch tiene {len(ids)} ítems."

    mov_id = ids[idx - 1]
    rest = m.group(2).strip()
    rest_lower = rest.lower()

    movs = db.get_movements_by_ids([mov_id])
    if not movs:
        return f"No encontré el movimiento #{idx} en la base."

    mov = movs[0]
    review = mov.get("review_status") or ("approved" if mov.get("status") == "aprobado" else "pending")

    # Solo se permite decidir movimientos pendientes/corrected_pending. Si fue
    # aprobado/ignorado antes (ej. el usuario revisita un ignorado vía /pending),
    # informar y no hacer nada.
    if rest_lower in {"ok", "ok.", "si", "sí", "ignorar", "ignora", "skip"}:
        if review not in {"pending", "corrected_pending"}:
            return f"#{idx} ya está {review}, no lo modifico."

    if rest_lower in {"ok", "ok.", "si", "sí"}:
        try:
            if review == "corrected_pending":
                updated = movement_service.approve_corrected_movement(
                    mov_id, actor=chat_id, source="telegram", expected_version=None,
                )
            else:
                updated = movement_service.approve_movement(
                    mov_id, actor=chat_id, source="telegram", expected_version=None,
                )
        except ServiceError as e:
            log.exception(f"feedback approve {mov_id} falló")
            return f"⚠️ No pude aprobar #{idx}: {type(e).__name__}: {str(e)[:120]}"

        cat = updated.get("final_category") or "?"
        sub = updated.get("final_subcategory")
        sync = updated.get("sheet_sync_status")
        suffix = "" if sync == "synced" else f" (sync: {sync})"
        return f"OK #{idx}: {cat}{('/' + sub) if sub else ''} ✓{suffix}"

    if rest_lower in {"ignorar", "ignora", "skip"}:
        try:
            movement_service.ignore_movement(
                mov_id,
                actor=chat_id,
                source="telegram",
                reason="(sin razón)",
                expected_version=None,
            )
        except ServiceError as e:
            log.exception(f"feedback ignore {mov_id} falló")
            return f"⚠️ No pude ignorar #{idx}: {type(e).__name__}: {str(e)[:120]}"
        return f"Ignorado #{idx} ✓"

    # Texto libre como hint: re-clasificar con el LLM y reenviar tarjeta para confirmar.
    try:
        cls = classifier.classify_with_hint(
            description=mov.get("description", ""),
            amount=float(mov.get("amount") or 0),
            hint=rest,
        )
    except Exception as e:
        log.exception("classify_with_hint falló")
        return f"No pude reclasificar #{idx}: {type(e).__name__}: {e}"

    try:
        movement_service.correct_movement(
            mov_id,
            actor=chat_id,
            source="telegram",
            expected_version=None,
            final_category=cls.category,
            final_subcategory=cls.subcategory,
            comercio_final=cls.comercio or mov.get("comercio_final") or mov.get("comercio"),
            suggested_category=cls.category,
            suggested_subcategory=cls.subcategory,
            confidence=cls.confidence,
            classifier_source=cls.source,
            comercio=cls.comercio or mov.get("comercio"),
            tipo=cls.tipo or mov.get("tipo"),
            requiere_revision=cls.requiere_revision,
            pregunta_sugerida=cls.pregunta_sugerida,
        )
    except ServiceError as e:
        log.exception(f"feedback correct {mov_id} falló")
        return f"No pude registrar la corrección de #{idx}: {type(e).__name__}: {e}"

    refreshed = db.get_movements_by_ids([mov_id])
    if refreshed:
        telegram_notify.send_movement_cards([refreshed[0]])
    return f"🔁 Re-categorizando #{idx} con: «{rest[:60]}»"


def _approve_all(ids: list[str], chat_id: str) -> str:
    """Aprueba todos los movimientos pendientes del último batch. Cada uno pasa
    por services.movements.approve_movement (o approve_corrected_movement si
    ya estaba en corrected_pending). El service dispara el sync a GSheet por
    cada uno; los que fallan quedan en sheet_sync_status=sync_error y son
    reintentables desde el dashboard."""
    movs = db.get_movements_by_ids(ids)
    count = 0
    sheet_failures = 0
    skipped = 0

    for mov in movs:
        review = mov.get("review_status") or ("approved" if mov.get("status") == "aprobado" else "pending")
        if review not in {"pending", "corrected_pending"}:
            skipped += 1
            continue

        try:
            if review == "corrected_pending":
                updated = movement_service.approve_corrected_movement(
                    mov["id"], actor=chat_id, source="telegram", expected_version=None,
                )
            else:
                updated = movement_service.approve_movement(
                    mov["id"], actor=chat_id, source="telegram", expected_version=None,
                )
        except ServiceError as e:
            log.exception(f"_approve_all error en {mov.get('id')}")
            sheet_failures += 1
            continue

        if updated.get("sheet_sync_status") == "sync_error":
            sheet_failures += 1
        else:
            count += 1

    msg = f"Aprobados {count} movimientos del último batch ✓"
    if sheet_failures:
        msg += f"\n⚠️ {sheet_failures} con error de sync a GSheet (reintenta desde el dashboard)."
    if skipped:
        msg += f"\n({skipped} ya estaban resueltos, no se tocaron)"
    return msg


_PATTERN_STOPWORDS = {
    "COMPRA", "COMPRAS", "PAGO", "PAGOS", "TRANSFERENCIA", "TRANSFER",
    "ABONO", "CARGO", "PRESTAMO", "AVANCE", "CMR", "WEBPAY", "MERPAGO",
    "MERCADOPAGO", "ONECLICK", "RECURRENTE", "TARJETA",
}


def _extract_pattern(description: str) -> str | None:
    """Primer token alfabético ≥4 chars que NO sea un stopword genérico
    (COMPRA, PAGO, etc). Evita reglas demasiado amplias que misclasifiquen todo."""
    norm = normalize(description)
    if not norm:
        return None
    for token in re.split(r"[^A-Z]+", norm):
        if len(token) >= 4 and token not in _PATTERN_STOPWORDS:
            return token
    return None
