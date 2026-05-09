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

    # Force Reply: si el mensaje es respuesta a un prompt de corrección o ignore,
    # rutear acá antes que cualquier otra cosa. Resuelve el caso de varias acciones
    # activas simultáneamente sin ambigüedad.
    reply_to = msg.get("reply_to_message")
    if reply_to:
        reply_to_id = str(reply_to.get("message_id", ""))
        pending = db.get_pending_user_action(reply_to_id)
        if pending:
            if pending.get("action") == "ignore":
                _handle_ignore_reply(text, chat_id, pending, reply_to_id)
            else:
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


def _edit_card(chat_id: str, message_id: int, mov: dict, new_text: str, *, keyboard: dict | None = None) -> None:
    """Edita una tarjeta de movimiento. Si la tarjeta original tiene foto
    (file_id en mov), usa editMessageCaption; si no, editMessageText.

    Por default quita el inline keyboard. Si el caller pasa `keyboard`, se usa
    ese (ej. para dejar un botón 'Corregir nuevamente' tras aprobar/ignorar).
    """
    has_photo = bool(mov.get("tg_photo_file_id"))
    rm = keyboard if keyboard is not None else {"inline_keyboard": []}
    if has_photo:
        telegram_notify.edit_message_caption(chat_id, message_id, new_text, reply_markup=rm)
    else:
        # editMessageText no recibe reply_markup en nuestro helper actual,
        # pero igual editamos el texto. Si necesitamos reply_markup en text-only,
        # lo manejamos como caso especial vía editMessageReplyMarkup aparte.
        telegram_notify.edit_message_text(chat_id, message_id, new_text)
        # Mantener el keyboard customizado en mensajes sin foto:
        if keyboard is not None:
            telegram_notify.edit_message_reply_markup(chat_id, message_id, keyboard)


def _correct_again_keyboard(mov_id: str) -> dict:
    """Keyboard de un solo botón '✏️ Corregir nuevamente' para tarjetas ya
    aprobadas o ignoradas. El callback es 'c:<id>', el mismo que el botón
    'Corregir' original — reusa el flujo de Force Reply."""
    return {"inline_keyboard": [[
        {"text": "✏️ Corregir nuevamente", "callback_data": f"c:{mov_id}"},
    ]]}


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
        # Guard contra doble-approve: si ya estaba aprobado (ej. el usuario clickea
        # ✅ en dos tarjetas duplicadas que el bot envió por error), no volver a
        # llamar upsert_movement — eso evita el race condition con read-after-write
        # del Google Sheets API que produce filas duplicadas en el sheet.
        if mov.get("status") == "aprobado":
            log.info(f"approve duplicado ignorado para mov {mov_id} (ya estaba aprobado)")
            cat = mov.get("final_category") or mov.get("suggested_category") or "Otro"
            sub = mov.get("final_subcategory") or mov.get("suggested_subcategory")
            label = f"{_hesc(cat)} / {_hesc(sub)}" if sub else _hesc(cat)
            if message_id:
                from .telegram_notify import _movement_card_text
                _edit_card(chat_id, message_id, mov,
                           f"{_movement_card_text(mov)}\n\n✅ <b>Aprobado:</b> {label}",
                           keyboard=_correct_again_keyboard(mov_id))
            return

        cat = mov.get("suggested_category") or "Otro"
        sub = mov.get("suggested_subcategory")
        db.update_decision(mov_id, status="aprobado", final_category=cat, final_subcategory=sub, decided_by=chat_id)
        try:
            gsheet.upsert_movement({**mov, "final_category": cat, "final_subcategory": sub})
        except Exception as e:
            log.warning(f"gsheet.upsert_movement falló: {e}")
        label = f"{_hesc(cat)} / {_hesc(sub)}" if sub else _hesc(cat)
        if message_id:
            from .telegram_notify import _movement_card_text
            _edit_card(chat_id, message_id, mov,
                       f"{_movement_card_text(mov)}\n\n✅ <b>Aprobado:</b> {label}",
                       keyboard=_correct_again_keyboard(mov_id))

    elif action == "i":
        # En lugar de marcar como ignorado inmediatamente, pedimos una razón
        # con force_reply. La marca real ocurre en _handle_ignore_reply cuando
        # el usuario responde (puede mandar "skip" para ignorar sin razón, o
        # "cancelar" para abortar).
        prompt_id = telegram_notify.send_ignore_prompt(chat_id, mov)
        if prompt_id:
            db.save_pending_user_action(
                prompt_message_id=str(prompt_id),
                mov_id=mov_id,
                chat_id=chat_id,
                action="ignore",
                original_card_message_id=message_id,
            )
        if message_id:
            from .telegram_notify import _movement_card_text
            _edit_card(chat_id, message_id, mov,
                       f"{_movement_card_text(mov)}\n\n🚫 <i>Esperando razón…</i>")

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


