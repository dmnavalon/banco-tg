"""CLI para administrar credenciales y correr scrapers de patrimonio.

Ejemplos:
    python -m src.patrimonio.cli add fintual
    python -m src.patrimonio.cli list
    python -m src.patrimonio.cli login fintual            # primer login headed
    python -m src.patrimonio.cli run                      # corre todos los sitios
    python -m src.patrimonio.cli run fintual              # corre solo uno
    python -m src.patrimonio.cli edit fintual 12500000 "Ajuste manual"
    python -m src.patrimonio.cli remove fintual
    python -m src.patrimonio.cli status
"""
from __future__ import annotations

import argparse
import getpass
import sys
from datetime import datetime

from ..utils import get_logger
from . import keychain
from .keychain import CredentialNotFound

log = get_logger("patrimonio.cli")

SUPPORTED_SITES = ["fintual", "racional", "bch_inv", "nauta", "mercadopago"]


def _adapter_for(site: str):
    """Lazy import: solo importa adapters cuando se necesitan. Esto evita
    que `add/list/remove` pidan Playwright instalado."""
    from .adapters.base import PatrimonioAdapter
    if site == "fintual":
        from .adapters.fintual import FintualAdapter
        return FintualAdapter()
    if site == "racional":
        from .adapters.racional import RacionalAdapter
        return RacionalAdapter()
    if site == "bch_inv":
        from .adapters.bancochile_inv import BancoChileInvAdapter
        return BancoChileInvAdapter()
    if site == "nauta":
        from .adapters.nauta import NautaAdapter
        return NautaAdapter()
    if site == "mercadopago":
        from .adapters.mercadopago import MercadoPagoAdapter
        return MercadoPagoAdapter()
    raise ValueError(f"Sitio no soportado: {site}. Conocidos: {', '.join(SUPPORTED_SITES)}")


# ---------------------------------------------------------------------------
# Comandos
# ---------------------------------------------------------------------------

def cmd_add(args: argparse.Namespace) -> int:
    site = args.site.strip().lower()
    if site not in SUPPORTED_SITES:
        print(f"❌ Sitio no soportado: {site}")
        print(f"   Conocidos: {', '.join(SUPPORTED_SITES)}")
        return 2
    print(f"Configurando credenciales para: {site}")
    user = input("Usuario / RUT / email: ").strip()
    if not user:
        print("❌ Usuario vacío. Abortado.")
        return 2
    password = getpass.getpass("Clave (no se mostrará): ")
    if not password:
        print("❌ Clave vacía. Abortado.")
        return 2
    keychain.set_credential(site, user, password)
    print(f"✅ Guardado en Mac Keychain ({keychain.SERVICE}/{site}).")
    print(f"   Próximo paso: `python -m src.patrimonio.cli login {site}` para sesión inicial.")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    sites = keychain.list_sites()
    if not sites:
        print("No hay sitios configurados.")
        return 0
    print(f"{'Sitio':<14} {'Usuario':<32} {'Última actualización'}")
    print("-" * 75)
    for s in sites:
        user = (s.get("user") or "").strip() or "(?)"
        upd = (s.get("updated_at") or "").strip() or "(?)"
        print(f"{s['site']:<14} {user:<32} {upd}")
    return 0


def cmd_remove(args: argparse.Namespace) -> int:
    site = args.site.strip().lower()
    if not keychain.delete_credential(site):
        print(f"⚠️  No había credencial para {site} en Keychain.")
        return 1
    print(f"✅ Credencial de {site} borrada del Keychain.")
    # También limpia la sesión Playwright si existe
    try:
        adapter = _adapter_for(site)
        adapter.clear_state()
    except Exception as e:
        log.warning("No pude limpiar state file para %s: %s", site, e)
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    sites = keychain.list_sites()
    print(f"{'Sitio':<14} {'Credencial':<12} {'Sesión Playwright':<22} {'Última act.'}")
    print("-" * 75)
    configured = {s["site"]: s for s in sites}
    for site in SUPPORTED_SITES:
        cred = "✅ ok" if site in configured else "❌ falta"
        try:
            adapter = _adapter_for(site)
            sess = "✅ ok" if adapter.has_state() else "⚠️  sin login"
        except Exception:
            sess = "❌ adapter pendiente"
        upd = configured.get(site, {}).get("updated_at", "")
        print(f"{site:<14} {cred:<12} {sess:<22} {upd}")
    return 0


def cmd_login(args: argparse.Namespace) -> int:
    site = args.site.strip().lower()
    try:
        adapter = _adapter_for(site)
    except (ValueError, ModuleNotFoundError) as e:
        print(f"❌ {e}")
        return 2
    try:
        keychain.get_credential(site)
    except CredentialNotFound:
        print(f"❌ Primero configura credenciales: python -m src.patrimonio.cli add {site}")
        return 2
    print(f"🔓 Abriendo {site} en modo VISIBLE para login manual…")
    print("   Resuelve 2FA/captcha en la ventana. Cuando estés en el home, vuelve acá y presiona ENTER.")
    adapter.login(headed=True)
    print(f"✅ Sesión guardada (cifrada) en src/patrimonio/state/state_{site}.json.enc")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    from .runner import run_one, run_all
    if args.site:
        site = args.site.strip().lower()
        try:
            holding = run_one(site)
        except Exception as e:
            print(f"❌ {site}: {type(e).__name__}: {e}")
            return 1
        print(f"✅ {site}: {holding.notas_para_sheet()} — CLP {holding.valor_clp:,.0f}")
        return 0
    summary = run_all()
    return 0 if summary["errors"] == 0 else 1


def cmd_edit(args: argparse.Namespace) -> int:
    from .runner import write_manual_snapshot
    site = args.site.strip().lower()
    try:
        amount = float(args.amount)
    except ValueError:
        print(f"❌ Monto inválido: {args.amount}")
        return 2
    write_manual_snapshot(site, amount, args.nota or "")
    print(f"✅ {site}: snapshot manual escrito (CLP {amount:,.0f}).")
    return 0


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="patrimonio", description="Patrimonio CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp_add = sub.add_parser("add", help="Agregar/actualizar credencial de un sitio")
    sp_add.add_argument("site")
    sp_add.set_defaults(func=cmd_add)

    sp_list = sub.add_parser("list", help="Listar sitios configurados")
    sp_list.set_defaults(func=cmd_list)

    sp_rm = sub.add_parser("remove", help="Borrar credencial de un sitio")
    sp_rm.add_argument("site")
    sp_rm.set_defaults(func=cmd_remove)

    sp_st = sub.add_parser("status", help="Estado de credenciales y sesiones")
    sp_st.set_defaults(func=cmd_status)

    sp_lg = sub.add_parser("login", help="Login manual (primera vez o tras expiración)")
    sp_lg.add_argument("site")
    sp_lg.set_defaults(func=cmd_login)

    sp_run = sub.add_parser("run", help="Correr scrapers (todos o uno)")
    sp_run.add_argument("site", nargs="?", default=None)
    sp_run.set_defaults(func=cmd_run)

    sp_ed = sub.add_parser("edit", help="Snapshot manual sin scraper")
    sp_ed.add_argument("site")
    sp_ed.add_argument("amount", help="Monto CLP")
    sp_ed.add_argument("nota", nargs="?", default="")
    sp_ed.set_defaults(func=cmd_edit)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
