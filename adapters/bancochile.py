from __future__ import annotations

import re
from typing import Callable

from playwright.sync_api import Page, TimeoutError as PWTimeout

from src.utils import get_logger, parse_chilean_date, parse_clp_amount

from .base import (
    CaptchaPresent,
    LoginFailed,
    ScraperBroken,
    TwoFARequired,
    any_present,
    click_first,
    fill_first,
    first_visible,
)

log = get_logger("adapters.bancochile")

LOGIN_URL = "https://login.portales.bancochile.cl/login"
MOVEMENTS_URL = (
    "https://portalpersonas.bancochile.cl/mibancochile-web/front/persona/index.html"
    "#/movimientos/cuenta/saldos-movimientos/"
)

SEL_RUT = [
    "#ppriv_per-login-click-input-rut",
    'input[name="userRut"]',
]
SEL_PASS = [
    "#ppriv_per-login-click-input-password",
    'input[name="userPassword"]',
]
SEL_SUBMIT = [
    "#ppriv_per-login-click-ingresar-login",
    "button.bch-login__submit",
]

SEL_CAPTCHA_FILLED = [
    ".captcha-container iframe",
    ".captcha-container canvas",
    ".captcha-container img[src]",
]

# Tabla real renderizada por Angular Material/CDK. El selector genérico "table"
# matcheaba elementos del header/sidebar antes que la tabla real, por eso
# hay que apuntar a la clase específica `bch-table`.
SEL_TABLE_ROW = "table.bch-table tbody tr.bch-row:not(.table-collapse-row)"
SEL_TABLE_CONTAINER = "table.bch-table"
SEL_NEXT_PAGE = "button.mat-paginator-navigation-next"

SEL_OTP_INPUT = [
    'input[autocomplete="one-time-code"]',
    'input[name*="otp" i]',
    'input[id*="otp" i]',
    'input[placeholder*="código" i]',
    'input[maxlength="6"][type="text"]:not([name="userRut"])',
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
    r"portalpersonas\.bancochile\.cl|mibancochile",
    re.IGNORECASE,
)


def _log_state(page: Page, label: str) -> None:
    """Helper de diagnóstico: loguea URL actual + presencia de campos clave."""
    try:
        diag = page.evaluate("""
            () => ({
                url: window.location.href,
                title: document.title,
                rut_input: !!document.querySelector('#ppriv_per-login-click-input-rut'),
                pass_input: !!document.querySelector('#ppriv_per-login-click-input-password'),
                submit_btn: !!document.querySelector('#ppriv_per-login-click-ingresar-login'),
                bch_table: !!document.querySelector('table.bch-table'),
                forms: document.querySelectorAll('form').length,
                visible_text: (document.body ? document.body.innerText : '').slice(0, 200).replace(/\\s+/g, ' '),
            })
        """)
        log.info(
            f"BCh state [{label}]: url={diag.get('url')!r} "
            f"title={diag.get('title')!r} "
            f"rut_input={diag.get('rut_input')} pass_input={diag.get('pass_input')} "
            f"submit_btn={diag.get('submit_btn')} bch_table={diag.get('bch_table')} "
            f"forms={diag.get('forms')} text={diag.get('visible_text')!r}"
        )
    except Exception as e:
        log.warning(f"BCh state [{label}]: no pude inspeccionar página ({e})")


