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


def login(page: Page, rut: str, password: str, otp_provider: Callable[[str], str] | None = None) -> None:
    if len(password) > 8:
        raise LoginFailed("La clave de Banco de Chile no puede tener más de 8 caracteres.")

    log.info("Navegando al login de Banco de Chile…")
    page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(2000)

    if DASHBOARD_PATTERN.search(page.url):
        log.info("Sesión persistida activa. Saltando login.")
        return

    if any_present(page, SEL_CAPTCHA_FILLED, timeout_ms=2000):
        raise CaptchaPresent("Banco de Chile está mostrando un captcha. Login automático abortado.")

    if not fill_first(page, SEL_RUT, rut):
        raise ScraperBroken("No encontré el campo RUT en Banco de Chile.")
    if not fill_first(page, SEL_PASS, password):
        raise ScraperBroken("No encontré el campo de clave en Banco de Chile.")

    if any_present(page, SEL_CAPTCHA_FILLED, timeout_ms=1500):
        raise CaptchaPresent("Banco de Chile mostró captcha tras llenar el form.")

    if not click_first(page, SEL_SUBMIT):
        raise ScraperBroken("No pude clickear el botón Ingresar de Banco de Chile.")

    try:
        page.wait_for_url(DASHBOARD_PATTERN, timeout=20000)
        log.info("Login Banco de Chile OK (sin 2FA).")
        return
    except PWTimeout:
        pass

    otp_input = first_visible(page, SEL_OTP_INPUT, timeout_ms=3000)
    if otp_input:
        if otp_provider is None:
            raise TwoFARequired("Banco de Chile pide 2FA y no hay otp_provider configurado.")
        code = otp_provider("bancochile")
        otp_input.fill(code)
        if not click_first(page, SEL_OTP_SUBMIT):
            raise ScraperBroken("No encontré botón para confirmar el OTP en Banco de Chile.")
        try:
            page.wait_for_url(DASHBOARD_PATTERN, timeout=20000)
            log.info("Login Banco de Chile OK con 2FA.")
            return
        except PWTimeout:
            raise LoginFailed("Banco de Chile no llegó al dashboard tras OTP.")

    if DASHBOARD_PATTERN.search(page.url):
        log.info("Login Banco de Chile OK (URL ya coincide con dashboard).")
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


def fetch_movements(page: Page) -> list[dict]:
    log.info("Navegando directo a saldos-movimientos de Banco de Chile…")
    page.goto(MOVEMENTS_URL, wait_until="domcontentloaded", timeout=30000)

    # La SPA carga async. Esperamos a que aparezca al menos una fila real
    # (no una table-collapse-row, esas son filas-fantasma de animación).
    try:
        page.wait_for_selector(SEL_TABLE_ROW, state="attached", timeout=40000)
        page.wait_for_timeout(2000)
    except PWTimeout:
        raise ScraperBroken("No encontré tabla de movimientos en Banco de Chile.")

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
