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
    read_table_rows,
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

SEL_TABLE = [
    "table",
    '[class*="movimiento"] table',
    '[class*="transaction"]',
    '[class*="listado"] table',
    '[class*="saldo"] table',
]

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


def fetch_movements(page: Page) -> list[dict]:
    log.info("Navegando directo a saldos-movimientos de Banco de Chile…")
    page.goto(MOVEMENTS_URL, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(4000)

    # TODO v2: si la tabla DOM rompe en producción, migrar a descarga de
    # cartola con `a:has-text("Descargar cartola")` + parseo del archivo.
    table = read_table_rows(page, SEL_TABLE)
    if not table:
        raise ScraperBroken("No encontré tabla de movimientos en Banco de Chile.")

    log.info(f"Tabla BCh: {len(table['headers'])} columnas, {len(table['rows'])} filas.")

    movements: list[dict] = []
    for cells in table["rows"]:
        mov = _parse_row(cells, table["headers"])
        if mov:
            movements.append(mov)

    log.info(f"Banco de Chile: {len(movements)} movimientos parseados.")
    return movements


def _parse_row(cells: list[str], headers: list[str]) -> dict | None:
    """Parser genérico basado en headers (fecha, descripción, monto/cargo/abono, saldo)."""
    if len(cells) < 2:
        return None

    date_iso: str | None = None
    description: str | None = None
    amount: float | None = None
    movement_type: str | None = None

    for i, cell in enumerate(cells):
        cell = (cell or "").strip()
        header = (headers[i] if i < len(headers) else "").lower()
        if "fecha" in header or re.match(r"^\d{2}[/\-]\d{2}[/\-]\d{2,4}$", cell):
            date_iso = parse_chilean_date(cell)
        elif "descripci" in header or "detalle" in header or "glosa" in header:
            description = cell
        elif "cargo" in header:
            value = parse_clp_amount(cell)
            if value is not None:
                amount = -abs(value)
                movement_type = "cargo"
        elif "abono" in header:
            value = parse_clp_amount(cell)
            if value is not None:
                amount = abs(value)
                movement_type = "abono"
        elif "monto" in header:
            value = parse_clp_amount(cell)
            if value is not None and amount is None:
                amount = value
                movement_type = "abono" if value >= 0 else "cargo"

    if not description:
        for cell in cells[1:]:
            cell = (cell or "").strip()
            if (
                len(cell) > 3
                and not re.fullmatch(r"\$?[\d.,\-]+", cell)
                and not re.match(r"^\d{2}[/\-]\d{2}", cell)
            ):
                description = cell
                break

    if not description or amount is None or not date_iso:
        return None

    return {
        "date": date_iso,
        "description": description,
        "amount": amount,
        "movement_type": movement_type,
        "account": "bancochile",
    }