def _handle_ignore_reply(text: str, chat_id: str, pending: dict, prompt_msg_id: str) -> None:
    """Procesa la respuesta del usuario a un prompt de Force Reply para ignorar
    un movimiento. La razón se guarda en `ignore_reason` del documento Firestore.

    Comandos especiales del texto:
      - "cancelar"/"cancel"/"abortar"/"salir" → no ignora, reenvía la tarjeta original.
      - "skip"/"sin razón"/"-" → ignora sin razón (queda como ignorado, sin reason).
      - cualquier otro texto → ignora con esa razón.
    """
    mov_id = pending.get("mov_id", "")
    db.delete_pending_user_action(prompt_msg_id)

    try:
        telegram_notify.delete_message(chat_id, int(prompt_msg_id))
    except Exception:
        pass

    movs = db.get_movements_by_ids([mov_id])
    if not movs:
        _send("Movimiento no encontrado en la base.")
        return
    mov = movs[0]

    text_clean = text.strip()
    text_lower = text_clean.lower()

    if text_lower in {"cancelar", "cancel", "abort", "abortar", "salir"}:
        # Reenviar la tarjeta original con sus 3 botones.
        telegram_notify.send_movement_cards([mov])
        _send("✖️ Ignore cancelado.")
        return

    reason: str | None = text_clean
    if text_lower in {"skip", "sin razon", "sin razón", "-", "ninguna", "nada"}:
        reason = None

    db.update_decision(
        mov_id,
        status="ignorado",
        final_category=None,
        final_subcategory=None,
        decided_by=chat_id,
        ignore_reason=reason,
    )

    # Editar la tarjeta original para mostrarla como Ignorado (con razón si la hay)
    # + botón "Corregir nuevamente" por si Diego cambia de opinión después.
    refreshed = db.get_movements_by_ids([mov_id])
    if refreshed:
        mov_new = refreshed[0]
        orig_msg_id = pending.get("original_card_message_id")
        if orig_msg_id:
            from .telegram_notify import _movement_card_text
            badge = f"\n\n⚫ <b>Ignorado:</b> {_hesc(reason)}" if reason else "\n\n⚫ <b>Ignorado</b>"
            _edit_card(chat_id, int(orig_msg_id), mov_new,
                       f"{_movement_card_text(mov_new)}{badge}",
                       keyboard=_correct_again_keyboard(mov_id))


def _handle_greeting() -> None:
    try:
        pendientes = db.get_all_pending()
        ignoradas = db.get_ignored()
        if not pendientes and not ignoradas:
            _send(
                "👋 Hola, Diego.\n\n"
                "✅ Todo al día por ahora.\n"
                "No tengo movimientos nuevos pendientes de revisión.\n\n"
                "Si quieres revisar pendientes anteriores, escribe /pending."
            )
            return

        n_p = len(pendientes)
        n_i = len(ignoradas)
        if n_p and n_i:
            saludo = f"👋 Hola, Diego.\n\nTienes {n_p} pendiente{'s' if n_p != 1 else ''} + {n_i} ignorada{'s' if n_i != 1 else ''} en cola:"
        elif n_p:
            plural = "s" if n_p != 1 else ""
            saludo = f"👋 Hola, Diego.\n\nTienes {n_p} movimiento{plural} pendiente{plural} de revisión:"
        else:
            plural = "s" if n_i != 1 else ""
            saludo = f"👋 Hola, Diego.\n\nSin pendientes nuevos. Te reenvío {n_i} ignorada{plural} por si querés re-categorizar:"
        _send(saludo)
        movs = list(pendientes) + list(ignoradas)
        _ensure_classified(movs)
        telegram_notify.send_movement_cards(movs)
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


_CUOTAS_RE = re.compile(r"\((\d+)\s*/\s*(\d+)\)\s*$")


def _backfill_cuotas_from_description(movs: list[dict]) -> None:
    """Para cada mov pendiente cuya descripción termina con '(X/N)' y que NO
    tiene los campos cuotas_* poblados, los infiere desde el sufijo y los
    persiste en Firestore. Modifica los dicts in-place.

    Solo aplica a pendientes (los aprobados/corregidos quedan como están,
    según la decisión de Diego). cuota_monto NO se infiere ni se persiste —
    solo se obtiene re-scrapeando el modal de Falabella, queda None hasta
    entonces. La tarjeta TG y el dashboard del GSheet derivan on-the-fly
    como `amount / cuotas_total` cuando cuota_monto es None (exacta para
    cuotas sin interés, aproximada cuando hay interés).
    """
    for mov in movs:
        if mov.get("cuotas_total"):
            continue  # ya enriquecido
        desc = mov.get("description") or ""
        m = _CUOTAS_RE.search(desc)
        if not m:
            continue
        c_actual = int(m.group(1))
        c_total = int(m.group(2))
        if c_total <= 1:
            continue  # 1/1 no aplica como compra en cuotas
        # Persistir en Firestore con campos parciales (sin cuota_monto).
        try:
            ref = db._db().collection("movements").document(mov["id"])
            db._with_retry(lambda r=ref, ca=c_actual, ct=c_total: r.update({
                "cuotas_actual": ca,
                "cuotas_total": ct,
            }))
        except Exception:
            log.exception(f"backfill_cuotas: error persistiendo {mov.get('id')}")
            continue
        # In-place para el render que sigue.
        mov["cuotas_actual"] = c_actual
        mov["cuotas_total"] = c_total
        log.info(f"backfill_cuotas: {mov.get('id')} → {c_actual}/{c_total}")


