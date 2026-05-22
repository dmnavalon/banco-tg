"""Adapter Fintual — extrae el valor total del portafolio.

Estrategia:
- Primer login: ventana visible, autocompleta email desde Keychain, Diego
  resuelve clave y 2FA (push o SMS), presiona ENTER en la terminal cuando
  esté en el home. Guarda storage_state cifrado.
- Corridas siguientes: headless con storage_state. Si la sesión expiró (la
  URL redirige a /login o aparece el form de login), levanta SessionExpired.
- Selector primario: el número grande visible en `/app` que dice "$X.XXX.XXX".
  Hay fallback heurístico si Fintual rota el data-test.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

from ...utils import get_logger, parse_clp_amount
from ..keychain import get_credential
from .base import Holding, PatrimonioAdapter, SessionExpired, ScrapeBroken

log = get_logger("patrimonio.fintual")

LOGIN_URL = "https://fintual.cl/f/sign-in/"
APP_URL = "https://fintual.cl/app/goals"

SEL_EMAIL = [
    'input[type="email"]',
    'input[name="user[email]"]',
    'input[name="email"]',
    'input[autocomplete="email"]',
]

# Selectores donde Fintual ha mostrado el valor del portafolio. Probamos en
# orden, primer match gana. Si todos fallan, fallback heurístico abajo.
SEL_PORTFOLIO_VALUE = [
    '[data-test="portfolio-value"]',
    '[data-testid="portfolio-value"]',
    '[data-test="total-portfolio"]',
    'section[aria-label*="portafolio" i] :text-matches("\\$\\s*[\\d.,]+")',
]

# Detectores de "no estás logueado"
SEL_LOGIN_PRESENT = [
    'input[type="password"]',
    'form[action*="login" i]',
]


class FintualAdapter(PatrimonioAdapter):
    SITE = "fintual"
    INVERSION_ID = "INV-FINTUAL"
    NOMBRE_DISPLAY = "Fintual"
    CLASE = "Renta variable"
    SUBCLASE = "Fondos mutuos"
    INSTITUCION = "Fintual"
    MONEDA = "CLP"
    LIQUIDEZ = "Alta"
    PAIS = "Chile"

    def login(self, headed: bool = True) -> None:
        user, _password = get_credential(self.SITE)  # password lo escribe el usuario para evitar autocomplete mismatch
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=not headed)
            ctx = browser.new_context(
                viewport={"width": 1280, "height": 900},
                locale="es-CL",
            )
            page = ctx.new_page()
            page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30_000)
            # Autocompletar email para acelerar al usuario
            for sel in SEL_EMAIL:
                try:
                    page.locator(sel).first.fill(user, timeout=2000)
                    log.info("Fintual: email autocompletado (%s)", sel)
                    break
                except Exception:
                    continue
            print(f"\n→ Ventana abierta en {LOGIN_URL}")
            print(f"  Email ya autocompletado: {user}")
            print("  Completa tu clave y 2FA en la ventana, llega al home, y presiona ENTER acá.\n")
            try:
                input()
            except KeyboardInterrupt:
                print("Cancelado.")
                browser.close()
                return
            # Snapshot del storage_state para futuras corridas
            state_json = ctx.storage_state()
            self.save_state(json.dumps(state_json).encode("utf-8"))
            browser.close()

    def fetch_holdings(self) -> Holding:
        state_bytes = self.load_state()
        if state_bytes is None:
            raise SessionExpired(f"Sin sesión persistida para {self.SITE}. Corre `login fintual` primero.")
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                # Truco: storage_state acepta path o dict. Le pasamos un tmpfile
                # porque dict no acepta TypedDict directo en algunas versiones.
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
                    # `networkidle` puede colgar si Fintual tiene WS abiertos;
                    # intentamos con load.
                    page.goto(APP_URL, wait_until="load", timeout=30_000)

                # ¿Volvió al login? → sesión expirada
                for sel in SEL_LOGIN_PRESENT:
                    if page.locator(sel).count() > 0:
                        raise SessionExpired(
                            f"{self.SITE}: redirigió al login. Corre `login fintual`."
                        )

                value_clp = _extract_portfolio_value(page)
                if value_clp is None:
                    # Dejamos screenshot para debug
                    debug_path = _debug_screenshot(page, self.SITE)
                    raise ScrapeBroken(
                        f"{self.SITE}: no encontré el valor del portafolio. "
                        f"Screenshot: {debug_path}"
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


def _extract_portfolio_value(page) -> float | None:
    # Intento 1: selectores conocidos
    for sel in SEL_PORTFOLIO_VALUE:
        try:
            loc = page.locator(sel).first
            if loc.count() == 0:
                continue
            txt = loc.inner_text(timeout=2000)
            val = parse_clp_amount(txt)
            if val is not None and val > 0:
                log.info("Fintual valor por selector: %s → %s", sel, val)
                return val
        except Exception:
            continue

    # Intento 2: heurístico — busca el $XX.XXX.XXX más grande visible en la página
    try:
        text = page.evaluate("() => document.body.innerText || ''")
    except Exception:
        text = ""
    candidates: list[float] = []
    for m in re.finditer(r"\$\s*[\d\.\,]+", text):
        val = parse_clp_amount(m.group(0))
        if val is not None and val > 100_000:  # ignorar valores chicos tipo precios
            candidates.append(val)
    if candidates:
        best = max(candidates)
        log.info("Fintual valor heurístico (máximo $ en página): %s", best)
        return best
    return None


def _debug_screenshot(page, site: str) -> Path:
    out = Path(tempfile.gettempdir()) / f"patrimonio-{site}-{int(datetime.now().timestamp())}.png"
    try:
        page.screenshot(path=str(out), full_page=True)
    except Exception:
        pass
    return out
