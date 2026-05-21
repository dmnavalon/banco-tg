"""Refresh fotos de movimientos pendientes huérfanos.

Cuando el daily falla DESPUÉS de insertar movimientos en Firestore pero ANTES
de mandar las tarjetas (ej. DeadlineExceeded en update_classification), los
`screenshot_bytes` capturados por el adapter se pierden en memoria sin
persistirse como `tg_photo_file_id`.

Este script:
  1. Lee movimientos `pendiente` con `tg_photo_file_id` vacío del banco objetivo.
  2. Re-scrapea ese banco usando el adapter (login + paginación + screenshots).
  3. Por cada movimiento scrapeado, calcula su `mov_id` (hash determinístico).
  4. Si matchea con un pendiente huérfano, manda la tarjeta con foto vía
     `telegram_notify.send_movement_cards`. El sendPhoto persiste el file_id.

Uso:
    cd "/Users/diego/Desktop/Desarrollos DMN/Control de Gastos/Gestión de Gastos"
    source .venv/bin/activate
    python -m scripts.refresh_photos falabella

Solo Falabella tiene captura de modal por ahora (BCh no implementa screenshots).

Side-effects:
  - Re-manda hasta 5 tarjetas por batch a Telegram (con paginación /next).
  - Las tarjetas viejas (sin foto) quedan en el chat — Telegram no permite
    borrarlas si tienen >48h o si no se guardó su message_id. Diego puede
    ignorarlas o borrarlas manualmente.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Permite correr el script como módulo desde la raíz del proyecto.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright  # noqa: E402

from src import db, secrets_store, telegram_notify  # noqa: E402
from src.utils import get_logger, movement_id, project_path  # noqa: E402

log = get_logger("refresh_photos")

VALID_BANKS = {"falabella"}  # solo Falabella tiene capture de modal por ahora


def _load_pending_without_photo(bank: str) -> dict[str, dict]:
    """Devuelve un mapping mov_id → mov_dict de los movimientos `pendiente` del
    `bank` que no tienen `tg_photo_file_id`."""
    snaps = (
        db._db()  # type: ignore[attr-defined]
        .collection("movements")
        .where(filter=db.fstore.FieldFilter("bank", "==", bank))  # type: ignore[attr-defined]
        .where(filter=db.fstore.FieldFilter("status", "==", "pendiente"))  # type: ignore[attr-defined]
        .get()
    )
    out: dict[str, dict] = {}
    for s in snaps:
        info = s.to_dict()
        if not info.get("tg_photo_file_id"):
            out[s.id] = info
    return out


def _scrape_with_screenshots(bank: str) -> list[dict]:
    """Re-corre el adapter `bank` con Playwright y devuelve todos los movimientos
    parseados (incluyendo los que ya existen en Firestore), con sus screenshot_bytes."""
    try:
        creds = secrets_store.load(bank)
    except secrets_store.CredentialDecryptError as e:
        raise RuntimeError(str(e)) from e
    if not creds:
        raise RuntimeError(f"No hay credenciales para {bank}. Configura con /cred {bank}.")
    rut, password = creds

    state_file = project_path("data", f"state_{bank}.json")

    if bank == "falabella":
        from adapters import falabella as adapter
    else:
        raise RuntimeError(f"Banco no soportado para refresh: {bank}")

    log.info(f"Lanzando Playwright para {bank} (refresh-only, no inserta en Firestore)…")
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--disable-dev-shm-usage", "--no-sandbox"],
        )
        ctx_kwargs: dict = {
            "viewport": {"width": 1920, "height": 1080},
            "locale": "es-CL",
            "timezone_id": "America/Santiago",
            "user_agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/130.0.0.0 Safari/537.36"
            ),
        }
        if state_file.exists():
            try:
                json.loads(state_file.read_text())
                ctx_kwargs["storage_state"] = str(state_file)
                log.info(f"Reusando sesión persistida en {state_file.name}")
            except Exception:
                log.warning(f"{state_file.name} corrupto, ignorando.")

        context = browser.new_context(**ctx_kwargs)
        page = context.new_page()
        try:
            adapter.login(page, rut, password)
            try:
                state_file.parent.mkdir(parents=True, exist_ok=True)
                context.storage_state(path=str(state_file))
            except Exception as e:
                log.warning(f"No pude guardar storage_state: {e}")
            raws = adapter.fetch_movements(page)
        finally:
            try:
                context.close()
            finally:
                browser.close()
    log.info(f"{bank}: {len(raws)} movimientos parseados (con screenshots).")
    return raws


def _build_mov_id(raw: dict, bank: str, dup_idx: int = 0) -> str:
    return movement_id(
        date_iso=raw["date"],
        amount=float(raw.get("amount") or 0),
        description=raw["description"],
        bank=bank,
        account=raw.get("account") or bank,
        dup_idx=dup_idx,
    )


def main(bank: str) -> int:
    bank = bank.lower()
    if bank not in VALID_BANKS:
        log.error(f"Banco no soportado: {bank}. Opciones: {', '.join(sorted(VALID_BANKS))}")
        return 1

    db.init_if_needed()
    pendientes = _load_pending_without_photo(bank)
    log.info(f"Pendientes sin foto en {bank}: {len(pendientes)}")
    if not pendientes:
        log.info("Nada que refrescar. Saliendo.")
        return 0

    raws = _scrape_with_screenshots(bank)
    matched: list[dict] = []
    dup_counts: dict[tuple, int] = {}
    for raw in raws:
        if not raw or not raw.get("date") or not raw.get("description"):
            continue
        amount = float(raw.get("amount") or 0)
        dup_key = (raw["date"], amount, raw["description"], raw.get("account") or bank)
        dup_idx = dup_counts.get(dup_key, 0)
        dup_counts[dup_key] = dup_idx + 1
        mid = _build_mov_id(raw, bank, dup_idx=dup_idx)
        if mid not in pendientes:
            continue
        # Enriquecer el dict de Firestore con los screenshot_bytes recién capturados,
        # para que `_send_one_card` haga sendPhoto y persista el file_id.
        mov = pendientes[mid]
        mov["screenshot_bytes"] = raw.get("screenshot_bytes")
        matched.append(mov)
    log.info(f"Matched {len(matched)} de {len(pendientes)} pendientes huérfanos.")
    if not matched:
        log.warning("Ningún match: el scrape no trajo los movimientos pendientes (puede que Falabella ya los confirmó como otra fecha, o cambió la descripción).")
        return 0

    log.info(f"Re-mandando {len(matched)} tarjetas (en lotes de 5; usá /next en TG para avanzar).")
    telegram_notify.send_movement_cards(matched)
    log.info("Listo. Las tarjetas viejas sin foto siguen en el chat — bórralas a mano si querés limpieza.")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python -m scripts.refresh_photos <falabella>")
        sys.exit(1)
    sys.exit(main(sys.argv[1]))
