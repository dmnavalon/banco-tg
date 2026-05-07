from __future__ import annotations

import re
from typing import Callable

from playwright.sync_api import Page, TimeoutError as PWTimeout

from src.utils import get_logger, parse_chilean_date, parse_clp_amount

from .base import (
    LoginFailed,
    ScraperBroken,
    TwoFARequired,
    click_first,
    fill_first,
    first_visible,
)

log = get_logger("adapters.falabella")

LOGIN_URL = "https://www.bancofalabella.cl/login"

# La página inicial NO muestra los inputs hasta clickear "Mi cuenta", que abre
# el panel de login. Si la sesión está activa este botón redirige al dashboard.
SEL_BTN_MI_CUENTA = [
    'button[aria-label="Button"]:has-text("Mi cuenta")',
    'button:has-text("Mi cuenta")',
    'button[class*="button_button__primary"]:has-text("Mi cuenta")',
]

SEL_RUT = [
    'input[placeholder="RUT"]',
    'input[maxlength="10"][type="text"]',
]
SEL_PASS = [
    'input[placeholder="Clave Internet"]',
    'input[type="password"][maxlength="6"]',
]
SEL_SUBMIT = [
    "#desktop-login",
    'button[data-testid="desktop-login"]',
    'button:has-text("Ingresar")',
]

SEL_BTN_ESTADO = [
    "#cardAccount0",
    'button:has-text("Estado de cuenta")',
    'button.btn-grey:has-text("Estado de cuenta")',
]

# Selectores SIEMPRE contextualizados a un overlay explícito — evita matchear
# "Cerrar sesión" del header u otros botones de cierre fuera de modales.
SEL_POPUP_CLOSE = [
    '[role="dialog"] button[aria-label*="cerrar" i]',
    '[role="dialog"] button[aria-label*="close" i]',
    '[class*="modal"] button[aria-label*="cerrar" i]',
    '[class*="modal"] button[aria-label*="close" i]',
    '[class*="popup"] button[aria-label*="cerrar" i]',
    '[class*="popup"] button[aria-label*="close" i]',
    '[class*="overlay"] button[aria-label*="cerrar" i]',
    '[class*="overlay"] button[aria-label*="close" i]',
    '[role="dialog"] button[title*="cerrar" i]',
    '[class*="modal"] button[title*="cerrar" i]',
    '[class*="modal"] button[class*="close"]',
    '[class*="popup"] button[class*="close"]',
    '[class*="overlay"] [class*="close"]',
    'button.modal-close',
    '.modal-header .close',
    '[role="dialog"] button:has-text("×")',
    '[role="dialog"] button:has-text("✕")',
    '[class*="modal"] button:has-text("×")',
    '[class*="modal"] button:has-text("✕")',
]
SEL_TAB_MOVS = [
    'label[for="last-movements"]',
    'label:has-text("Últimos movimientos")',
    'label:has-text("Últimos Movimientos")',
]
SEL_TABLE = [
    "app-movements-table table",
    "#LastMovements-panel table",
    "table.table-hover",
]

# Heurística genérica para 2FA — Falabella suele pedir un OTP corto (4-6 dígitos)
# tras login. Los selectores son tentativos: ajustar al ver HTML real (TODO v2).
SEL_OTP_INPUT = [
    'input[autocomplete="one-time-code"]',
    'input[name*="otp" i]',
    'input[id*="otp" i]',
    'input[placeholder*="código" i]',
    'input[maxlength="6"][type="text"]:not([placeholder*="RUT" i])',
    'input[maxlength="4"][type="text"]',
]
SEL_OTP_SUBMIT = [
    'button:has-text("Validar")',
    'button:has-text("Aceptar")',
    'button:has-text("Confirmar")',
    'button:has-text("Continuar")',
    'button[type="submit"]',
]

DASHBOARD_PATTERN = re.compile(
    r"bancofalabella\.cl/(web-clientes|home|dashboard|resumen|productos)|"
    r"web2\.bancofalabella\.cl",
    re.IGNORECASE,
)


