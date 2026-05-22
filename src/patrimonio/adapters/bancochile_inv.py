"""Adapter Banco de Chile — sección Inversiones.

Independiente del scraper de movimientos (`adapters/bancochile.py`):
mantiene su propio storage_state en `state_bch_inv.json.enc` y credencial
en Keychain bajo el slug `bch_inv` (Diego usa el mismo RUT+clave que en
la entrada de movimientos, pero la duplica para que este módulo no dependa
del sistema Fernet/Firestore del bot principal).

URLs a confirmar tras el primer login (suelen ser SPA con hash routes,
ej. `index.html#/inversiones/fondos-mutuos`). Si APP_URL da 404 o redirige
fuera, ajustar y reintentar.
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

log = get_logger("patrimonio.bch_inv")

LOGIN_URL = "https://login.portales.bancochile.cl/login"
# URL del resumen de inversiones — Diego confirma tras primer login si es otra
APP_URL = (
    "https://portalpersonas.bancochile.cl/mibancochile-web/front/persona/index.html"
    "#/inversiones/resumen-inversiones"
)

SEL_TOTAL = [
    'div:has-text("Total inversiones") + div',
    'span:has-text("Total inversiones") + span',
    '[data-test="total-inversiones"]',
    'h2:has-text("$")',
]

SEL_LOGIN_PRESENT = [
    'input#ppriv_per-login-click-input-rut',
    'input[name="userRut"]',
    'input[name="userPassword"]',
]


class BancoChileInvAdapter(PatrimonioAdapter):
    SITE = "bch_inv"
    INVERSION_ID = "INV-BCH-INV"
    NOMBRE_DISPLAY = "Banco de Chile · Inversiones"
    CLASE = "Renta variable"  # mix — ajustar manual si Diego quiere
    SUBCLASE = "Fondos mutuos / DAP"
    INSTITUCION = "Banco de Chile"
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
            # No autocompleto el RUT porque BCh tiene listeners Angular que
            # rechazan fills programáticos (ver bancochile.py:183-206 sobre
            # `_type_human`). El usuario lo tipea manualmente — es 1 vez.
            print(f"\n→ Ventana abierta en {LOGIN_URL}")
            print(f"  Tu RUT registrado en Keychain: {user}")
            print("  Tipea RUT+clave en la ventana, resuelve OTP si pide, navega a")
            print(f"  «Inversiones → Resumen» y presiona ENTER acá.\n")
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
            raise SessionExpired(f"Sin sesión persistida para {self.SITE}. Corre `login bch_inv`.")
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
                    page.goto(APP_URL, wait_until="networkidle", timeout=40_000)
                except PWTimeout:
                    page.goto(APP_URL, wait_until="load", timeout=40_000)

                # BCh tiene popups de bienvenida que tapan la UI — esperar un
                # poco y reintentar si vemos el form de login (significa que la
                # sesión expiró o el storage_state no aplicó).
                page.wait_for_timeout(2500)

                for sel in SEL_LOGIN_PRESENT:
                    if page.locator(sel).count() > 0:
                        raise SessionExpired(f"{self.SITE}: redirigió al login. Corre `login bch_inv`.")

                value_clp = _extract_total(page)
                if value_clp is None:
                    debug_path = _debug_screenshot(page, self.SITE)
                    raise ScrapeBroken(
                        f"{self.SITE}: no encontré el total de inversiones. Screenshot: {debug_path}"
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
                log.info("BCh Inv total por selector: %s → %s", sel, val)
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
        log.info("BCh Inv total heurístico: %s", best)
        return best
    return None


def _debug_screenshot(page, site: str) -> Path:
    out = Path(tempfile.gettempdir()) / f"patrimonio-{site}-{int(datetime.now().timestamp())}.png"
    try:
        page.screenshot(path=str(out), full_page=True)
    except Exception:
        pass
    return out