def login(page: Page, rut: str, password: str, otp_provider: Callable[[str], str] | None = None) -> None:
    if len(password) > 8:
        raise LoginFailed("La clave de Banco de Chile no puede tener más de 8 caracteres.")

    log.info("Navegando al login de Banco de Chile…")
    page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(2000)
    _log_state(page, "post-goto-login")

    if DASHBOARD_PATTERN.search(page.url):
        # La cookie del portal está vigente, pero BCh tiene cookies separadas
        # para distintos paths. Verificamos navegando a MOVEMENTS_URL.
        log.info("Sesión persistida del portal activa. Verificando acceso a movimientos…")
        page.goto(MOVEMENTS_URL, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2500)
        _log_state(page, "post-goto-movs-from-persisted")
        if DASHBOARD_PATTERN.search(page.url):
            log.info("Sesión persistida válida para movimientos. Saltando login.")
            return
        log.warning(
            f"Sesión persistida expiró al navegar a movimientos (URL: {page.url}). "
            f"Limpiando cookies y re-logueando con credenciales."
        )
        # La doble validación detectó sesión inválida. Limpiar TODO el storage
        # antes del re-login para evitar que el form arrastre estado viejo
        # (inputs auto-rellenados, error messages residuales, etc.).
        try:
            page.context.clear_cookies()
            log.info("Cookies del contexto limpiadas para forzar login fresh.")
        except Exception as e:
            log.warning(f"No pude limpiar cookies: {e}")
        page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2500)
        _log_state(page, "post-goto-login-fresh")

    if any_present(page, SEL_CAPTCHA_FILLED, timeout_ms=2000):
        raise CaptchaPresent("Banco de Chile está mostrando un captcha. Login automático abortado.")

    log.info(f"Llenando RUT… (selectores: {SEL_RUT})")
    if not fill_first(page, SEL_RUT, rut):
        _log_state(page, "fail-rut")
        raise ScraperBroken("No encontré el campo RUT en Banco de Chile.")
    log.info("RUT llenado OK.")

    log.info(f"Llenando clave… (selectores: {SEL_PASS})")
    if not fill_first(page, SEL_PASS, password):
        _log_state(page, "fail-pass")
        raise ScraperBroken("No encontré el campo de clave en Banco de Chile.")
    log.info("Clave llenada OK.")

    if any_present(page, SEL_CAPTCHA_FILLED, timeout_ms=1500):
        raise CaptchaPresent("Banco de Chile mostró captcha tras llenar el form.")

    log.info(f"Click submit… (selectores: {SEL_SUBMIT})")
    if not click_first(page, SEL_SUBMIT):
        _log_state(page, "fail-submit")
        raise ScraperBroken("No pude clickear el botón Ingresar de Banco de Chile.")
    log.info("Submit clickeado, esperando dashboard…")

    try:
        page.wait_for_url(DASHBOARD_PATTERN, timeout=20000)
        log.info(f"Login Banco de Chile OK (sin 2FA). URL: {page.url}")
        return
    except PWTimeout:
        log.warning(f"No llegó al dashboard en 20s tras submit. URL: {page.url}")
        _log_state(page, "post-submit-no-dashboard")

    otp_input = first_visible(page, SEL_OTP_INPUT, timeout_ms=3000)
    if otp_input:
        log.info("OTP input detectado, pidiendo 2FA al usuario.")
        if otp_provider is None:
            raise TwoFARequired("Banco de Chile pide 2FA y no hay otp_provider configurado.")
        code = otp_provider("bancochile")
        otp_input.fill(code)
        if not click_first(page, SEL_OTP_SUBMIT):
            raise ScraperBroken("No encontré botón para confirmar el OTP en Banco de Chile.")
        try:
            page.wait_for_url(DASHBOARD_PATTERN, timeout=20000)
            log.info(f"Login Banco de Chile OK con 2FA. URL: {page.url}")
            return
        except PWTimeout:
            raise LoginFailed(f"Banco de Chile no llegó al dashboard tras OTP. URL: {page.url}")

    if DASHBOARD_PATTERN.search(page.url):
        log.info(f"Login Banco de Chile OK (URL ya coincide con dashboard). URL: {page.url}")
        return

    raise LoginFailed(f"Banco de Chile no avanzó al dashboard tras login. URL actual: {page.url}")


