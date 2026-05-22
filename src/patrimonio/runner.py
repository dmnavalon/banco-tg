"""Orquesta corridas de scrapers: itera sitios, escribe a Sheets, manda Telegram.

Llamado desde:
- CLI: `python -m src.patrimonio.cli run [site]`
- Endpoint HTTP (botón "Actualizar ahora" del dashboard)
- launchd: cada Domingo 22:00
"""
from __future__ import annotations

from datetime import datetime
from typing import Callable

from .. import telegram_notify
from ..utils import get_logger, format_clp
from . import keychain
from .adapters.base import (
    Holding,
    PatrimonioAdapter,
    SessionExpired,
    CredentialsRejected,
    ScrapeBroken,
)
from .keychain import CredentialNotFound
from .sheets_writer import ensure_maestro_row, mark_snapshot_error, upsert_snapshot

log = get_logger("patrimonio.runner")


def _adapter_classes() -> dict[str, type[PatrimonioAdapter]]:
    """Lazy import del registry: evita cargar todos los adapters salvo cuando
    realmente se necesita (algunos pueden importar Playwright + libs pesadas)."""
    out: dict[str, type[PatrimonioAdapter]] = {}
    try:
        from .adapters.fintual import FintualAdapter
        out["fintual"] = FintualAdapter
    except ImportError as e:
        log.warning("Adapter Fintual no disponible: %s", e)
    try:
        from .adapters.racional import RacionalAdapter
        out["racional"] = RacionalAdapter
    except ImportError as e:
        log.debug("Adapter Racional aún no implementado: %s", e)
    try:
        from .adapters.bancochile_inv import BancoChileInvAdapter
        out["bch_inv"] = BancoChileInvAdapter
    except ImportError as e:
        log.debug("Adapter BCh Inversiones aún no implementado: %s", e)
    try:
        from .adapters.nauta import NautaAdapter
        out["nauta"] = NautaAdapter
    except ImportError as e:
        log.debug("Adapter NAUTA aún no implementado: %s", e)
    try:
        from .adapters.mercadopago import MercadoPagoAdapter
        out["mercadopago"] = MercadoPagoAdapter
    except ImportError as e:
        log.debug("Adapter Mercado Pago aún no implementado: %s", e)
    return out


def run_one(site: str, progress: Callable[[str], None] | None = None) -> Holding:
    """Corre un solo sitio. Lanza excepción al caller si falla.

    Side effects: escribe a Inversiones_Maestro (alta una vez) e
    Inversiones_Snapshot (upsert del mes actual).
    """
    def _p(msg: str) -> None:
        log.info(msg)
        if progress:
            progress(msg)

    classes = _adapter_classes()
    if site not in classes:
        raise ValueError(f"Sitio no soportado: {site}. Disponibles: {', '.join(classes)}")
    adapter = classes[site]()

    # Validación previa: credencial existe
    try:
        keychain.get_credential(site)
    except CredentialNotFound:
        raise RuntimeError(
            f"{site}: sin credencial en Keychain. Corre `python -m src.patrimonio.cli add {site}`."
        )

    _p(f"🔍 [{site}] Conectando…")
    holding = adapter.fetch_holdings()
    ensure_maestro_row(adapter.maestro_row())
    upsert_snapshot(holding)
    _p(f"✅ [{site}] {format_clp(holding.valor_clp)} guardado en Sheets")
    return holding


def run_all(progress: Callable[[str], None] | None = None) -> dict:
    """Corre TODOS los sitios con credencial configurada. No falla en bloque
    si uno revienta — captura por sitio, sigue con el resto, y al final
    manda Telegram con el resumen.

    Returns: dict con `ok`, `errors`, `total_clp`, `details`.
    """
    def _p(msg: str) -> None:
        log.info(msg)
        if progress:
            progress(msg)

    configured = {s["site"] for s in keychain.list_sites()}
    classes = _adapter_classes()
    candidates = [s for s in classes if s in configured]

    details: list[dict] = []
    holdings_ok: list[Holding] = []
    errors = 0

    for site in candidates:
        try:
            holding = run_one(site, progress=_p)
            holdings_ok.append(holding)
            details.append({"site": site, "status": "ok", "valor_clp": holding.valor_clp})
        except SessionExpired as e:
            _p(f"⚠️  [{site}] Sesión expirada: {e}")
            mark_snapshot_error(_inversion_id_for(site), "sesion_expirada")
            telegram_notify.send_message(
                f"⚠️ Patrimonio: sesión expirada en {site}. "
                f"Corre `python -m src.patrimonio.cli login {site}` cuando puedas."
            )
            errors += 1
            details.append({"site": site, "status": "sesion_expirada", "error": str(e)})
        except CredentialsRejected as e:
            _p(f"❌ [{site}] Credenciales rechazadas: {e}")
            mark_snapshot_error(_inversion_id_for(site), "credenciales_rechazadas")
            telegram_notify.send_message(
                f"❌ Patrimonio: {site} rechazó la credencial. "
                f"Corre `python -m src.patrimonio.cli add {site}` con la clave nueva."
            )
            errors += 1
            details.append({"site": site, "status": "credenciales", "error": str(e)})
        except (ScrapeBroken, Exception) as e:
            _p(f"❌ [{site}] {type(e).__name__}: {e}")
            try:
                mark_snapshot_error(_inversion_id_for(site), f"{type(e).__name__}")
            except Exception:
                pass
            errors += 1
            details.append({"site": site, "status": "error", "error": str(e)})

    total = sum(h.valor_clp for h in holdings_ok)
    summary = {
        "ok": len(holdings_ok),
        "errors": errors,
        "total_clp": total,
        "details": details,
        "fecha": datetime.now().isoformat(timespec="seconds"),
    }

    # Telegram resumen
    parts = [f"📊 Patrimonio actualizado: {format_clp(total)}"]
    for h in holdings_ok:
        parts.append(f"  • {h.nombre}: {format_clp(h.valor_clp)}")
    if errors:
        parts.append(f"⚠️ {errors} sitio(s) con problemas (ver detalle arriba)")
    try:
        telegram_notify.send_message("\n".join(parts))
    except Exception as e:
        log.warning("No pude mandar Telegram resumen: %s", e)

    return summary


def write_manual_snapshot(site: str, amount: float, nota: str) -> None:
    """Edit a mano: escribe un snapshot sin pasar por scraper."""
    classes = _adapter_classes()
    if site not in classes:
        raise ValueError(f"Sitio no soportado: {site}.")
    adapter = classes[site]()
    holding = Holding(
        site=adapter.SITE,
        inversion_id=adapter.INVERSION_ID,
        nombre=adapter.NOMBRE_DISPLAY,
        clase=adapter.CLASE,
        subclase=adapter.SUBCLASE,
        institucion=adapter.INSTITUCION,
        moneda=adapter.MONEDA,
        valor_moneda_orig=amount,
        tipo_cambio=1.0,
        valor_clp=amount,
        fecha=datetime.now(),
        estado="manual",
        notas_extra=nota,
    )
    ensure_maestro_row(adapter.maestro_row())
    upsert_snapshot(holding)


def _inversion_id_for(site: str) -> str:
    classes = _adapter_classes()
    if site in classes:
        return classes[site].INVERSION_ID
    return f"INV-{site.upper()}"
