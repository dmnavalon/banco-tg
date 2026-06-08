"""Backfill de fotos de Falabella para movs ya en DB sin foto.

Re-scrapea Falabella capturando el screenshot del modal de TODAS las filas
visibles (predicate=None), recalcula el movement_id idéntico al scraper, y para
los movs que existen en DB sin `tg_photo_file_id` ni `screenshot_b64`, persiste
el b64. Después el usuario los ve con foto vía /pending o /next.

No inserta movs nuevos ni reenvía nada: solo rellena la foto faltante.

Uso:
    cd "Gestión de Gastos"
    .venv/bin/python -m scripts.backfill_falabella_photos
"""
from __future__ import annotations
import base64
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright  # noqa: E402

from src import db, secrets_store  # noqa: E402
from src.utils import get_logger, movement_id, project_path  # noqa: E402

log = get_logger("backfill_fala")
BANK = "falabella"


def main() -> int:
    db.init_if_needed()
    creds = secrets_store.load(BANK)
    if not creds:
        print("❌ Sin credenciales falabella")
        return 1
    rut, password = creds

    state_file = project_path("data", f"state_{BANK}.json")
    remote_state = db.get_browser_state(BANK)
    if remote_state:
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(remote_state)

    from adapters import falabella

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True, slow_mo=150,
            args=["--disable-blink-features=AutomationControlled", "--disable-dev-shm-usage",
                  "--disable-features=IsolateOrigins,site-per-process,AutomationControlled",
                  "--disable-site-isolation-trials", "--disable-web-security", "--no-sandbox"],
        )
        ctx = browser.new_context(
            viewport={"width": 1920, "height": 1080}, locale="es-CL",
            timezone_id="America/Santiago",
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
            storage_state=str(state_file) if state_file.exists() else None,
        )
        page = ctx.new_page()
        try:
            falabella.login(page, rut, password)
            # Captura TODAS las fotos (sin filtrar por nuevos)
            raw_movements = falabella.fetch_movements(page, screenshot_predicate=None)
        finally:
            ctx.close()
            browser.close()

    print(f"\nMovimientos leídos del sitio: {len(raw_movements)}")

    # Recalcular movement_id idéntico a scraper.run_for_bank (con dup_idx).
    dup_counts: dict[tuple, int] = {}
    updated, no_match, already_had, no_shot = 0, 0, 0, 0
    for raw in raw_movements:
        if not raw or not raw.get("date") or not raw.get("description"):
            continue
        amount = float(raw.get("amount") or 0.0)
        account = raw.get("account") or BANK
        dup_key = (raw["date"], amount, raw["description"], account)
        dup_idx = dup_counts.get(dup_key, 0)
        dup_counts[dup_key] = dup_idx + 1
        mid = movement_id(
            date_iso=raw["date"], amount=amount, description=raw["description"],
            bank=BANK, account=account, dup_idx=dup_idx,
        )
        shot = raw.get("screenshot_bytes")
        doc = db.get_movement_by_id(mid)
        if doc is None:
            no_match += 1
            continue
        if doc.get("tg_photo_file_id") or doc.get("screenshot_b64"):
            already_had += 1
            continue
        if not shot:
            no_shot += 1
            continue
        db.set_movement_screenshot_b64(mid, base64.b64encode(shot).decode("ascii"))
        updated += 1
        print(f"  ✅ backfill {raw['date']} | {raw['description'][:38]}")

    print(f"\n=== RESUMEN ===")
    print(f"  Rellenados (b64 persistido): {updated}")
    print(f"  Ya tenían foto/b64:          {already_had}")
    print(f"  Sin screenshot capturado:    {no_shot}")
    print(f"  No están en DB (no match):   {no_match}")
    if updated:
        print(f"\n👉 Ahora manda /pending o /next en Telegram para verlos con foto.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