def _ensure_classified(movs: list[dict]) -> None:
    """Para cada mov en la lista que no tenga suggested_category, llama al
    classifier (rule-first → Haiku) y persiste el resultado. Modifica los
    dicts in-place para que las tarjetas siguientes los muestren bien.
    También enriquece campos de cuotas desde la descripción si faltan.

    Esto cubre el caso donde el daily insertó movimientos pero falló antes
    de clasificarlos (ej. DeadlineExceeded de Firestore mid-loop), y también
    el caso de movs viejos que no tienen los campos cuotas_* porque se
    insertaron antes del feature.
    """
    from . import classifier

    # Primero rellenar cuotas desde la descripción (rápido, no requiere LLM).
    _backfill_cuotas_from_description(movs)

    pending = [m for m in movs if not m.get("suggested_category")]
    if not pending:
        return

    log.info(f"_ensure_classified: {len(pending)} movs sin categoría — clasificando con LLM…")
    if len(pending) > 5:
        _send(f"⏳ Clasificando {len(pending)} movimientos sin categoría… (puede tardar ~{len(pending)}s)")

    for mov in pending:
        try:
            cls = classifier.classify(mov.get("description", ""), float(mov.get("amount") or 0))
        except Exception as e:
            log.exception(f"Error clasificando mov {mov.get('id')}")
            continue
        try:
            db.update_classification(
                mov["id"],
                suggested_category=cls.category,
                suggested_subcategory=cls.subcategory,
                confidence=cls.confidence,
                classifier_source=cls.source,
                comercio=cls.comercio,
                tipo=cls.tipo,
                requiere_revision=cls.requiere_revision,
                pregunta_sugerida=cls.pregunta_sugerida,
            )
        except Exception:
            log.exception(f"Error persistiendo clasificación de mov {mov.get('id')}")
        # Update in-place para que el render de la tarjeta use los valores nuevos
        mov["suggested_category"] = cls.category
        mov["suggested_subcategory"] = cls.subcategory
        mov["confidence"] = cls.confidence
        mov["classifier_source"] = cls.source
        if cls.comercio:
            mov["comercio"] = cls.comercio
        if cls.tipo:
            mov["tipo"] = cls.tipo
        mov["requiere_revision"] = cls.requiere_revision
        mov["pregunta_sugerida"] = cls.pregunta_sugerida


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
        # `get_all_pending` ignora el flag `notified_at` — `get_pending` solo
        # devuelve los que no fueron notificados (utilidad del daily). En
        # /pending queremos TODOS los que sigan en estado 'pendiente', incluso
        # si ya los habíamos enviado a TG antes y el usuario aún no los aprobó.
        pendientes = db.get_all_pending()
        ignoradas = db.get_ignored()
        if not pendientes and not ignoradas:
            _send("Sin pendientes ni ignoradas.")
            return
        # Cola: pendientes primero, ignoradas al final. La paginación de 5
        # rellena con ignoradas solo cuando se acaban los pendientes — exactamente
        # lo que pediste: si hay 3 pendientes y N ignoradas, la primera tanda
        # de 5 son [3 pendientes, 2 ignoradas].
        movs = list(pendientes) + list(ignoradas)
        # Clasificar on-demand los que quedaron sin categoría (puede mandar
        # un mensaje "⏳ Clasificando N..." si hay >5).
        _ensure_classified(movs)
        # Mensaje de intro siempre, así sabés cuántos vienen antes de que
        # empiecen a llegar las tarjetas.
        if pendientes and ignoradas:
            intro = f"📋 {len(pendientes)} pendientes + {len(ignoradas)} ignoradas en cola."
        elif pendientes:
            plural = "s" if len(pendientes) != 1 else ""
            intro = f"📋 {len(pendientes)} pendiente{plural} en cola."
        else:
            plural = "s" if len(ignoradas) != 1 else ""
            intro = f"📋 Sin pendientes nuevos. Reenviando {len(ignoradas)} ignorada{plural} (por si querés re-categorizarlas)."
        _send(intro)
        telegram_notify.send_daily_batch(movs)
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
