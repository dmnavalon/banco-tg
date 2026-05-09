"""Debug visual + estructural de la tabla de movimientos de Banco de Chile.

Reusa el state actual (cookies de la sesión activa), llega a la tabla siguiendo
el mismo flujo que el daily, y dumpea:
  1. Cuántas filas detecta el selector actual.
  2. HTML completo de la primera fila (para ver clases reales del DOM).
  3. Para cada fila: clases de cada <td> + texto crudo.
  4. Qué texto extrae con los selectores actuales (cdk-column-*) en cada columna.
  5. Qué retorna _parse_row para cada fila.

Uso:
    cd "Gestión de Gastos"
    source .venv/bin/activate
    python -m scripts.debug_bch_table
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

log = get_logger("debug_bch_table")


def main() -> int:
    db.init_if_needed()
    creds = secrets_store.load("bancochile")
    if not creds:
        print("❌ No hay credenciales para bancochile.")
        return 1
    rut, password = creds

    state_file = project_path("data", "state_bancochile.json")

    # Bajar state remoto si existe (sino usar el local).
    try:
        remote_state = db.get_browser_state("bancochile")
        if remote_state:
            state_file.parent.mkdir(parents=True, exist_ok=True)
            state_file.write_text(remote_state)
            print(f"✅ State remoto bajado de Firestore → {state_file.name}")
    except Exception as e:
        print(f"⚠️  No pude bajar state remoto: {e}")

    from adapters import bancochile

    # Monkey-patch de _read_current_page para dumpear DOM antes de parsear.
    original_read = bancochile._read_current_page
    original_parse = bancochile._parse_row

    def patched_read(page):
        rows_loc = page.locator(bancochile.SEL_TABLE_ROW)
        n = rows_loc.count()
        print(f"\n{'=' * 70}")
        print(f"DIAG: {n} filas detectadas con selector: {bancochile.SEL_TABLE_ROW!r}")
        print(f"{'=' * 70}")

        if n == 0:
            print("⚠️  Cero filas. Inspeccionando contenedor…")
            try:
                table_count = page.locator("table").count()
                bch_table_count = page.locator("table.bch-table").count()
                tbody_count = page.locator("tbody").count()
                print(f"  - <table> en DOM: {table_count}")
                print(f"  - table.bch-table: {bch_table_count}")
                print(f"  - <tbody>: {tbody_count}")
                if bch_table_count > 0:
                    inner = page.locator("table.bch-table").first.inner_html()
                    print(f"  - HTML de table.bch-table (1500 chars):\n{inner[:1500]}")
            except Exception as e:
                print(f"  Error inspeccionando: {e}")
            return []

        # Dump primera fila completa.
        try:
            first_html = rows_loc.first.inner_html()
            print(f"\n--- HTML primera fila (1500 chars) ---")
            print(first_html[:1500])
            print(f"--- fin HTML ---\n")
        except Exception as e:
            print(f"⚠️  No pude leer HTML primera fila: {e}")

        # Inspección de celdas por fila (primeras 5).
        muestras = min(n, 5)
        for i in range(muestras):
            row = rows_loc.nth(i)
            try:
                cells = row.evaluate("""
                    r => Array.from(r.children).map(c => ({
                        tag: c.tagName,
                        class: c.className,
                        text: (c.innerText || '').slice(0, 60).replace(/\\s+/g, ' ').trim()
                    }))
                """)
                print(f"\nFila {i}: {len(cells)} celdas")
                for j, c in enumerate(cells):
                    print(f"  [{j}] <{c['tag']} class={c['class']!r}> text={c['text']!r}")
            except Exception as e:
                print(f"Fila {i}: error inspeccionando celdas: {e}")

        # Probar selectores actuales.
        print(f"\n{'=' * 70}")
        print(f"Probando selectores actuales del adapter:")
        print(f"  fecha     → td.cdk-column-fechaContable")
        print(f"  desc      → td.cdk-column-descripcion")
        print(f"  cargo     → td.cdk-column-cargo")
        print(f"  abono     → td.cdk-column-abono")
        print(f"{'=' * 70}")

        for i in range(muestras):
            row = rows_loc.nth(i)
            try:
                fecha = row.locator("td.cdk-column-fechaContable").first.inner_text(timeout=2000).strip() if row.locator("td.cdk-column-fechaContable").count() else "<no encontrado>"
            except Exception as e:
                fecha = f"<ERROR: {e}>"
            try:
                desc = row.locator("td.cdk-column-descripcion").first.inner_text(timeout=2000).strip() if row.locator("td.cdk-column-descripcion").count() else "<no encontrado>"
            except Exception as e:
                desc = f"<ERROR: {e}>"
            try:
                cargo = row.locator("td.cdk-column-cargo").first.inner_text(timeout=2000).strip() if row.locator("td.cdk-column-cargo").count() else "<no encontrado>"
            except Exception as e:
                cargo = f"<ERROR: {e}>"
            try:
                abono = row.locator("td.cdk-column-abono").first.inner_text(timeout=2000).strip() if row.locator("td.cdk-column-abono").count() else "<no encontrado>"
            except Exception as e:
                abono = f"<ERROR: {e}>"
            parsed = original_parse(fecha, desc, cargo, abono)
            print(f"\nFila {i}:")
            print(f"  fecha={fecha!r}")
            print(f"  desc={desc!r}")
            print(f"  cargo={cargo!r}")
            print(f"  abono={abono!r}")
            print(f"  → _parse_row: {parsed}")

        print(f"\n{'=' * 70}\n")
        return original_read(page)

    bancochile._read_current_page = patched_read

    print("=" * 70)
    print("Lanzando Chromium VISIBLE. Reusando state actual.")
    print("=" * 70)

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            slow_mo=200,
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
        if state_file.exists():
            ctx_kwargs["storage_state"] = str(state_file)

        context = browser.new_context(**ctx_kwargs)
        page = context.new_page()

        try:
            print("\n▶ Llamando a bancochile.login() (reusará state si está vigente)…")
            bancochile.login(page, rut, password)
            print(f"✅ Login OK. URL: {page.url}\n")

            print("▶ Llamando a bancochile.fetch_movements()…\n")
            movs = bancochile.fetch_movements(page)
            print(f"\n✅ fetch_movements retornó {len(movs)} movimientos totales.")
            if movs:
                print(f"\nPrimeros 3:")
                for m in movs[:3]:
                    print(f"  {m}")
        except Exception as e:
            print(f"\n❌ Error: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()

        print("\nVentana abierta 5 min para que inspecciones manualmente.")
        print("Ctrl+C cuando termines.")
        try:
            page.wait_for_timeout(300_000)
        except KeyboardInterrupt:
            print("Interrumpido.")
        finally:
            try:
                context.close()
            finally:
                browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