def login(page: Page, rut: str, password: str, otp_provider: Callable[[str], str] | None = None) -> None:
    if not re.fullmatch(r"\d{6}", password):
        raise LoginFailed("La clave de internet de Falabella debe ser exactamente 6 dígitos.")

    log.info("Navegando al login de Falabella…")
    page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30000)

    page.wait_for_timeout(2000)

    if DASHBOARD_PATTERN.search(page.url):
        log.info("Sesión persistida activa. Saltando login.")
        return

    # En la página inicial los inputs están ocultos. Hay que clickear "Mi cuenta"
    # para que aparezca el panel con RUT y clave. Si la sesión está activa, este
    # click puede redirigir directo al dashboard.
    if click_first(page, SEL_BTN_MI_CUENTA, timeout_ms=8000):
        log.info("Click en 'Mi cuenta' — esperando panel de login…")
        page.wait_for_timeout(2000)
        if DASHBOARD_PATTERN.search(page.url):
            log.info("Sesión persistida activa tras click 'Mi cuenta'. Saltando login.")
            return
    else:
        log.warning("No encontré el botón 'Mi cuenta'. Quizá los inputs ya están visibles.")

    # Esperar que el input de RUT esté visible (puede tardar tras animación del panel).
    rut_input = first_visible(page, SEL_RUT, timeout_ms=8000)
    if not rut_input:
        raise ScraperBroken("No encontré el campo RUT en Falabella tras abrir 'Mi cuenta'.")
    rut_input.fill(rut)
    if not fill_first(page, SEL_PASS, password):
        raise ScraperBroken("No encontré el campo de clave en Falabella.")

    try:
        page.wait_for_function(
            "() => { const b = document.querySelector('#desktop-login'); return b && !b.disabled; }",
            timeout=8000,
        )
    except PWTimeout:
        log.warning("Botón #desktop-login no se habilitó en 8s — intentaré click igual.")

    if not click_first(page, SEL_SUBMIT):
        raise ScraperBroken("No pude clickear el botón Ingresar de Falabella.")

    try:
        page.wait_for_url(DASHBOARD_PATTERN, timeout=20000)
        log.info("Login Falabella OK (sin 2FA).")
        return
    except PWTimeout:
        pass

    otp_input = first_visible(page, SEL_OTP_INPUT, timeout_ms=3000)
    if otp_input:
        if otp_provider is None:
            raise TwoFARequired("Falabella pide 2FA y no hay otp_provider configurado.")
        code = otp_provider("falabella")
        otp_input.fill(code)
        if not click_first(page, SEL_OTP_SUBMIT):
            raise ScraperBroken("No encontré botón para confirmar el OTP en Falabella.")
        try:
            page.wait_for_url(DASHBOARD_PATTERN, timeout=20000)
            log.info("Login Falabella OK con 2FA.")
            return
        except PWTimeout:
            raise LoginFailed("Falabella no llegó al dashboard tras enviar OTP.")

    if DASHBOARD_PATTERN.search(page.url):
        log.info("Login Falabella OK (URL ya coincide con dashboard).")
        return

    raise LoginFailed(f"Falabella no avanzó al dashboard tras login. URL actual: {page.url}")


def _dismiss_popups(page: Page) -> None:
    """Cierra popups/overlays publicitarios de alta z-index. Itera hasta 3 veces."""
    for attempt in range(3):
        closed_any = False

        # 1. Botones de cierre conocidos — sin break para cerrar múltiples popups
        for sel in SEL_POPUP_CLOSE:
            try:
                loc = page.locator(sel).first
                if loc.is_visible(timeout=600):
                    loc.click(timeout=2000)
                    log.info(f"Popup cerrado con: {sel}")
                    page.wait_for_timeout(500)
                    closed_any = True
            except Exception:
                continue

        # 2. Escape
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)

        # 3. JS: ocultar todos los overlays de z-index alto (no solo los del centro)
        removed = page.evaluate("""
            () => {
                let count = 0;
                const area = window.innerWidth * window.innerHeight;
                document.querySelectorAll('*').forEach(el => {
                    if (el.tagName === 'BODY' || el.tagName === 'HTML') return;
                    const z = parseInt(window.getComputedStyle(el).zIndex, 10);
                    const r = el.getBoundingClientRect();
                    if (z > 100 && r.width * r.height > area * 0.15) {
                        el.style.display = 'none';
                        count++;
                    }
                });
                return count;
            }
        """)
        if removed:
            log.info(f"Overlays ocultados via JS (intento {attempt+1}): {removed}")
            page.wait_for_timeout(400)

        # Si no hubo popups en este intento, salir del loop
        if not closed_any and not removed:
            break


