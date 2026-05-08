from __future__ import annotations

import re

from . import classifier, db, gsheet, telegram_notify
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

    if rest_lower in {"ok", "ok.", "si", "sí"}:
        cat = mov["suggested_category"] or "Otro"
        sub = mov["suggested_subcategory"]
        db.update_decision(
            mov_id,
            status="aprobado",
            final_category=cat,
            final_subcategory=sub,
            decided_by=chat_id,
        )
        gsheet_warn = _try_append({**mov, "final_category": cat, "final_subcategory": sub})
        return f"OK #{idx}: {cat}{('/' + sub) if sub else ''} ✓{gsheet_warn}"

    if rest_lower in {"ignorar", "ignora", "skip"}:
        db.update_decision(
            mov_id,
            status="ignorado",
            final_category=None,
            final_subcategory=None,
            decided_by=chat_id,
        )
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

    db.update_classification(
        mov_id,
        suggested_category=cls.category,
        suggested_subcategory=cls.subcategory,
        confidence=cls.confidence,
        classifier_source=cls.source,
        comercio=cls.comercio or mov.get("comercio"),
        tipo=cls.tipo or mov.get("tipo"),
        requiere_revision=cls.requiere_revision,
        pregunta_sugerida=cls.pregunta_sugerida,
    )

    refreshed = db.get_movements_by_ids([mov_id])
    if refreshed:
        telegram_notify.send_movement_cards([refreshed[0]])
    return f"🔁 Re-categorizando #{idx} con: «{rest[:60]}»"


def _approve_all(ids: list[str], chat_id: str) -> str:
    movs = db.get_movements_by_ids(ids)
    count = 0
    sheet_failures = 0
    for mov in movs:
        if mov["status"] != "pendiente":
            continue
        cat = mov["suggested_category"] or "Otro"
        sub = mov["suggested_subcategory"]
        db.update_decision(
            mov["id"],
            status="aprobado",
            final_category=cat,
            final_subcategory=sub,
            decided_by=chat_id,
        )
        if _try_append({**mov, "final_category": cat, "final_subcategory": sub}):
            sheet_failures += 1
        count += 1
    msg = f"Aprobados {count} movimientos del último batch ✓"
    if sheet_failures:
        msg += f"\n⚠️ {sheet_failures} no llegaron al GSheet (ver logs)."
    return msg


def _try_append(mov: dict) -> str:
    """Empuja el movimiento al GSheet. Si falla, loguea con stack y devuelve un warning para el mensaje al usuario."""
    try:
        gsheet.upsert_movement(mov)
        return ""
    except Exception as e:
        log.exception("No pude empujar al GSheet")
        return f"\n⚠️ GSheet falló: {type(e).__name__}: {str(e)[:120]}"


def _split_cat_sub(s: str) -> tuple[str, str | None]:
    parts = [p.strip() for p in s.split("/", 1)]
    cat = parts[0].capitalize() if parts[0] else "Otro"
    sub = parts[1] if len(parts) > 1 and parts[1] else None
    if sub:
        sub = sub.capitalize()
    return cat, sub


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
