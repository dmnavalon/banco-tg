"""Interfaz común para adapters de patrimonio.

Distinto de `adapters/base.py` (el de movimientos bancarios) — aquí lo que
queremos es una sola consulta de SALDO TOTAL del portafolio, no una lista
de movimientos. Por eso la interfaz es más simple.

Cada adapter implementa una subclase de `PatrimonioAdapter` y se registra
en `runner.REGISTRY`. El runner los itera y llama `fetch_holdings()`.
"""
from __future__ import annotations

import base64
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

from ...utils import get_logger, project_path
from ..keychain import get_or_create_master_key

log = get_logger("patrimonio.adapters.base")

STATE_DIR = project_path("src", "patrimonio", "state")


class AdapterError(Exception):
    pass


class SessionExpired(AdapterError):
    """La sesión persistida ya no es válida — Diego tiene que correr `login`."""


class CredentialsRejected(AdapterError):
    """El sitio rechazó user/clave. Stop antes de intentar de nuevo."""


class ScrapeBroken(AdapterError):
    """Selectores cambiaron, la UI no es la que esperábamos."""


@dataclass
class Holding:
    """Snapshot de un sitio en un momento dado."""
    site: str  # slug interno: "fintual", "racional", ...
    inversion_id: str  # id para Inversiones_Maestro: "INV-FINTUAL", ...
    nombre: str  # display: "Fintual"
    clase: str  # "Renta variable" | "Renta fija" | "Cash" | ...
    subclase: str  # libre, ej "Fondos mutuos", "Depósito a plazo"
    institucion: str  # display, ej "Fintual"
    moneda: str  # "CLP" | "USD" | "UF"
    valor_moneda_orig: float
    tipo_cambio: float  # 1.0 si moneda == CLP
    valor_clp: float
    fecha: datetime = field(default_factory=datetime.now)
    estado: str = "ok"  # "ok" | "sesion_expirada" | "error:<reason>" | "manual"
    notas_extra: str = ""

    def notas_para_sheet(self) -> str:
        ts = self.fecha.strftime("%Y-%m-%d %H:%M")
        parts = [f"act:{ts}", f"scraper:{self.estado}"]
        if self.notas_extra:
            parts.append(self.notas_extra)
        return " · ".join(parts)


# Flags Chromium para evadir detección de automation (Google, Cloudflare, etc.)
# Mismo set probado en src/scraper.py para BCh/Falabella (incidente 2026-05-15).
STEALTH_LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-dev-shm-usage",
    "--disable-features=IsolateOrigins,site-per-process,AutomationControlled",
    "--disable-site-isolation-trials",
]

# Script inyectado pre-navigation que enmascara señales típicas que sitios
# como Google (SSO) usan para detectar Chromium headless / WebDriver.
STEALTH_INIT_SCRIPT = """
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
    Object.defineProperty(navigator, 'plugins', {
        get: () => [{name: 'Chrome PDF Plugin'}, {name: 'Chrome PDF Viewer'}, {name: 'Native Client'}]
    });
    Object.defineProperty(navigator, 'languages', { get: () => ['es-CL', 'es', 'en-US', 'en'] });
    Object.defineProperty(navigator, 'platform', { get: () => 'MacIntel' });
    window.chrome = { runtime: {}, loadTimes: function(){}, csi: function(){} };
    const origQuery = window.navigator.permissions.query;
    window.navigator.permissions.query = (parameters) => (
        parameters.name === 'notifications'
            ? Promise.resolve({state: Notification.permission})
            : origQuery(parameters)
    );
"""


class PatrimonioAdapter(ABC):
    """Subclasea esto para implementar un sitio nuevo."""

    SITE: str = ""
    INVERSION_ID: str = ""
    NOMBRE_DISPLAY: str = ""
    CLASE: str = ""
    SUBCLASE: str = ""
    INSTITUCION: str = ""
    MONEDA: str = "CLP"
    LIQUIDEZ: str = "Alta"  # "Alta" | "Media" | "Baja"
    PAIS: str = "Chile"

    def __init__(self) -> None:
        if not self.SITE:
            raise ValueError(f"{type(self).__name__} no setea SITE")
        if not self.INVERSION_ID:
            raise ValueError(f"{type(self).__name__} no setea INVERSION_ID")

    # ---------- API publica ---------------------------------------------------

    @abstractmethod
    def login(self, headed: bool = True) -> None:
        """Primer login interactivo. Abre Chromium visible, deja que el
        usuario resuelva 2FA/captcha, persiste storage_state cifrado."""

    @abstractmethod
    def fetch_holdings(self) -> Holding:
        """Carga sesión persistida, navega, extrae el valor del portafolio.
        Levanta SessionExpired si la sesión no sirve."""

    # ---------- Storage de sesión cifrada -------------------------------------

    def state_path(self) -> Path:
        return STATE_DIR / f"state_{self.SITE}.json.enc"

    def has_state(self) -> bool:
        return self.state_path().exists()

    def save_state(self, raw_json_bytes: bytes) -> None:
        """Cifra storage_state JSON y escribe a disco."""
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        token = Fernet(get_or_create_master_key()).encrypt(raw_json_bytes)
        # base64 encode para que el archivo sea ASCII-safe (más fácil de
        # inspeccionar y debuggear que un blob binario).
        self.state_path().write_bytes(base64.b64encode(token))
        log.info("Sesión guardada (cifrada): %s", self.state_path().name)

    def load_state(self) -> Optional[bytes]:
        """Devuelve el storage_state descifrado o None si no existe.

        Levanta SessionExpired si el archivo existe pero la master key no lo
        descifra (rara vez — solo si rotó la key manualmente).
        """
        path = self.state_path()
        if not path.exists():
            return None
        try:
            token = base64.b64decode(path.read_bytes())
            return Fernet(get_or_create_master_key()).decrypt(token)
        except InvalidToken as e:
            raise SessionExpired(
                f"Sesión cifrada para {self.SITE} no se puede descifrar — "
                f"master key rotada o blob corrupto. Re-login con `login {self.SITE}`."
            ) from e

    def clear_state(self) -> None:
        if self.state_path().exists():
            self.state_path().unlink()
            log.info("Sesión borrada: %s", self.state_path().name)

    # ---------- Metadata para Inversiones_Maestro -----------------------------

    def maestro_row(self) -> dict:
        """Row para `Inversiones_Maestro` (alta inicial)."""
        return {
            "id": self.INVERSION_ID,
            "activo": self.NOMBRE_DISPLAY,
            "clase": self.CLASE,
            "subclase": self.SUBCLASE,
            "moneda": self.MONEDA,
            "pais": self.PAIS,
            "institucion": self.INSTITUCION,
            "liquidez": self.LIQUIDEZ,
            "fecha_inicio": datetime.now().strftime("%d/%m/%Y"),
            "activa": True,
        }
