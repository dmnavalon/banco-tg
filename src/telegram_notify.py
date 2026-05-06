from __future__ import annotations

import json
import os
import time
from typing import Sequence

import requests

from . import db
from .utils import format_clp, get_logger

log = get_logger("telegram_notify")

TG_API = "https://api.telegram.org"


def _bot_token() -> str:
    token = os.environ.get("TG_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("TG_BOT_TOKEN no está configurado en .env")
    return token


def _chat_id() -> str:
    chat_id = os.environ.get("TG_CHAT_ID", "").strip()
    if not chat_id:
        raise RuntimeError("TG_CHAT_ID no está configurado en .env")
    return chat_id


def send_message(text: str, *, chat_id: str | None = None, log_db: bool = True) -> dict | None:
    target = chat_id or _chat_id()
    url = f"{TG_API}/bot{_bot_token()}/sendMessage"
    payload = {"chat_id": target, "text": text, "disable_web_page_preview": True}

    delays = [0, 2, 5]
    for delay in delays:
        if delay:
            time.sleep(delay)
        try:
            r = requests.post(url, json=payload, timeout=20)
            if r.status_code == 200 and r.json().get("ok"):
                data = r.json()["result"]
                if log_db:
                    try:
                        db.record_telegram_log(
                            direction="out",
                            chat_id=target,
                            message_id=str(data.get("message_id", "")),
                            text=text,
                        )
                    except Exception:
                        pass
                return data
            log.warning(f"sendMessage {r.status_code}: {r.text[:200]}")
        except requests.RequestException as e:
            log.warning(f"sendMessage falló: {e}")

    log.error("sendMessage falló tras 3 intentos.")
    return None


def answer_callback_query(callback_query_id: str, text: str = "") -> None:
    try:
        requests.post(
            f"{TG_API}/bot{_bot_token()}/answerCallbackQuery",
            json={"callback_query_id": callback_query_id, "text": text},
            timeout=5,
        )
    except Exception:
        pass


def edit_message_text(chat_id: str, message_id: int, text: str) -> None:
    try:
        requests.post(
            f"{TG_API}/bot{_bot_token()}/editMessageText",
            json={"chat_id": chat_id, "message_id": message_id, "text": text},
            timeout=10,
        )
    except Exception:
        pass


def _movement_card_text(mov: dict) -> str:
    bank = (mov.get("bank") or "").capitalize()
    desc = mov.get("description") or ""
    amount_str = format_clp(mov.get("amount") or 0)
    comercio = mov.get("comercio") or "-"
    cat = mov.get("suggested_category") or "Sin categoría"
    sub = mov.get("suggested_subcategory")
    conf = int((mov.get("confidence") or 0) * 100)
    tipo = mov.get("tipo") or "Egreso"
    pregunta = mov.get("pregunta_sugerida")
    propuesta = f"{cat} / {sub}" if sub else cat

    lines = [
        f"Banco: {bank}",
        f"Texto: {desc}",
        f"Monto: {amount_str}",
        f"Tipo: {tipo}",
        f"Comercio: {comercio}",
        f"Propuesta: {propuesta} ({conf}%)",
    ]
    if pregunta:
        lines.append(f"Consulta: {pregunta}")
    return "\n".join(lines)


def _movement_keyboard(mov_id: str) -> dict:
    return {
        "inline_keyboard": [[
            {"text": "Aprobar", "callback_data": f"a:{mov_id}"},
            {"text": "Corregir", "callback_data": f"c:{mov_id}"},
            {"text": "Ignorar",  "callback_data": f"i:{mov_id}"},
        ]]
    }


def send_movement_cards(movements: Sequence[dict]) -> bool:
    if not movements:
        send_message("Sin movimientos nuevos para revisar hoy.")
        return True

    target = _chat_id()
    url = f"{TG_API}/bot{_bot_token()}/sendMessage"
    ids_sent: list[str] = []

    for mov in movements:
        mov_id = mov["id"]
        text = _movement_card_text(mov)
        keyboard = _movement_keyboard(mov_id)

        sent = False
        for delay in [0, 2, 5]:
            if delay:
                time.sleep(delay)
            try:
                r = requests.post(url, json={
                    "chat_id": target,
                    "text": text,
                    "reply_markup": keyboard,
                    "disable_web_page_preview": True,
                }, timeout=20)
                if r.status_code == 200 and r.json().get("ok"):
                    ids_sent.append(mov_id)
                    sent = True
                    break
                log.warning(f"sendMessage card {r.status_code}: {r.text[:200]}")
            except requests.RequestException as e:
                log.warning(f"send card falló: {e}")

        if not sent:
            log.error(f"No se pudo enviar tarjeta para {mov_id}")

    if ids_sent:
        db.set_batch_ids(ids_sent)
        db.mark_notified(ids_sent)
        db.record_telegram_log(
            direction="out",
            chat_id=target,
            message_id="batch",
            text=f"{len(ids_sent)} tarjetas enviadas",
            payload=",".join(ids_sent),
        )

    return len(ids_sent) == len(movements)


# Alias de compatibilidad
send_daily_batch = send_movement_cards
