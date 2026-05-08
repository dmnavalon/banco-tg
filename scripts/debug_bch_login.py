"""Debug visual del login de Banco de Chile.

Lanza Playwright en modo NO-headless (Chromium visible) usando las credenciales
del secrets_store. Vas a poder ver con tus propios ojos:
  1. Cómo se llena el RUT y la clave.
  2. Si BCh muestra algún CAPTCHA o pantalla anti-bot que el adapter no detecta.
  3. Si después del submit aparece un mensaje de error específico.
  4. Si BCh detecta el navegador automatizado y rechaza el login como medida
     evasiva (en lugar de mostrar CAPTCHA visible).

El script PAUSA después del submit para que puedas inspeccionar la pantalla
todo el tiempo que quieras. Cierra con Ctrl+C cuando termines.

Uso:
    cd "Gestión de Gastos"
    source .venv/bin/activate
    python -m scripts.debug_bch_login
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright  # noqa: E402

from src import db, secrets_store  # noqa: E402
from src.utils import get_logger, project_path  # noqa: E402

log = get_logger("debug_bch_login")


def main() -> int:
    db.init_if_needed()
    creds = secrets_store.load("bancochile")
    if not creds:
        print("❌ No hay credenciales para bancochile. Configurá con /cred bancochile.")
        return 1
    rut, password = creds

    state_file = project_path("data", "state_bancochile.json")

    from adapters import bancochile

    print("=" * 70)
    print("Lanzando Chromium en modo VISIBLE.")
    print("Vas a ver el flujo de login en una ventana separada.")
    print("Cuando el adapter llegue al submit, inspeccioná visualmente:")
    print("  - ¿BCh muestra CAPTCHA?")
    print("  - ¿El form se llenó con los valores correctos (RUT + clave)?")
    print("  - ¿Después del submit aparece error de credenciales o algo más?")
    print("Cuando termines de inspeccionar, cierra la ventana o Ctrl+C aquí.")
    print("=" * 70)

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            slow_mo=200,  # delay artificial entre acciones para que se vea bien
            args=["--disable-blink-features=AutomationControlled", "--disable-dev-shm-usage"],
        )
        ctx_kwargs = {
            "viewport": {"width": 1440, "height": 900},
            "locale": "es-CL",
            "timezone_id": "America/Santiago",
            "user_agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/130.0.0.0 Safari/537.36"
            ),
        }
        # NO usamos storage_state aquí — queremos un login fresh para reproducir el bug.
        context = browser.new_context(**ctx_kwargs)
        page = context.new_page()

        try:
            log.info("Llamando a bancochile.login() con HEADLESS=false…")
            bancochile.login(page, rut, password)
            log.info(f"Login OK. URL final: {page.url}")
            print("\n✅ Login exitoso. Inspecciona la ventana — debería estar en el portal.")
        except Exception as e:
            log.error(f"Login falló: {type(e).__name__}: {e}")
            print(f"\n❌ Login falló: {type(e).__name__}: {e}")
            print(f"   URL actual: {page.url}")
            print(f"\nInspeccioná la ventana visualmente:")
            print(f"  - ¿Aparece CAPTCHA?")
            print(f"  - ¿Hay algún mensaje de error (rojo) sobre el form?")
            print(f"  - ¿BCh detectó que es navegador automatizado?")
            print(f"  - ¿El form quedó con los valores correctos?")

        print("\nLa ventana se mantiene abierta 5 minutos para inspección.")
        print("Cierra con Ctrl+C cuando termines.")
        try:
            page.wait_for_timeout(300_000)
        except KeyboardInterrupt:
            print("Interrumpido por usuario.")
        finally:
            try:
                context.close()
            finally:
                browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