def _read_current_page(page: Page) -> list[dict]:
    """Lee la página visible actual de la tabla bch-table.

    BCh usa Angular Material CDK con clases tipo `cdk-column-fechaContable`,
    `cdk-column-descripcion`, `cdk-column-cargo`, `cdk-column-abono`. Apuntamos
    directo a esas clases en vez de heurísticas por header text.

    Saltamos las filas con clase `table-collapse-row` que son filas vacías de
    animación de detalle expandible.
    """
    rows_loc = page.locator(SEL_TABLE_ROW)
    n = rows_loc.count()
    log.info(f"BCh: {n} filas detectadas en página actual")
    results: list[dict] = []
    for i in range(n):
        row = rows_loc.nth(i)
        try:
            fecha = row.locator("td.cdk-column-fechaContable").first.inner_text().strip()
            descripcion = row.locator("td.cdk-column-descripcion").first.inner_text().strip()
            cargo = row.locator("td.cdk-column-cargo").first.inner_text().strip()
            abono = row.locator("td.cdk-column-abono").first.inner_text().strip()
        except Exception as e:
            log.warning(f"BCh fila {i}: error leyendo celdas: {e}")
            continue
        mov = _parse_row(fecha, descripcion, cargo, abono)
        if mov:
            results.append(mov)
    return results


def _parse_row(fecha: str, descripcion: str, cargo: str, abono: str) -> dict | None:
    """Parsea una fila de la cuenta corriente de BCh.

    BCh muestra cargo y abono en columnas separadas: una de las dos viene vacía.
    Si la celda de cargo trae monto → es un egreso (negativo). Si la de abono
    trae monto → es un ingreso (positivo).
    """
    if not fecha or not descripcion:
        return None

    cargo_val = parse_clp_amount(cargo) if cargo.strip() else None
    abono_val = parse_clp_amount(abono) if abono.strip() else None

    if cargo_val is not None and cargo_val > 0:
        amount = -abs(cargo_val)
        movement_type = "cargo"
    elif abono_val is not None and abono_val > 0:
        amount = abs(abono_val)
        movement_type = "abono"
    else:
        # Ninguna columna trajo monto válido — fila no parseable.
        return None

    return {
        "date": parse_chilean_date(fecha),
        "description": descripcion,
        "amount": amount,
        "movement_type": movement_type,
        "account": "bancochile",
    }


def _go_to_next_page(page: Page) -> bool:
    """Click en el botón 'siguiente página' del paginador Material si está habilitado.
    Retorna True si avanzó; False si era la última página."""
    try:
        next_btn = page.locator(SEL_NEXT_PAGE).first
        if not next_btn.is_visible(timeout=1500):
            return False
        if next_btn.is_disabled():
            return False
        next_btn.click(timeout=3000)
        page.wait_for_timeout(2000)  # esperar render de la nueva página
        return True
    except Exception as e:
        log.info(f"BCh: sin siguiente página o falló avanzar: {type(e).__name__}: {e}")
        return False


def _send_diagnostic_screenshot(page: Page, label: str) -> None:
    """Captura screenshot de la pantalla actual y lo manda por Telegram al chat
    autorizado. Útil para debug visual cuando un selector no aparece y los
    logs de texto no son suficientes."""
    try:
        png = page.screenshot(full_page=True, timeout=10000)
    except Exception as e:
        log.warning(f"No pude tomar screenshot de diagnóstico: {e}")
        return
    try:
        import os
        import requests
        token = os.environ.get("TG_BOT_TOKEN", "").strip()
        chat_id = os.environ.get("TG_CHAT_ID", "").strip()
        if not token or not chat_id:
            log.warning("Sin TG_BOT_TOKEN/TG_CHAT_ID; salto envío de screenshot diag.")
            return
        files = {"photo": (f"bch-diag-{label}.png", png, "image/png")}
        data = {
            "chat_id": chat_id,
            "caption": f"🔍 BCh diag [{label}] · URL: {page.url[:200]}",
        }
        r = requests.post(
            f"https://api.telegram.org/bot{token}/sendPhoto",
            data=data, files=files, timeout=30,
        )
        if r.status_code == 200 and r.json().get("ok"):
            log.info(f"Screenshot diag [{label}] enviado a Telegram.")
        else:
            log.warning(f"sendPhoto diag falló: {r.status_code} {r.text[:200]}")
    except Exception as e:
        log.warning(f"Error enviando screenshot diag: {e}")


