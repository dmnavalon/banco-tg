from __future__ import annotations

from typing import Iterable

from playwright.sync_api import Locator, Page, TimeoutError as PWTimeout


class LoginFailed(Exception):
    pass


class TwoFARequired(Exception):
    pass


class CaptchaPresent(Exception):
    pass


class ScraperBroken(Exception):
    pass


def first_visible(page: Page, selectors: Iterable[str], timeout_ms: int = 5000) -> Locator | None:
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            loc.wait_for(state="visible", timeout=timeout_ms)
            return loc
        except PWTimeout:
            continue
    return None


def click_first(page: Page, selectors: Iterable[str], timeout_ms: int = 10000) -> bool:
    for sel in selectors:
        try:
            page.wait_for_selector(sel, state="attached", timeout=timeout_ms)
            loc = page.locator(sel).first
            if loc.is_visible(timeout=2000):
                loc.click(timeout=5000)
                return True
        except PWTimeout:
            continue
        except Exception:
            continue
    return False


def any_present(page: Page, selectors: Iterable[str], timeout_ms: int = 2000) -> bool:
    for sel in selectors:
        try:
            page.wait_for_selector(sel, state="attached", timeout=timeout_ms)
            return True
        except PWTimeout:
            continue
    return False


def fill_first(page: Page, selectors: Iterable[str], value: str, timeout_ms: int = 5000) -> bool:
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            loc.wait_for(state="visible", timeout=timeout_ms)
            loc.fill(value)
            return True
        except PWTimeout:
            continue
        except Exception:
            continue
    return False


def read_table_rows(page: Page, table_selectors: Iterable[str]) -> dict | None:
    """Lee la tabla con más filas que matche cualquiera de los selectores.

    Devuelve {"headers": [...], "rows": [[...], ...]} o None si no encuentra
    una tabla con al menos 2 filas.
    """
    selector = ", ".join(table_selectors)
    return page.evaluate(
        """
        (selector) => {
            const tables = document.querySelectorAll(selector);
            if (!tables.length) return null;
            let best = null;
            let maxRows = 0;
            tables.forEach((t) => {
                const rows = t.querySelectorAll('tr');
                if (rows.length > maxRows) {
                    maxRows = rows.length;
                    best = t;
                }
            });
            if (!best || maxRows < 2) return null;
            const headerRow = best.querySelector('thead tr') || best.querySelector('tr');
            const headers = [];
            if (headerRow) {
                headerRow.querySelectorAll('th, td').forEach((c) => headers.push(c.innerText.trim()));
            }
            const rows = [];
            const hasThead = !!best.querySelector('thead');
            const dataRows = best.querySelectorAll(hasThead ? 'tbody tr' : 'tr');
            dataRows.forEach((row, i) => {
                if (i === 0 && !hasThead) return;
                const cells = [];
                row.querySelectorAll('td').forEach((c) => cells.push(c.innerText.trim()));
                if (cells.length) rows.push(cells);
            });
            return { headers, rows };
        }
        """,
        selector,
    )
