from __future__ import annotations

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
    last_err: Exception | None = None
    for delay in delays:
        if delay:
            time.sleep(delay)
        try:
            r = requests.post(url, json=payload, timeout=20)
            if r.status_code == 200:
                data = r.json()
                if data.get("ok"):
                    if log_db:
                        try:
                            db.record_telegram_log(
                                direction="out",
                                chat_id=target,
                                message_id=str(data["result"].get("message_id", "")),
                                text=text,
                            )
                        except Exception as e:
                            log.warning(f"No pude registrar telegram_log: {e}")
                    return data["result"]
            log.warning(f"Telegram respondió {r.status_code}: {r.text[:200]}")
        except requests.RequestException as e:
            last_err = e
            log.warning(f"sendMessage falló: {type(e).__name__}: {e}")

    log.error(f"sendMessage falló tras 3 intentos: {last_err}")
    return None


def send_daily_batch(movements: Sequence[dict]) -> bool:
    if not movements:
        send_message("Sin movimientos nuevos para revisar hoy.")
        return True

    lines = ["Diego, movimientos nuevos para revisar:\n"]
    for i, mov in enumerate(movements, 1):
        bank = mov["bank"]
        date = mov["date"]
        desc = mov["description"]
        amount_str = format_clp(mov["amount"])
        cat = mov.get("suggested_category") or "Otro"
        sub = mov.get("suggested_subcategory")
        conf = mov.get("confidence") or 0.0
        cat_str = f"{cat}/{sub}" if sub else cat
        lines.append(f"{i}. {date} · [{bank}] {desc}")
        lines.append(f"   {amount_str} → {cat_str} ({int(conf * 100)}%)")
    lines.append("")
    lines.append("Responde:")
    lines.append("  «1 ok» (acepta sugerencia)")
    lines.append("  «2 supermercado» (corrige)")
    lines.append("  «2 alimentacion/restaurant» (corrige cat/subcat)")
    lines.append("  «3 ignorar»")
    lines.append("  «todo ok»")

    text = "\n".join(lines)
    target = _chat_id()
    url = f"{TG_API}/bot{_bot_token()}/sendMessage"
    payload = {"chat_id": target, "text": text, "disable_web_page_preview": True}

    delays = [0, 2, 5]
    for delay in delays:
        if delay:
            time.sleep(delay)
        try:
            r = requests.post(url, json=payload, timeout=20)
            if r.status_code == 200:
                data = r.json()
                if data.get("ok"):
                    ids_payload = ",".join(str(m["id"]) for m in movements)
                    db.record_telegram_log(
                        direction="out",
                        chat_id=target,
                        message_id=str(data["result"].get("message_id", "")),
                        text=text,
                        payload=ids_payload,
                    )
                    db.mark_notified([m["id"] for m in movements])
                    return True
            log.warning(f"Telegram respondió {r.status_code}: {r.text[:200]}")
        except requests.RequestException as e:
            log.warning(f"send_daily_batch falló: {type(e).__name__}: {e}")

    log.error("send_daily_batch falló tras 3 intentos. Movimientos quedan sin notificar.")
    return False
