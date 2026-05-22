"""Adapter Mercado Pago Chile.

Extrae el saldo total en MP (saldo en cuenta + saldo en Cuenta con
Rendimiento si está visible en el home). Si MP esconde el saldo detrás
de un botón "Mostrar saldo", el adapter intenta hacer click primero.

MP es el sitio más propenso a re-verificación SMS — espera tener que correr
`login mercadopago` más seguido que para los otros adapters.
"""
from __future__ import annotations

import json
import re
import tempfile
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

from ...utils import get_logger, parse_clp_amount
from ..keychain import get_credential
from .base import Holding, PatrimonioAdapter, SessionExpired, ScrapeBroken

log = get_logger("patrimonio.mercadopago")

LOGIN_URL = "https://www.mercadopago.cl/login"
APP_URL = "https://www.mercadopago.cl/home"

SEL_USER = [
    'input[name="user_id"]',
    'input[type="email"]',
    'input[autocomplete="username"]',
    'input[name="email"]',
]

# Cuando el saldo está oculto detrás de "Mostrar saldo"
SEL_TOGGLE_BALANCE = [
    'button:has-text("Mostrar saldo")',
    'button[aria-label*="mostrar saldo" i]',
    'button[aria-label*="ver saldo" i]',
]

SEL_BALANCE = [
    '[data-testid="balance-current-amount"]',
    '[data-testid="amount-balance"]',
    '[data-test="balance"]',
    'span:has-text("$") >> nth=0',
]

SEL_LOGIN_PRESENT = [
    'input[type="password"]',
    'form[action*="login" i]',
    'input[name="password"]',
]


class MercadoPagoAdapter(PatrimonioAdapter):
    SITE = "mercadopago"
    INVERSION_ID = "INV-MERCADOPAGO"
    NOMBRE_DISPLAY = "Mercado Pago"
    CLASE = "Cash"
    SUBCLASE = "Cuenta + rendimiento"
    INSTITUCION = "Mercado Pago"
    MONEDA = "CLP"
    LIQUIDEZ = "Alta"
    PAIS = "Chile"

    def login(self, headed: bool = True) -> None:
        user, _password = get_credential(self.SITE)
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=not headed)
            ctx = browser.new_context(
                viewport={"width": 1280, "height": 900},
                locale="es-CL",
            )
            page = ctx.new_page()
            page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30_000)
            for sel in SEL_USER:
                try:
                    page.locator(sel).first.fill(user, timeout=2000)
                    log.info("MP: usuario autocompletado (%s)", sel)
                    break
                except Exception:
                    continue
            print(f"\n→ Ventana abierta en {LOGIN_URL}")
            print(f"  Usuario ya autocompletado: {user}")
            print("  Completa clave, verificación SMS/email, y presiona ENTER acá cuando estés en el home.\n")
            try:
                input()
            except KeyboardInterrupt:
                print("Cancelado.")
                browser.close()
                return
            state_json = ctx.storage_state()
            self.save_state(json.dumps(state_json).encode("utf-8"))
            browser.close()

    def fetch_holdings(self) -> Holding:
        state_bytes = self.load_state()
        if state_bytes is None:
            raise SessionExpired(f"Sin sesión persistida para {self.SITE}. Corre `login mercadopago`.")
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                state_dict = json.loads(state_bytes.decode("utf-8"))
                ctx = browser.new_context(
                    storage_state=state_dict,
                    viewport={"width": 1280, "height": 900},
                    locale="es-CL",
                )
                page = ctx.new_page()
                try:
                    page.goto(APP_URL, wait_until="networkidle", timeout=30_000)
                except PWTimeout:
                    page.goto(APP_URL, wait_until="load", timeout=30_000)

                for sel in SEL_LOGIN_PRESENT:
                    if page.locator(sel).count() > 0:
                        raise SessionExpired(f"{self.SITE}: redirigió al login. Corre `login mercadopago`.")

                # Intenta destapar el saldo si está oculto
                for sel in SEL_TOGGLE_BALANCE:
                    try:
                        loc = page.locator(sel).first
                        if loc.is_visible(timeout=1500):
                            loc.click(timeout=2000)
                            page.wait_for_timeout(500)
                            log.info("MP: click en %s para mostrar saldo", sel)
                            break
                    except Exception:
                        continue

                value_clp = _extract_balance(page)
                if value_clp is None:
                    debug_path = _debug_screenshot(page, self.SITE)
                    raise ScrapeBroken(
                        f"{self.SITE}: no encontré el saldo. Screenshot: {debug_path}"
                    )

                return Holding(
                    site=self.SITE,
                    inversion_id=self.INVERSION_ID,
                    nombre=self.NOMBRE_DISPLAY,
                    clase=self.CLASE,
                    subclase=self.SUBCLASE,
                    institucion=self.INSTITUCION,
                    moneda=self.MONEDA,
                    valor_moneda_orig=value_clp,
                    tipo_cambio=1.0,
                    valor_clp=value_clp,
                    fecha=datetime.now(),
                    estado="ok",
                )
            finally:
                browser.close()


def _extract_balance(page) -> float | None:
    for sel in SEL_BALANCE:
        try:
            loc = page.locator(sel).first
            if loc.count() == 0:
                continue
            txt = loc.inner_text(timeout=2000)
            val = parse_clp_amount(txt)
            if val is not None and val >= 0:
                log.info("MP saldo por selector: %s → %s", sel, val)
                return val
        except Exception:
            continue
    try:
        text = page.evaluate("() => document.body.innerText || ''")
    except Exception:
        text = ""
    candidates: list[float] = []
    for m in re.finditer(r"\$\s*[\d\.\,]+", text):
        val = parse_clp_amount(m.group(0))
        if val is not None and val > 0:
            candidates.append(val)
    if candidates:
        best = max(candidates)
        log.info("MP saldo heurístico: %s", best)
        return best
    return None


def _debug_screenshot(page, site: str) -> Path:
    out = Path(tempfile.gettempdir()) / f"patrimonio-{site}-{int(datetime.now().timestamp())}.png"
    try:
        page.screenshot(path=str(out), full_page=True)
    except Exception:
        pass
    return out
