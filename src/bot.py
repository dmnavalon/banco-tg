from __future__ import annotations

import os
import re
import threading
import time
import traceback
from html import escape as _hesc
from typing import Any

import requests

from . import db, feedback, gsheet, secrets_store, telegram_notify
from .utils import format_clp, get_logger, project_path

log = get_logger("bot")

TG_API = "https://api.telegram.org"

VALID_BANKS = ["falabella", "bancochile"]

_GREET_RE = re.compile(
    r"^(hol+[aáe]?|holis?|hi+|hey+|ey+|wena[s]?|buena[s]?(\s+(d[ií]as?|tardes?|noches?))?|"
    r"buenos\s+d[ií]as?|saludos?|qu[eé]\s+tal|ol[aá]|hello|good\s+(morning|afternoon|evening)|"
    r"qh|q\s+hay|q\s+onda|como\s+est[aá][s]?)[\s!.,😊👋🙋🤙]*$",
    re.IGNORECASE,
)

HELP_TEXT = (
    "Comandos:\n"
    "/start, /help — esta ayuda\n"
    "/setup — guía de configuración\n"
    "/cred <banco> — configura credenciales (falabella, bancochile)\n"
    "/forget <banco> — borra credenciales y sesión\n"
    "/cancel — cancela el wizard activo\n"
    "/test <banco> — ejecuta scrape ahora\n"
    "/run — corre el daily completo\n"
    "/status — bancos configurados y estado\n"
    "/pending — re-envía pendientes no notificados (de a 5)\n"
    "/next — siguientes 5 tarjetas si quedaron en cola\n"
    "/last [N] — últimos N movimientos (default 10, máx 50)\n"
    "\n"
    "Para responder un movimiento puedes:\n"
    " • Usar los botones de la tarjeta (✅ Aprobar / ✏️ Corregir / 🚫 Ignorar)\n"
    " • Escribir «1 ok», «2 ignorar», «todo ok»\n"
    " • Corregir con texto libre («2 es del super», «3 esto va a Bodemall»)\n"
    "   y el agente categoriza solo, te reenvía la tarjeta para confirmar."
)