def fetch_movements(page: Page) -> list[dict]:
    log.info("Navegando directo a saldos-movimientos de Banco de Chile…")
    page.goto(MOVEMENTS_URL, wait_until="domcontentloaded", timeout=30000)

    page.wait_for_timeout(3000)
    log.info(f"BCh: URL post-goto = {page.url}")
    _log_state(page, "post-goto-movements")

    # Si después del goto a movs nos rebotaron a Auth0, intentar un click en
    # cualquier link "Cuentas" / "Saldos y movimientos" del menú lateral
    # antes de rendirse — algunos flujos requieren navegación click-driven.
    if "login.portales.bancochile.cl" in page.url or "/authorize" in page.url:
        log.warning(f"BCh: post-goto-movements URL en Auth0 ({page.url}). Sesión efectivamente expiró aquí.")
        _send_diagnostic_screenshot(page, "rebote-auth0")
        raise ScraperBroken(
            f"BCh nos rebotó a Auth0 al ir a saldos-movimientos (URL: {page.url}). "
            f"La sesión del segundo nivel sigue inválida incluso tras login fresh — "
            f"probable token JWT que requiere step-up auth o navegación click-driven desde el menú."
        )

    # La SPA carga async. Esperamos a que aparezca al menos una fila real.
    try:
        page.wait_for_selector(SEL_TABLE_ROW, state="attached", timeout=40000)
        page.wait_for_timeout(2000)
    except PWTimeout:
        try:
            diag = page.evaluate("""
                () => {
                    const tables = Array.from(document.querySelectorAll('table')).map(t => ({
                        class: t.className,
                        rows: t.querySelectorAll('tbody tr').length,
                        headers: Array.from(t.querySelectorAll('thead th')).map(h => h.textContent.trim()).slice(0, 6),
                    }));
                    const headings = Array.from(document.querySelectorAll('h1, h2, h3'))
                        .map(h => h.textContent.trim()).filter(t => t).slice(0, 6);
                    const buttons = Array.from(document.querySelectorAll('button, a[role="button"]'))
                        .filter(b => b.offsetParent !== null)
                        .map(b => b.textContent.trim().slice(0, 40))
                        .filter(t => t.length > 0).slice(0, 12);
                    return { url: window.location.href, headings, tables, buttons };
                }
            """)
            log.warning(f"BCh diag URL: {diag.get('url')!r}")
            log.warning(f"BCh diag headings: {diag.get('headings')}")
            log.warning(f"BCh diag tables: {diag.get('tables')}")
            log.warning(f"BCh diag botones visibles: {diag.get('buttons')}")
        except Exception as diag_err:
            log.warning(f"BCh: no pude obtener diagnóstico de la página: {diag_err}")
        # Mandar screenshot al usuario para diagnóstico visual.
        _send_diagnostic_screenshot(page, "no-tabla")
        raise ScraperBroken(
            f"No encontré tabla de movimientos en Banco de Chile (URL final: {page.url})."
        )

    movements: list[dict] = []
    page_num = 1
    max_pages = 50  # safety guard contra loop infinito si el paginador se rompe
    while page_num <= max_pages:
        log.info(f"Procesando página {page_num} de movimientos BCh…")
        rows = _read_current_page(page)
        if not rows:
            log.info(f"Página {page_num} sin filas. Cortando.")
            break
        log.info(f"Página {page_num}: {len(rows)} movimientos parseados")
        movements.extend(rows)
        if not _go_to_next_page(page):
            log.info(f"Última página de BCh ({page_num}). Total: {len(movements)} movimientos.")
            break
        page_num += 1

    log.info(f"Banco de Chile: {len(movements)} movimientos totales.")
    return movements