def _select_confirmed_table(page: Page):
    """Encuentra la tabla de movimientos confirmados (NO la de Pendientes de confirmación).

    Falabella muestra DOS tablas en "Últimos Movimientos":
      1. "Pendientes de confirmación" — compras del día sin fecha asentada. IGNORAR.
      2. "Fecha de compras" / movimientos confirmados — ESTA es la que queremos.

    Las identificamos por el texto del primer ``<th>``. Si dice "pendiente", la salta.
    Devuelve (table_locator, headers) o (None, []) si no encuentra.
    """
    container = page.locator("app-movements-table")
    tables = container.locator("table").all()
    if not tables:
        return None, []

    best, max_rows, best_headers = None, -1, []
    for t in tables:
        headers = [h.inner_text().strip() for h in t.locator("thead tr th").all()]
        first_header_lower = (headers[0] if headers else "").lower()
        if "pendiente" in first_header_lower:
            log.info(f"Saltando tabla de pendientes (header[0]={headers[0]!r})")
            continue
        n = t.locator("tbody tr").count()
        log.info(f"Tabla candidata (header[0]={headers[0] if headers else '∅'!r}): {n} filas")
        if n > max_rows:
            max_rows, best, best_headers = n, t, headers
    return best, best_headers


def _capture_modal_screenshot(page: Page, row) -> bytes | None:
    """Click en una fila para abrir el modal de detalle, captura screenshot del modal y lo cierra.

    Devuelve los bytes PNG del modal o None si falla. Garantiza que el modal queda
    cerrado al salir (incluso si algo falla en el medio).
    """
    try:
        row.click(timeout=5000)
        modal = page.locator("div.modal-content").first
        modal.wait_for(state="visible", timeout=8000)
        page.wait_for_timeout(400)  # espera animación
        png = modal.screenshot(timeout=6000)
    except Exception as e:
        log.warning(f"No pude capturar screenshot del modal: {type(e).__name__}: {e}")
        png = None
    finally:
        # Cierra el modal por todos los caminos posibles, en orden de preferencia.
        try:
            close_btn = page.locator("button.close-movements").first
            if close_btn.is_visible(timeout=1000):
                close_btn.click(timeout=2000)
                page.wait_for_timeout(300)
            else:
                page.keyboard.press("Escape")
                page.wait_for_timeout(300)
        except Exception:
            try:
                page.keyboard.press("Escape")
            except Exception:
                pass
    return png


def _go_to_next_page(page: Page) -> bool:
    """Click en el botón 'avanzar' de la paginación si está habilitado. Retorna True si avanzó."""
    try:
        next_btn = page.locator('button.btn-pagination:has(img[alt="boton avanzar"])').first
        if not next_btn.is_visible(timeout=1500):
            return False
        if next_btn.is_disabled():
            return False
        next_btn.click(timeout=3000)
        page.wait_for_timeout(2000)
        return True
    except Exception as e:
        log.info(f"Sin siguiente página o falló avanzar: {type(e).__name__}: {e}")
        return False


def _read_current_page(page: Page, capture_screenshots: bool = True) -> list[dict]:
    """Lee la página actual de la tabla de movimientos confirmados.

    Por cada fila, parsea las celdas y opcionalmente captura un screenshot del
    modal de detalle. Devuelve lista de movimientos (vacía si no hay).
    """
    table, headers = _select_confirmed_table(page)
    if table is None:
        return []

    rows = table.locator("tbody tr")
    n_rows = rows.count()
    log.info(f"Página actual: {len(headers)} columnas, {n_rows} filas")

    results: list[dict] = []
    for i in range(n_rows):
        row = rows.nth(i)
        if row.locator("td").count() == 0:
            continue
        cells = [td.inner_text().strip() for td in row.locator("td").all()]
        mov = _parse_row(cells)
        if not mov:
            continue
        if capture_screenshots:
            mov["screenshot_bytes"] = _capture_modal_screenshot(page, row)
        results.append(mov)
    return results


