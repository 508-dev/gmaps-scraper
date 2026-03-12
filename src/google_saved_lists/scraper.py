"""Browser-backed Google Maps saved-list scraper."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from google_saved_lists.models import SavedList
from google_saved_lists.parser import JSONValue, parse_saved_list_artifacts


@dataclass(slots=True)
class BrowserArtifacts:
    """Artifacts collected from a browser session."""

    runtime_state: JSONValue | None
    script_texts: list[str]
    html: str


class ScrapeError(RuntimeError):
    """Raised when browser automation fails."""


def scrape_saved_list(
    list_url: str,
    *,
    headless: bool = True,
    timeout_ms: int = 30_000,
    settle_time_ms: int = 3_000,
) -> SavedList:
    """Scrape and parse a Google Maps saved list."""
    artifacts = collect_browser_artifacts(
        list_url,
        headless=headless,
        timeout_ms=timeout_ms,
        settle_time_ms=settle_time_ms,
    )
    return parse_saved_list_artifacts(
        list_url,
        runtime_state=artifacts.runtime_state,
        script_texts=artifacts.script_texts,
        html=artifacts.html,
    )


def collect_browser_artifacts(
    list_url: str,
    *,
    headless: bool,
    timeout_ms: int,
    settle_time_ms: int,
) -> BrowserArtifacts:
    """Load a page in CloakBrowser and collect runtime artifacts."""
    try:
        from cloakbrowser import launch  # type: ignore[import-untyped]
    except ImportError as exc:  # pragma: no cover - dependency error path
        raise ScrapeError("CloakBrowser is not installed. Run `uv sync --dev`.") from exc

    browser = launch(headless=headless, humanize=True)
    try:
        page = browser.new_page()
        page.goto(list_url, wait_until="domcontentloaded", timeout=timeout_ms)
        try:
            page.wait_for_load_state("networkidle", timeout=timeout_ms)
        except Exception:
            pass
        page.wait_for_timeout(settle_time_ms)

        runtime_state = _read_runtime_state(page, timeout_ms=timeout_ms)
        script_texts = _read_script_texts(page)
        html = page.content()
    except Exception as exc:  # pragma: no cover - browser error path
        raise ScrapeError(f"Failed to collect browser artifacts: {exc}") from exc
    finally:
        browser.close()

    return BrowserArtifacts(runtime_state=runtime_state, script_texts=script_texts, html=html)


def _read_runtime_state(page: Any, *, timeout_ms: int) -> JSONValue | None:
    attempts = max(1, timeout_ms // 1_000)
    for _ in range(attempts):
        runtime_state = page.evaluate(
            "() => globalThis.APP_INITIALIZATION_STATE ?? window.APP_INITIALIZATION_STATE ?? null"
        )
        if isinstance(runtime_state, (list, dict)):
            return runtime_state
        page.wait_for_timeout(1_000)
    return None


def _read_script_texts(page: Any) -> list[str]:
    script_texts = page.evaluate(
        "() => Array.from(document.scripts, (script) => script.textContent || '')"
    )
    if not isinstance(script_texts, list):
        return []
    return [text for text in script_texts if isinstance(text, str)]
