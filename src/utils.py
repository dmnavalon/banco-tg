import hashlib
import logging
import os
import re
import sys
import unicodedata
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


def normalize(s: str | None) -> str:
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", s).strip().upper()


def movement_id(date_iso: str, amount: float, description: str, bank: str, account: str | None) -> str:
    key = "|".join([
        date_iso,
        f"{amount:.2f}",
        normalize(description),
        normalize(bank),
        normalize(account or ""),
    ])
    return hashlib.sha1(key.encode()).hexdigest()[:16]


def mask(s: str | None, keep: int = 4) -> str:
    if not s:
        return ""
    s = str(s)
    if len(s) <= keep:
        return "*" * len(s)
    return "*" * (len(s) - keep) + s[-keep:]


def parse_clp_amount(raw: str) -> float | None:
    if raw is None:
        return None
    cleaned = (
        str(raw)
        .strip()
        .replace(" ", " ")
        .replace(" ", "")
    )
    negative = cleaned.startswith("-") or cleaned.endswith("-")
    cleaned = cleaned.lstrip("-").rstrip("-")
    cleaned = cleaned.lstrip("$")
    cleaned = cleaned.replace(".", "")
    cleaned = cleaned.replace(",", ".")
    if not cleaned:
        return None
    try:
        num = float(cleaned)
    except ValueError:
        return None
    return -num if negative else num


def parse_chilean_date(raw: str) -> str:
    if not raw:
        return ""
    cleaned = raw.strip()
    m = re.match(r"^(\d{1,2})[/\-](\d{1,2})[/\-](\d{2,4})$", cleaned)
    if not m:
        return cleaned
    day, month, year = m.group(1), m.group(2), m.group(3)
    if len(year) == 2:
        year = "20" + year
    return f"{year}-{month.zfill(2)}-{day.zfill(2)}"


def format_clp(amount: float) -> str:
    sign = "-" if amount < 0 else ""
    abs_amount = abs(int(round(amount)))
    return f"{sign}${abs_amount:,.0f}".replace(",", ".")


_LOGGER_CACHE: dict[str, logging.Logger] = {}


def get_logger(name: str) -> logging.Logger:
    if name in _LOGGER_CACHE:
        return _LOGGER_CACHE[name]
    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, level, logging.INFO))
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
        logger.addHandler(handler)
        logger.propagate = False
    _LOGGER_CACHE[name] = logger
    return logger


def project_path(*parts: str) -> Path:
    return PROJECT_ROOT.joinpath(*parts)
