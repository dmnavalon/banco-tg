from __future__ import annotations

import re
import sys
import time
import traceback
from datetime import datetime
from typing import Callable

from . import classifier, db, scraper, secrets_store, telegram_notify
from .utils import get_logger

log = get_logger("run_daily")


class TwoFATimeout(Exception):
    pass


def make_otp_provider(timeout_seconds: int = 300) -> Callable[[str], str]:
    """Devuelve una callable(bank) que pide OTP por TG y espera respuesta.

    Asume que `src.bot` está corriendo y registra cada mensaje entrante en
    `telegram_log` con direction='in'. El provider hace polling sobre la
    tabla buscando mensajes posteriores al instante del pedido que
    empiecen con 'otp '.
    """
    def provider(bank: str) -> str:
        marker = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        telegram_notify.send_message(
            f"[{bank}] El banco pidió código 2FA. Respóndeme con: otp <código>. "
            f"Tienes {timeout_seconds // 60} min."
        )
        log.info(f"Esperando OTP para {bank} (marker={marker})")

        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            text = db.get_latest_otp(marker)
            if text:
                m = re.match(r"^\s*otp\s+(\S+)", text, re.IGNORECASE)
                if m:
                    code = m.group(1).strip()
                    log.info(f"OTP recibido para {bank}.")
                    telegram_notify.send_message(f"[{bank}] OTP recibido, continuando…")
                    return code

            time.sleep(2)

        raise TwoFATimeout(f"OTP no recibido para {bank} en {timeout_seconds}s.")

    return provider


def run_for_bank_full(bank: str, otp_provider: Callable[[str], str] | None = None) -> list[dict]:
    """Loguea, scrapea, clasifica los nuevos. Devuelve los movs nuevos clasificados."""
    creds = secrets_store.load(bank)
    if not creds:
        raise RuntimeError(f"No hay credenciales configuradas para {bank}.")
    rut, password = creds

    new_movements = scraper.run_for_bank(bank, rut, password, otp_provider)

    classified: list[dict] = []
    for mov in new_movements:
        cls = classifier.classify(mov["description"], mov["amount"])
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
        classified.append({
            **mov,
            "suggested_category": cls.category,
            "suggested_subcategory": cls.subcategory,
            "confidence": cls.confidence,
            "classifier_source": cls.source,
            "comercio": cls.comercio,
            "tipo": cls.tipo,
            "requiere_revision": cls.requiere_revision,
            "pregunta_sugerida": cls.pregunta_sugerida,
        })
    return classified


def main() -> int:
    db.init_if_needed()
    banks = secrets_store.list_configured()
    if not banks:
        telegram_notify.send_message("No hay bancos configurados. Usa /cred <banco> para configurar.")
        log.warning("Sin bancos configurados — saliendo.")
        return 0

    otp_provider = make_otp_provider()
    all_new: list[dict] = []
    errors: list[str] = []

    for bank in banks:
        try:
            new = run_for_bank_full(bank, otp_provider)
            all_new.extend(new)
            log.info(f"{bank}: {len(new)} movimientos nuevos.")
        except Exception as e:
            tb = traceback.format_exc()
            db.record_error(component=f"run_daily.{bank}", message=str(e), traceback=tb)
            errors.append(f"[{bank}] {type(e).__name__}: {e}")
            telegram_notify.send_message(f"[{bank}] Error: {type(e).__name__}: {e}")

    if all_new:
        telegram_notify.send_daily_batch(all_new)
    else:
        telegram_notify.send_message("Sin movimientos nuevos hoy." + (
            "\n\nErrores:\n" + "\n".join(errors) if errors else ""
        ))

    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