def fetch_movements(page: Page) -> list[dict]:
    log.info("Esperando dashboard Falabella…")
    page.wait_for_timeout(5000)

    url_before = page.url
    _dismiss_popups(page)
    if not DASHBOARD_PATTERN.search(page.url):
        raise ScraperBroken(
            f"Cerrar popups nos sacó del dashboard. URL antes: {url_before} → después: {page.url}. "
            f"Probable que un selector de SEL_POPUP_CLOSE haya clickeado un botón legítimo (ej. 'Cerrar sesión')."
        )

    log.info(f"Buscando botón Estado de cuenta. URL actual: {page.url}")
    # Diagnóstico: listar botones visibles en el DOM para depuración
    btns = page.evaluate("""
        () => Array.from(document.querySelectorAll('button, a[role="button"]'))
              .filter(b => b.offsetParent !== null)
              .map(b => b.textContent.trim().slice(0, 40))
              .filter(t => t.length > 0)
              .slice(0, 15)
    """)
    log.info(f"Botones visibles en la página: {btns}")

    if not click_first(page, SEL_BTN_ESTADO, timeout_ms=15000):
        raise ScraperBroken("No encontré el botón Estado de cuenta en Falabella.")

    log.info("Esperando carga del SPA con tabs…")
    page.wait_for_timeout(8000)

    if not click_first(page, SEL_TAB_MOVS, timeout_ms=15000):
        raise ScraperBroken("No encontré la pestaña Últimos Movimientos en Falabella.")

    # Esperar filas con locators (atraviesan shadow DOM, a diferencia de evaluate)
    try:
        page.locator("app-movements-table table tbody tr").nth(1).wait_for(state="visible", timeout=20000)
    except PWTimeout:
        pass

    movements: list[dict] = []
    page_num = 1
    max_pages = 50  # safety guard contra loop infinito si la paginación se rompe
    while page_num <= max_pages:
        log.info(f"Procesando página {page_num} de movimientos…")
        rows = _read_current_page(page, capture_screenshots=True)
        if not rows:
            log.info(f"Página {page_num} vacía. Cortando.")
            break
        log.info(f"Página {page_num}: {len(rows)} movimientos parseados")
        movements.extend(rows)
        if not _go_to_next_page(page):
            log.info(f"Llegué a la última página ({page_num}). Total: {len(movements)} movimientos.")
            break
        page_num += 1

    log.info(f"Falabella: {len(movements)} movimientos totales.")
    return movements


def _parse_row(cells: list[str]) -> dict | None:
    """Parsea una fila del Estado de Cuenta de Falabella.

    Columnas reales: Fecha (DD/MM/YYYY), Descripción, Persona, Monto,
    Cuotas, Cuota a pagar, Cambio cuotas, Vacío.

    Devuelve `persona` como campo separado en el dict (Titular / Adicional / nombre)
    en vez de inyectarlo en la descripción. La descripción mantiene solo la cuota.
    """
    if len(cells) < 4:
        return None
    fecha = (cells[0] or "").strip()
    descripcion = (cells[1] or "").strip()
    persona = (cells[2] or "").strip() if len(cells) > 2 else ""
    monto_raw = (cells[3] or "").strip()
    cuotas = (cells[4] or "").strip() if len(cells) > 4 else ""

    if not fecha or not descripcion:
        return None

    monto = parse_clp_amount(monto_raw) or 0.0
    desc = descripcion
    if cuotas and cuotas != "/":
        desc = f"{desc} ({cuotas})"

    return {
        "date": parse_chilean_date(fecha),
        "description": desc,
        "amount": -abs(monto),
        "movement_type": "cargo",
        "account": "falabella",
        "persona": persona or None,
    }
