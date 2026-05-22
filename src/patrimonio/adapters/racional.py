"""Adapter Racional — saldo de inversiones (renta fija).

URLs aproximadas, se ajustan tras el primer login si Diego confirma otras.
Si APP_URL da 404, pedir URL exacta del dashboard a Diego (igual que pasó
con Fintual: el `/f/` vs `/app/goals`).
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

log = get_logger("patrimonio.racional")

LOGIN_URL = "https://app.racional.cl/login"
APP_URL = "https://app.racional.cl/"

SEL_USER = [
    'input[name="rut"]',
    'input[name="username"]',
    'input[autocomplete="username"]',
    'input[type="email"]',
]

SEL_TOTAL = [
    '[data-test="total-invested"]',
    '[data-testid="total-invested"]',
    '[data-test="portfolio-total"]',
    'h1:has-text("$")',
    'h2:has-text("$")',
]

SEL_LOGIN_PRESENT = [
    'input[type="password"]',
    'input[name="password"]',
    'form[action*="login" i]',
]


class RacionalAdapter(PatrimonioAdapter):
    SITE = "racional"
    INVERSION_ID = "INV-RACIONAL"
    NOMBRE_DISPLAY = "Racional"
    CLASE = "Renta fija"
    SUBCLASE = "Depósitos / Fondos"
    INSTITUCION = "Racional"
    MONEDA = "CLP"
    LIQUIDEZ = "Media"
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
                    log.info("Racional: usuario autocompletado (%s)", sel)
                    break
                except Exception:
                    continue
            print(f"\n→ Ventana abierta en {LOGIN_URL}")
            print(f"  Usuario ya autocompletado: {user}")
            print("  Completa clave + OTP si pide, llega al dashboard, y presiona ENTER acá.\n")
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
            raise SessionExpired(f"Sin sesión persistida para {self.SITE}. Corre `login racional`.")
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
                        raise SessionExpired(f"{self.SITE}: redirigió al login. Corre `login racional`.")

                value_clp = _extract_total(page)
                if value_clp is None:
                    debug_path = _debug_screenshot(page, self.SITE)
                    raise ScrapeBroken(
                        f"{self.SITE}: no encontré el total invertido. Screenshot: {debug_path}"
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


def _extract_total(page) -> float | None:
    for sel in SEL_TOTAL:
        try:
            loc = page.locator(sel).first
            if loc.count() == 0:
                continue
            txt = loc.inner_text(timeout=2000)
            val = parse_clp_amount(txt)
            if val is not None and val > 0:
                log.info("Racional total por selector: %s → %s", sel, val)
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
        if val is not None and val > 100_000:
            candidates.append(val)
    if candidates:
        best = max(candidates)
        log.info("Racional total heurístico: %s", best)
        return best
    return None


def _debug_screenshot(page, site: str) -> Path:
    out = Path(tempfile.gettempdir()) / f"patrimonio-{site}-{int(datetime.now().timestamp())}.png"
    try:
        page.screenshot(path=str(out), full_page=True)
    except Exception:
        pass
    return out
