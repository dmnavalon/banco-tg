"""Adapter NAUTA (app.nauta.pro) — SSO con Google.

Diferencias vs adapters con login email+clave:
- NO se autocompleta nada en NAUTA. El login es "Continuar con Google",
  que redirige a accounts.google.com.
- Diego completa el flujo manual: click "Continuar con Google" → elige
  cuenta → resuelve 2FA → vuelve a NAUTA logueado → ENTER en terminal.
- Chromium se lanza con flags + init_script anti-detección porque Google
  bloquea Playwright vanilla con "Couldn't sign you in - This browser may
  not be secure".
- En `fetch_holdings`, si la URL contiene `accounts.google.com` significa
  que el token de Google expiró → SessionExpired.
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
from .base import (
    Holding,
    PatrimonioAdapter,
    ScrapeBroken,
    SessionExpired,
    STEALTH_INIT_SCRIPT,
    STEALTH_LAUNCH_ARGS,
)

log = get_logger("patrimonio.nauta")

LOGIN_URL = "https://app.nauta.pro/login"
APP_URL = "https://app.nauta.pro/"

SEL_TOTAL = [
    '[data-test="portfolio-total"]',
    '[data-testid="portfolio-total"]',
    '[data-test="total"]',
    'h1:has-text("$")',
    'h2:has-text("$")',
]


class NautaAdapter(PatrimonioAdapter):
    SITE = "nauta"
    INVERSION_ID = "INV-NAUTA"
    NOMBRE_DISPLAY = "Nauta"
    CLASE = "Renta variable"
    SUBCLASE = "Portafolio"
    INSTITUCION = "Nauta"
    MONEDA = "CLP"
    LIQUIDEZ = "Media"
    PAIS = "Chile"

    def login(self, headed: bool = True) -> None:
        # En SSO Google el "user" del Keychain es el email de la cuenta
        # Google que usa para entrar — no se autocompleta en NAUTA porque
        # NAUTA no tiene form de email; Google lo va a pedir adentro.
        user, _password = get_credential(self.SITE)
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=not headed,
                args=STEALTH_LAUNCH_ARGS,
            )
            ctx = browser.new_context(
                viewport={"width": 1280, "height": 900},
                locale="es-CL",
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/130.0.0.0 Safari/537.36"
                ),
            )
            ctx.add_init_script(STEALTH_INIT_SCRIPT)
            page = ctx.new_page()
            page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30_000)
            print(f"\n→ Ventana abierta en {LOGIN_URL}")
            print(f"  Cuenta Google registrada: {user}")
            print("  Pasos en la ventana:")
            print("    1. Click en «Continuar con Google» (o el botón equivalente)")
            print("    2. Elegí tu cuenta Google, completa clave + 2FA")
            print("    3. Vuelve a NAUTA logueado, llegá al home")
            print("    4. Presioná ENTER acá.\n")
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
            raise SessionExpired(f"Sin sesión persistida para {self.SITE}. Corre `login nauta`.")
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True,
                args=STEALTH_LAUNCH_ARGS,
            )
            try:
                state_dict = json.loads(state_bytes.decode("utf-8"))
                ctx = browser.new_context(
                    storage_state=state_dict,
                    viewport={"width": 1280, "height": 900},
                    locale="es-CL",
                    user_agent=(
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/130.0.0.0 Safari/537.36"
                    ),
                )
                ctx.add_init_script(STEALTH_INIT_SCRIPT)
                page = ctx.new_page()
                try:
                    page.goto(APP_URL, wait_until="networkidle", timeout=30_000)
                except PWTimeout:
                    page.goto(APP_URL, wait_until="load", timeout=30_000)

                # SSO expirado: NAUTA redirige al sign-in que clickea Google
                # y termina en accounts.google.com pidiendo re-auth.
                current = page.url.lower()
                if "accounts.google.com" in current or "/login" in current:
                    raise SessionExpired(
                        f"{self.SITE}: token Google expirado (URL: {current[:80]}). "
                        f"Corre `login nauta` para re-autenticar."
                    )

                value_clp = _extract_total(page)
                if value_clp is None:
                    debug_path = _debug_screenshot(page, self.SITE)
                    raise ScrapeBroken(
                        f"{self.SITE}: no encontré el total del portafolio. Screenshot: {debug_path}"
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
                log.info("Nauta total por selector: %s → %s", sel, val)
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
        log.info("Nauta total heurístico: %s", best)
        return best
    return None


def _debug_screenshot(page, site: str) -> Path:
    out = Path(tempfile.gettempdir()) / f"patrimonio-{site}-{int(datetime.now().timestamp())}.png"
    try:
        page.screenshot(path=str(out), full_page=True)
    except Exception:
        pass
    return out