def _bot_token() -> str:
    token = os.environ.get("TG_BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("TG_BOT_TOKEN no está configurado.")
    return token


def _running_in_cloud() -> bool:
    """True si estamos corriendo en Railway u otro hosting cloud (no en la Mac).
    Detecta por env vars que solo Railway/Docker setean."""
    return any(os.environ.get(k) for k in ("RAILWAY_ENVIRONMENT", "RAILWAY_PROJECT_ID", "RAILWAY_SERVICE_ID"))


def _authorized_chat_id() -> str:
    chat_id = os.environ.get("TG_CHAT_ID", "").strip()
    if not chat_id:
        raise RuntimeError("TG_CHAT_ID no está configurado.")
    return chat_id


def _read_offset() -> int:
    try:
        return int(db.get_config("tg_offset") or "0")
    except Exception:
        return 0


def _write_offset(offset: int) -> None:
    try:
        db.set_config("tg_offset", str(offset))
    except Exception:
        pass


def _get_updates(offset: int, timeout: int = 25) -> list[dict[str, Any]]:
    url = f"{TG_API}/bot{_bot_token()}/getUpdates"
    try:
        r = requests.get(
            url,
            params={"offset": offset, "timeout": timeout, "allowed_updates": ["message", "callback_query"]},
            timeout=timeout + 10,
        )
        if r.status_code != 200:
            log.warning(f"getUpdates respondió {r.status_code}: {r.text[:200]}")
            return []
        data = r.json()
        if not data.get("ok"):
            return []
        return data.get("result", []) or []
    except requests.RequestException as e:
        log.warning(f"getUpdates falló: {type(e).__name__}: {e}")
        return []


def _send(text: str) -> None:
    telegram_notify.send_message(text)


def _handle_message(msg: dict[str, Any]) -> None:
    chat = msg.get("chat") or {}
    chat_id = str(chat.get("id", ""))
    text = (msg.get("text") or "").strip()
    message_id = str(msg.get("message_id", ""))

    authorized = _authorized_chat_id()
    if chat_id != authorized:
        log.warning(f"Mensaje de chat no autorizado: {chat_id}. Ignorando.")
        return

    db.record_telegram_log(
        direction="in",
        chat_id=chat_id,
        message_id=message_id,
        text=text,
    )

    if not text:
        return

    # Force Reply: si el mensaje es respuesta a un prompt de corrección, rutear acá
    # antes que cualquier otra cosa. Esto resuelve el caso de varias correcciones
    # activas simultáneamente sin ambigüedad.
    reply_to = msg.get("reply_to_message")
    if reply_to:
        reply_to_id = str(reply_to.get("message_id", ""))
        pending = db.get_pending_correction(reply_to_id)
        if pending:
            _handle_correction_reply(text, chat_id, pending, reply_to_id)
            return

    if text.lower().startswith("otp "):
        log.info("Mensaje OTP recibido — el provider lo recogerá desde telegram_log.")
        return

    if text.startswith("/"):
        _handle_command(text, chat_id)
        return

    state = db.get_wizard_state(chat_id)
    if state:
        _handle_wizard_input(text, chat_id, state)
        return

    if _GREET_RE.match(text):
        threading.Thread(target=_handle_greeting, daemon=True).start()
        return

    response = feedback.apply(text, chat_id)
    # Si feedback no tiene batch y el mensaje es corto, probablemente es un saludo no reconocido
    if response == "No hay batch reciente. Manda /pending para reenviar." and len(text) <= 40:
        threading.Thread(target=_handle_greeting, daemon=True).start()
        return
    _send(response)


def _handle_command(text: str, chat_id: str) -> None:
    parts = text.split()
    cmd = parts[0].lower()
    args = parts[1:]

    if cmd in {"/start", "/help"}:
        _send(HELP_TEXT)
        return

    if cmd == "/setup":
        configured = set(secrets_store.list_configured())
        missing = [b for b in VALID_BANKS if b not in configured]
        if not missing:
            _send("Todos los bancos están configurados. Usa /test <banco> para probar.")
            return
        nxt = missing[0]
        _send(f"Siguiente banco a configurar: {nxt}\nMándame: /cred {nxt}")
        return

    if cmd == "/cred":
        if not args:
            _send("Uso: /cred <falabella|bancochile>")
            return
        bank = args[0].lower()
        if bank not in VALID_BANKS:
            _send(f"Banco no soportado. Opciones: {', '.join(VALID_BANKS)}")
            return
        db.set_wizard_state(chat_id, f"awaiting_rut_{bank}", {"bank": bank})
        _send(
            f"Configurando {bank}.\n"
            "1/2 → Mándame tu RUT en formato 12345678-9 (con guión, sin puntos).\n"
            "Cancela con /cancel."
        )
        return

    if cmd == "/forget":
        if not args:
            _send("Uso: /forget <falabella|bancochile>")
            return
        bank = args[0].lower()
        if bank not in VALID_BANKS:
            _send(f"Banco no soportado. Opciones: {', '.join(VALID_BANKS)}")
            return
        deleted = secrets_store.delete(bank)
        state_file = project_path("data", f"state_{bank}.json")
        if state_file.exists():
            state_file.unlink()
        _send(f"{bank}: credenciales {'borradas' if deleted else 'no había'}; sesión limpiada.")
        return

    if cmd == "/cancel":
        db.clear_wizard_state(chat_id)
        _send("Wizard cancelado.")
        return

    if cmd == "/test":
        if not args:
            _send("Uso: /test <falabella|bancochile>")
            return
        bank = args[0].lower()
        if bank not in VALID_BANKS:
            _send(f"Banco no soportado. Opciones: {', '.join(VALID_BANKS)}")
            return
        threading.Thread(target=_run_test_in_thread, args=(bank,), daemon=True).start()
        _send(f"[{bank}] Iniciando scrape en background…")
        return

    if cmd == "/run":
        threading.Thread(target=_run_daily_in_thread, daemon=True).start()
        _send("Corriendo daily completo en background…")
        return

    if cmd == "/status":
        configured = secrets_store.list_configured()
        pending = db.count_pending()
        total = db.count_total()
        rules_n = db.count_rules()
        last_err = db.get_last_error()
        err_str = "ninguno"
        if last_err:
            err_str = f"{last_err['component']}: {last_err['message'][:120]}"
        _send(
            "Estado:\n"
            f"  Bancos configurados: {', '.join(configured) if configured else '(ninguno)'}\n"
            f"  Movimientos totales: {total}\n"
            f"  Pendientes de revisar: {pending}\n"
            f"  Reglas aprendidas: {rules_n}\n"
            f"  Último error: {err_str}"
        )
        return

    if cmd == "/pending":
        threading.Thread(target=_resend_pending, daemon=True).start()
        _send("Reenviando pendientes…")
        return

    if cmd == "/next":
        threading.Thread(target=_send_next_page, daemon=True).start()
        return

    if cmd == "/last":
        n = 10
        if args:
            try:
                n = int(args[0])
            except ValueError:
                _send("Uso: /last [N]")
                return
        movs = db.get_last_movements(n)
        if not movs:
            _send("Sin movimientos.")
            return
        lines = [f"Últimos {len(movs)}:"]
        for m in movs:
            cat = m["final_category"] or m["suggested_category"] or "?"
            lines.append(f"  {m['date']} [{m['bank']}] {format_clp(m['amount'])} · {m['description'][:50]} · {cat} ({m['status']})")
        _send("\n".join(lines))
        return

    _send(f"Comando no reconocido: {cmd}. /help")


def _edit_card(chat_id: str, message_id: int, mov: dict, new_text: str) -> None:
    """Edita una tarjeta de movimiento. Si la tarjeta original tiene foto
    (file_id en mov), usa editMessageCaption; si no, editMessageText.
    Quita el inline keyboard (los botones) tras la decisión."""
    has_photo = bool(mov.get("tg_photo_file_id"))
    if has_photo:
        telegram_notify.edit_message_caption(chat_id, message_id, new_text, reply_markup={"inline_keyboard": []})
    else:
        telegram_notify.edit_message_text(chat_id, message_id, new_text)


def _handle_callback(callback: dict[str, Any]) -> None:
    callback_id = callback.get("id", "")
    chat = (callback.get("message") or {}).get("chat") or {}
    chat_id = str(chat.get("id", ""))
    message_id = (callback.get("message") or {}).get("message_id")
    data = (callback.get("data") or "").strip()

    authorized = _authorized_chat_id()
    if chat_id != authorized:
        telegram_notify.answer_callback_query(callback_id)
        return

    telegram_notify.answer_callback_query(callback_id)

    if not data or ":" not in data:
        return

    action, mov_id = data.split(":", 1)
    movs = db.get_movements_by_ids([mov_id])
    if not movs:
        if message_id:
            telegram_notify.edit_message_text(chat_id, message_id, "[Movimiento no encontrado]")
        return

    mov = movs[0]

    if action == "a":
        cat = mov.get("suggested_category") or "Otro"
        sub = mov.get("suggested_subcategory")
        db.update_decision(mov_id, status="aprobado", final_category=cat, final_subcategory=sub, decided_by=chat_id)
        try:
            gsheet.append_movement({**mov, "final_category": cat, "final_subcategory": sub})
        except Exception as e:
            log.warning(f"gsheet.append_movement falló: {e}")
        label = f"{_hesc(cat)} / {_hesc(sub)}" if sub else _hesc(cat)
        if message_id:
            from .telegram_notify import _movement_card_text
            _edit_card(chat_id, message_id, mov,
                       f"{_movement_card_text(mov)}\n\n✅ <b>Aprobado:</b> {label}")

    elif action == "i":
        db.update_decision(mov_id, status="ignorado", final_category=None, final_subcategory=None, decided_by=chat_id)
        if message_id:
            from .telegram_notify import _movement_card_text
            _edit_card(chat_id, message_id, mov,
                       f"{_movement_card_text(mov)}\n\n⚫ <b>Ignorado</b>")

    elif action == "c":
        # Force Reply: mandar un prompt nuevo que abre el input citando este mensaje
        # específico, así no hay confusión con cuál tarjeta se está corrigiendo.
        prompt_id = telegram_notify.send_correction_prompt(chat_id, mov)
        if prompt_id:
            db.save_pending_correction(
                prompt_message_id=str(prompt_id),
                mov_id=mov_id,
                chat_id=chat_id,
                original_card_message_id=message_id,
            )
        if message_id:
            from .telegram_notify import _movement_card_text
            _edit_card(chat_id, message_id, mov,
                       f"{_movement_card_text(mov)}\n\n✏️ <i>Esperando corrección…</i>")


def _handle_wizard_input(text: str, chat_id: str, state: dict[str, Any]) -> None:
    state_name = state["state"]
    payload = state["payload"] or {}
    bank = payload.get("bank")

    if state_name == "correcting":
        mov_id = payload.get("mov_id", "")
        message_id = payload.get("message_id")
        db.clear_wizard_state(chat_id)
        if not mov_id:
            _send("Error interno: mov_id no encontrado. Reintenta.")
            return
        movs = db.get_movements_by_ids([mov_id])
        if not movs:
            _send("Movimiento no encontrado en la base.")
            return
        mov = movs[0]

        # Re-clasificar con la pista del usuario en lenguaje natural.
        from . import classifier
        try:
            cls = classifier.classify_with_hint(
                description=mov.get("description", ""),
                amount=float(mov.get("amount") or 0),
                hint=text,
            )
        except Exception as e:
            log.exception("classify_with_hint falló")
            _send(f"No pude reclasificar con tu hint: {type(e).__name__}: {e}")
            return

        # Persistir la nueva sugerencia (queda como suggested_*, sigue 'pendiente').
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

        # Cerrar la tarjeta vieja con un nudge y reenviar una nueva con la categorización.
        if message_id:
            from .telegram_notify import _movement_card_text
            telegram_notify.edit_message_text(
                chat_id, message_id,
                f"{_movement_card_text(mov)}\n\n🔁 <b>Re-categorizando con:</b> <i>{_hesc(text)}</i>",
            )

        # Releer y mandar tarjeta nueva con la sugerencia actualizada.
        movs = db.get_movements_by_ids([mov_id])
        if movs:
            telegram_notify.send_movement_cards([movs[0]])
        return

    if state_name.startswith("awaiting_rut_"):
        if not re.fullmatch(r"\d{7,8}-[\dkK]", text):
            _send("RUT inválido. Formato: 12345678-9 (con guión, sin puntos). Reintenta o /cancel.")
            return
        payload["rut"] = text.upper()
        db.set_wizard_state(chat_id, f"awaiting_pass_{bank}", payload)
        if bank == "falabella":
            _send("2/2 → Mándame tu Clave Internet (exactamente 6 dígitos).")
        else:
            _send("2/2 → Mándame tu Clave (máx 8 caracteres).")
        return

    if state_name.startswith("awaiting_pass_"):
        if bank == "falabella" and not re.fullmatch(r"\d{6}", text):
            _send("Clave Falabella inválida: deben ser 6 dígitos. Reintenta o /cancel.")
            return
        if bank == "bancochile" and len(text) > 8:
            _send("Clave BCh excede 8 caracteres. Reintenta o /cancel.")
            return
        rut = payload.get("rut", "")
        try:
            secrets_store.store(bank, rut, text)
            db.clear_wizard_state(chat_id)
            _send(f"✓ Credenciales de {bank} guardadas (cifradas).\nProbá con /test {bank}.")
        except Exception as e:
            db.record_error("bot.wizard.store", str(e), traceback.format_exc())
            _send(f"Error guardando: {type(e).__name__}: {e}")
        return

    db.clear_wizard_state(chat_id)
    _send("Estado de wizard inválido, reseteado. Reintenta /cred <banco>.")


def _handle_correction_reply(text: str, chat_id: str, pending: dict, prompt_msg_id: str) -> None:
    """Procesa la respuesta del usuario a un prompt de Force Reply para corregir un movimiento.

    El prompt fue generado al apretar "✏️ Corregir" en una tarjeta. La respuesta del
    usuario (citando ese prompt) llega acá. Se re-clasifica con el LLM usando el texto
    como hint y se reenvía una tarjeta nueva. Si el usuario escribe "cancelar", se
    aborta y se vuelve a mandar la tarjeta original con sus botones.
    """
    mov_id = pending.get("mov_id", "")
    # Limpiar el pending PRIMERO: si algo falla, el prompt no queda colgado.
    db.delete_pending_correction(prompt_msg_id)

    # Limpieza visual: borrar el prompt del chat (ya cumplió su función).
    try:
        telegram_notify.delete_message(chat_id, int(prompt_msg_id))
    except Exception:
        pass

    movs = db.get_movements_by_ids([mov_id])
    if not movs:
        _send("Movimiento no encontrado en la base.")
        return
    mov = movs[0]

    if text.strip().lower() in {"cancelar", "cancel", "abort", "abortar", "salir"}:
        # Reenviar la tarjeta original con sus botones (nueva tarjeta, fresh).
        telegram_notify.send_movement_cards([mov])
        _send("✖️ Corrección cancelada.")
        return

    # Re-classify con la pista del usuario.
    from . import classifier
    try:
        cls = classifier.classify_with_hint(
            description=mov.get("description", ""),
            amount=float(mov.get("amount") or 0),
            hint=text,
        )
    except Exception as e:
        log.exception("classify_with_hint falló")
        _send(f"No pude reclasificar: {type(e).__name__}: {e}")
        return

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


def _handle_greeting() -> None:
    try:
        rows = db.get_all_pending()
        if not rows:
            _send(
                "👋 Hola, Diego.\n\n"
                "✅ Todo al día por ahora.\n"
                "No tengo movimientos nuevos pendientes de revisión.\n\n"
                "Si quieres revisar pendientes anteriores, escribe /pending."
            )
            return

        n = len(rows)
        plural = "s" if n != 1 else ""
        _send(f"👋 Hola, Diego.\n\nTienes {n} movimiento{plural} pendiente{plural} de revisión:")
        # Pasamos los dicts de Firestore tal cual para conservar tg_photo_file_id,
        # persona, y otros campos opcionales que la tarjeta usa.
        telegram_notify.send_movement_cards(list(rows))
    except Exception as e:
        db.record_error("bot.greeting", str(e), traceback.format_exc())
        _send(f"Error al consultar pendientes: {type(e).__name__}: {e}")


def _split_cat_sub(s: str) -> tuple[str, str | None]:
    parts = [p.strip() for p in s.split("/", 1)]
    cat = parts[0].capitalize() if parts[0] else "Otro"
    sub = parts[1].capitalize() if len(parts) > 1 and parts[1] else None
    return cat, sub


_PATTERN_STOPWORDS = {
    "COMPRA", "COMPRAS", "PAGO", "PAGOS", "TRANSFERENCIA", "TRANSFER",
    "ABONO", "CARGO", "PRESTAMO", "AVANCE", "CMR", "WEBPAY", "MERPAGO",
    "MERCADOPAGO", "ONECLICK", "RECURRENTE", "TARJETA",
}


def _extract_pattern(description: str) -> str | None:
    """Toma el primer token alfabético ≥4 chars que NO sea un stopword genérico.
    Evita aprender reglas como pattern='COMPRA' que matchearían cualquier movimiento."""
    from .utils import normalize
    norm = normalize(description)
    if not norm:
        return None
    for token in re.split(r"[^A-Z]+", norm):
        if len(token) >= 4 and token not in _PATTERN_STOPWORDS:
            return token
    return None


def _run_test_in_thread(bank: str) -> None:
    try:
        from . import run_daily
        otp = run_daily.make_otp_provider()
        new = run_daily.run_for_bank_full(bank, otp, progress=_send)
        if new:
            n = len(new)
            _send(f"✅ [{bank}] {n} movimiento(s) clasificado(s). Enviando tarjetas…")
            telegram_notify.send_movement_cards(new)
        # Si no hay nuevos, run_for_bank_full ya envió el mensaje "Sin movimientos"
    except Exception as e:
        db.record_error(f"bot.test.{bank}", str(e), traceback.format_exc())
        _send(f"[{bank}] Error: {type(e).__name__}: {e}")


def _run_daily_in_thread() -> None:
    try:
        from . import run_daily
        run_daily.main()
    except Exception as e:
        db.record_error("bot.run_daily", str(e), traceback.format_exc())
        _send(f"Daily falló: {type(e).__name__}: {e}")


def _send_next_page() -> None:
    try:
        msg = telegram_notify.send_next_batch_page()
        if msg:
            _send(msg)
    except Exception as e:
        db.record_error("bot.next", str(e), traceback.format_exc())
        _send(f"Error en /next: {type(e).__name__}: {e}")


def _resend_pending() -> None:
    try:
        rows = db.get_pending()
        if not rows:
            _send("Sin pendientes.")
            return
        # Reusamos el dict completo de Firestore para que `send_movement_cards`
        # tenga acceso a tg_photo_file_id, persona, y cualquier campo nuevo que
        # se agregue al modelo en el futuro sin tener que actualizar este loop.
        telegram_notify.send_daily_batch(list(rows))
    except Exception as e:
        db.record_error("bot.pending", str(e), traceback.format_exc())
        _send(f"Error reenviando pendientes: {type(e).__name__}: {e}")


def main() -> None:
    db.init_if_needed()
    log.info("Bot iniciando long-poll…")
    offset = _read_offset()
    while True:
        try:
            updates = _get_updates(offset)
            for u in updates:
                update_id = u.get("update_id", 0)
                offset = update_id + 1
                _write_offset(offset)
                msg = u.get("message")
                if msg:
                    try:
                        _handle_message(msg)
                    except Exception as e:
                        db.record_error("bot.handle_message", str(e), traceback.format_exc())
                        log.error(f"Error procesando update {update_id}: {e}")
                cbq = u.get("callback_query")
                if cbq:
                    try:
                        _handle_callback(cbq)
                    except Exception as e:
                        db.record_error("bot.handle_callback", str(e), traceback.format_exc())
                        log.error(f"Error procesando callback {update_id}: {e}")
        except Exception as e:
            log.error(f"Loop crash: {type(e).__name__}: {e}")
            time.sleep(5)


if __name__ == "__main__":
    main()
