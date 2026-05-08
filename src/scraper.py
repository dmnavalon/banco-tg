from __future__ import annotations

import importlib
import json
import os
from typing import Callable

from playwright.sync_api import sync_playwright

from . import db
from .utils import get_logger, movement_id, project_path

log = get_logger("scraper")


def _state_path(bank: str):
    return project_path("data", f"state_{bank}.json")


def _adapter_for(bank: str):
    bank = bank.lower()
    if bank not in {"falabella", "bancochile"}:
        raise ValueError(f"Banco no soportado: {bank}")
    return importlib.import_module(f"adapters.{bank}")


def run_for_bank(bank: str, rut: str, password: str, otp_provider: Callable[[str], str] | None = None) -> list[dict]:
    """Loguea, scrapea y persiste movimientos nuevos. Devuelve los nuevos."""
    bank = bank.lower()
    adapter = _adapter_for(bank)
    state_file = _state_path(bank)
    headless = os.environ.get("HEADLESS", "false").lower() == "true"

    log.info(f"Iniciando scrape de {bank} (headless={headless})")

    launch_args = [
        "--disable-blink-features=AutomationControlled",
        "--disable-dev-shm-usage",
    ]
    if headless:
        # En Docker/Railway no hay sandbox por defecto; sin --no-sandbox falla.
        launch_args.append("--no-sandbox")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless, args=launch_args)
        context_kwargs: dict = {
            "user_agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/130.0.0.0 Safari/537.36"
            ),
            "viewport": {"width": 1920, "height": 1080},
            "locale": "es-CL",
            "timezone_id": "America/Santiago",
        }
        # Cargar state desde Firestore primero (sincroniza Mac↔Railway). Si no
        # hay en Firestore, fallback al archivo local (legacy, solo Mac).
        remote_state = db.get_browser_state(bank)
        if remote_state:
            try:
                json.loads(remote_state)  # validar
                state_file.parent.mkdir(parents=True, exist_ok=True)
                state_file.write_text(remote_state)
                context_kwargs["storage_state"] = str(state_file)
                log.info(f"Reusando sesión persistida desde Firestore (sync para {bank}).")
            except Exception as e:
                log.warning(f"State de Firestore corrupto para {bank}, ignorando: {e}")
        elif state_file.exists():
            try:
                json.loads(state_file.read_text())
                context_kwargs["storage_state"] = str(state_file)
                log.info(f"Reusando sesión persistida local en {state_file.name} (Firestore vacío).")
            except Exception:
                log.warning(f"{state_file.name} corrupto, ignorando.")

        context = browser.new_context(**context_kwargs)
        page = context.new_page()

        try:
            adapter.login(page, rut, password, otp_provider)
            # Guardar state local Y subirlo a Firestore para que el otro lado
            # (Mac↔Railway) lo reuse sin tener que re-loguear.
            try:
                state_file.parent.mkdir(parents=True, exist_ok=True)
                context.storage_state(path=str(state_file))
                log.info(f"Sesión guardada en {state_file.name}")
                try:
                    state_json = state_file.read_text()
                    db.set_browser_state(bank, state_json)
                    log.info(f"Sesión sincronizada a Firestore para {bank}.")
                except Exception as e:
                    log.warning(f"No pude subir state a Firestore: {e}")
            except Exception as e:
                log.warning(f"No pude guardar storage_state: {e}")

            raw_movements = adapter.fetch_movements(page)
        finally:
            try:
                context.close()
            finally:
                browser.close()

    new_movements: list[dict] = []
    for raw in raw_movements:
        if not raw or not raw.get("date") or not raw.get("description"):
            continue
        amount = float(raw.get("amount") or 0.0)
        mid = movement_id(
            date_iso=raw["date"],
            amount=amount,
            description=raw["description"],
            bank=bank,
            account=raw.get("account") or bank,
        )
        # raw_blob se serializa a JSON: extraer los bytes del screenshot antes (no son JSON-friendly).
        screenshot_bytes = raw.pop("screenshot_bytes", None) if isinstance(raw, dict) else None
        inserted = db.insert_movement(
            mov_id=mid,
            date_iso=raw["date"],
            description=raw["description"],
            amount=amount,
            movement_type=raw.get("movement_type"),
            account=raw.get("account") or bank,
            bank=bank,
            raw_blob=json.dumps(raw, ensure_ascii=False),
            persona=raw.get("persona"),
            cuotas_actual=raw.get("cuotas_actual"),
            cuotas_total=raw.get("cuotas_total"),
            cuota_monto=raw.get("cuota_monto"),
        )
        if inserted:
            new_movements.append({
                "id": mid,
                "date": raw["date"],
                "description": raw["description"],
                "amount": amount,
                "movement_type": raw.get("movement_type"),
                "account": raw.get("account") or bank,
                "bank": bank,
                "persona": raw.get("persona"),
                "cuotas_actual": raw.get("cuotas_actual"),
                "cuotas_total": raw.get("cuotas_total"),
                "cuota_monto": raw.get("cuota_monto"),
                "screenshot_bytes": screenshot_bytes,
            })

    log.info(f"{bank}: {len(new_movements)} movimientos nuevos (de {len(raw_movements)} leídos)")
    return new_movements
