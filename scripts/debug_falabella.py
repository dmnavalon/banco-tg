"""Debug visual del scraper de Banco Falabella.

Lanza Playwright en modo NO-headless, reusa el state actual, hace login, llega
al botón "Estado de cuenta", lo clickea, y luego intenta llegar a "Últimos
Movimientos". Dumpea TODO a /tmp/falabella_debug.txt para que sea fácil
compartir la salida.

Uso:
    cd "Gestión de Gastos"
    source .venv/bin/activate
    python -m scripts.debug_falabella
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

OUT_PATH = Path("/tmp/falabella_debug.txt")
_out_fh = OUT_PATH.open("w", encoding="utf-8")


def emit(msg: str = "") -> None:
    print(msg)
    _out_fh.write(msg + "\n")
    _out_fh.flush()


from playwright.sync_api import sync_playwright  # noqa: E402

from src import db, secrets_store  # noqa: E402
from src.utils import get_logger, project_path  # noqa: E402

log = get_logger("debug_falabella")


def _list_visible_buttons(page) -> list[str]:
    return page.evaluate("""
        () => Array.from(document.querySelectorAll('button, a[role="button"]'))
              .filter(b => b.offsetParent !== null)
              .map(b => b.textContent.trim().slice(0, 60))
              .filter(t => t.length > 0)
              .slice(0, 30)
    """)


def _dump_dom_summary(page, label: str) -> None:
    emit(f"\n===== DOM SUMMARY [{label}] =====")
    emit(f"  URL: {page.url}")
    try:
        title = page.title()
        emit(f"  Title: {title}")
    except Exception:
        pass

    summary = page.evaluate("""
        () => {
            const visible = el => el && el.offsetParent !== null;
            const labels = Array.from(document.querySelectorAll('label'))
                .filter(visible)
                .map(l => ({
                    for_attr: l.getAttribute('for'),
                    text: (l.textContent || '').trim().slice(0, 60),
                    id: l.id,
                    class: (l.className || '').slice(0, 100),
                }))
                .slice(0, 30);
            const tabs = Array.from(document.querySelectorAll('[role="tab"], .nav-tabs li, .tab, .tabs li, ul li a'))
                .filter(visible)
                .map(e => ({
                    tag: e.tagName,
                    text: (e.textContent || '').trim().slice(0, 60),
                    role: e.getAttribute('role'),
                    class: (e.className || '').slice(0, 100),
                }))
                .slice(0, 30);
            const headings = Array.from(document.querySelectorAll('h1, h2, h3, h4'))
                .filter(visible)
                .map(h => ({ tag: h.tagName, text: (h.textContent || '').trim().slice(0, 80) }))
                .slice(0, 20);
            const inputsRadio = Array.from(document.querySelectorAll('input[type="radio"], input[type="checkbox"]'))
                .map(i => ({
                    type: i.type, id: i.id, name: i.name, value: i.value,
                    checked: i.checked, visible: i.offsetParent !== null,
                }))
                .slice(0, 30);
            const tables = Array.from(document.querySelectorAll('table')).map(t => ({
                rows: t.querySelectorAll('tbody tr').length,
                first_th: (t.querySelector('thead th')?.textContent || '').trim().slice(0, 60),
            }));
            return { labels, tabs, headings, inputsRadio, tables };
        }
    """)

    emit(f"\n  HEADINGS ({len(summary.get('headings', []))}):")
    for h in summary.get('headings', []):
        emit(f"    <{h['tag']}> {h['text']!r}")

    emit(f"\n  LABELS ({len(summary.get('labels', []))}):")
    for l in summary.get('labels', []):
        emit(f"    for={l.get('for_attr')!r} id={l.get('id')!r} text={l.get('text')!r} class={l.get('class')!r}")

    emit(f"\n  TABS/NAV ({len(summary.get('tabs', []))}):")
    for t in summary.get('tabs', []):
        emit(f"    <{t['tag']}> role={t.get('role')!r} text={t.get('text')!r} class={t.get('class')!r}")

    emit(f"\n  INPUTS radio/checkbox ({len(summary.get('inputsRadio', []))}):")
    for i in summary.get('inputsRadio', []):
        emit(f"    {i}")

    emit(f"\n  TABLES ({len(summary.get('tables', []))}):")
    for t in summary.get('tables', []):
        emit(f"    rows={t['rows']} first_th={t['first_th']!r}")


def main() -> int:
    emit(f"Salida también en {OUT_PATH}")
    db.init_if_needed()
    creds = secrets_store.load("falabella")
    if not creds:
        emit("❌ No hay credenciales para falabella.")
        return 1
    rut, password = creds

    state_file = project_path("data", "state_falabella.json")

    try:
        remote_state = db.get_browser_state("falabella")
        if remote_state:
            state_file.parent.mkdir(parents=True, exist_ok=True)
            state_file.write_text(remote_state)
            emit(f"✅ State remoto bajado de Firestore → {state_file.name}")
    except Exception as e:
        emit(f"⚠️  No pude bajar state remoto: {e}")

    from adapters import falabella
    from adapters.base import click_first as cf

    emit("=" * 70)
    emit("Lanzando Chromium VISIBLE para Falabella. Reusando state.")
    emit("=" * 70)

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            slow_mo=200,
            args=["--disable-blink-features=AutomationControlled", "--disable-dev-shm-usage"],
        )
        ctx_kwargs = {
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
            ctx_kwargs["storage_state"] = str(state_file)

        context = browser.new_context(**ctx_kwargs)
        page = context.new_page()

        try:
            emit("\n▶ Llamando a falabella.login()…")
            falabella.login(page, rut, password)
            emit(f"✅ Login OK. URL: {page.url}\n")

            page.wait_for_timeout(5000)

            emit("\n▶ _dismiss_popups #1…")
            falabella._dismiss_popups(page)

            btns = _list_visible_buttons(page)
            emit(f"\nBotones visibles tras login + dismiss_popups ({len(btns)}):")
            for b in btns:
                emit(f"  - {b!r}")

            emit("\n▶ Click 'Estado de cuenta'…")
            ok = cf(page, falabella.SEL_BTN_ESTADO, timeout_ms=15000)
            emit(f"  Resultado: {'✅' if ok else '❌'}")
            if not ok:
                emit("❌ No pude clickear Estado de cuenta. Abortando.")
                return 1

            emit("\n▶ Esperando 8s para que cargue la pantalla con tabs…")
            page.wait_for_timeout(8000)

            _dump_dom_summary(page, "POST-ESTADO-DE-CUENTA")

            # Probar cada selector de SEL_TAB_MOVS individualmente.
            emit("\n▶ Probando cada selector de SEL_TAB_MOVS:")
            for sel in falabella.SEL_TAB_MOVS:
                try:
                    loc = page.locator(sel).first
                    count = page.locator(sel).count()
                    visible = False
                    try:
                        visible = loc.is_visible(timeout=1500)
                    except Exception:
                        pass
                    emit(f"  {sel!r}: count={count}, visible={visible}")
                except Exception as e:
                    emit(f"  {sel!r}: ERROR {type(e).__name__}: {e}")

            # Buscar cualquier elemento que contenga "movimientos" (variantes).
            emit("\n▶ Búsqueda libre 'movimientos' en el DOM:")
            hits = page.evaluate("""
                () => {
                    const out = [];
                    const re = /movimientos/i;
                    document.querySelectorAll('label, a, button, span, div, li').forEach(el => {
                        if (out.length > 30) return;
                        if (el.offsetParent === null) return;
                        const txt = (el.textContent || '').trim();
                        if (re.test(txt) && txt.length < 80 && el.children.length < 5) {
                            out.push({
                                tag: el.tagName,
                                text: txt.slice(0, 60),
                                id: el.id,
                                class: (el.className || '').toString().slice(0, 100),
                                for_attr: el.getAttribute('for'),
                            });
                        }
                    });
                    return out;
                }
            """)
            for h in hits:
                emit(f"  <{h['tag']}> id={h.get('id')!r} for={h.get('for_attr')!r} text={h.get('text')!r} class={h.get('class')!r}")

            # Intentar el click oficial.
            emit("\n▶ Click 'Últimos Movimientos' con click_first(SEL_TAB_MOVS)…")
            ok2 = cf(page, falabella.SEL_TAB_MOVS, timeout_ms=15000)
            emit(f"  Resultado: {'✅' if ok2 else '❌'}")

            page.wait_for_timeout(8000)

            # Screenshot para inspección visual posterior.
            try:
                page.screenshot(path="/tmp/falabella_after_click.png", full_page=True, timeout=10000)
                emit("\n  📸 Screenshot guardado en /tmp/falabella_after_click.png")
            except Exception as e:
                emit(f"\n  ⚠️ No pude tomar screenshot: {e}")

            # Inspección dentro del shadow DOM: app-movements-table.
            emit("\n▶ Inspección de app-movements-table (locator atraviesa shadow DOM):")
            try:
                amt = page.locator("app-movements-table")
                amt_count = amt.count()
                emit(f"  count(app-movements-table) = {amt_count}")
                if amt_count > 0:
                    tables_inside = amt.locator("table").count()
                    rows_inside = amt.locator("table tbody tr").count()
                    emit(f"  count(app-movements-table table) = {tables_inside}")
                    emit(f"  count(app-movements-table table tbody tr) = {rows_inside}")
                    try:
                        inner = amt.first.inner_html(timeout=5000)
                        emit(f"\n  --- inner_html de app-movements-table (primeros 3000 chars) ---")
                        emit(inner[:3000])
                        emit(f"  --- fin ---")
                    except Exception as e:
                        emit(f"  No pude leer inner_html de app-movements-table: {e}")
            except Exception as e:
                emit(f"  Error inspeccionando app-movements-table: {e}")

            # Buscar otros indicadores: loading spinners, errores visibles, mensajes.
            emit("\n▶ Indicadores de estado (spinners, errores, mensajes):")
            indicators = page.evaluate("""
                () => {
                    const visible = el => el && el.offsetParent !== null;
                    const out = { spinners: [], errors: [], any_movs_text: [] };
                    document.querySelectorAll('[class*="spinner"], [class*="loading"], [class*="loader"]').forEach(el => {
                        if (visible(el)) out.spinners.push((el.className || '').slice(0, 100));
                    });
                    document.querySelectorAll('[class*="error"], [class*="alert"], [role="alert"]').forEach(el => {
                        if (visible(el)) out.errors.push({class: (el.className||'').slice(0,80), text: (el.textContent||'').trim().slice(0,120)});
                    });
                    // Cualquier texto que mencione "movimiento" en el body.
                    const bodyText = document.body ? document.body.innerText : '';
                    const lines = bodyText.split('\\n').filter(l => /movimiento/i.test(l)).slice(0, 10);
                    out.any_movs_text = lines;
                    return out;
                }
            """)
            emit(f"  Spinners visibles: {indicators.get('spinners')}")
            emit(f"  Errores visibles: {indicators.get('errors')}")
            emit(f"  Texto con 'movimiento': {indicators.get('any_movs_text')}")

            if ok2:
                _dump_dom_summary(page, "POST-ULTIMOS-MOVIMIENTOS")
        except Exception as e:
            emit(f"\n❌ Error: {type(e).__name__}: {e}")
            import traceback
            tb = traceback.format_exc()
            emit(tb)

        emit("\n=== FIN del diagnóstico. Salida completa en /tmp/falabella_debug.txt ===")
        emit("Ventana abierta 3 min para inspección. Ctrl+C cuando termines.")
        try:
            page.wait_for_timeout(180_000)
        except KeyboardInterrupt:
            emit("Interrumpido.")
        finally:
            try:
                context.close()
            finally:
                browser.close()
    _out_fh.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
