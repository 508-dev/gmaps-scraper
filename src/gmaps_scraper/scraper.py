"""Google Maps saved-list scraper with HTTP and browser collectors."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urljoin

from gmaps_scraper.models import SavedList
from gmaps_scraper.parser import JSONValue, ParseError, parse_saved_list_artifacts

type CollectionMode = Literal["auto", "curl", "browser"]

_CONSENT_URL_MARKERS = ("consent.google", "consent.youtube")
_HTTP_IMPERSONATE = "chrome"
DEFAULT_COLLECTION_MODE: CollectionMode = "auto"
_PRELOADED_FETCH_HREF_PATTERN = re.compile(
    r'<link\b[^>]*\bhref="([^"]*/maps/preview/[^"]+)"[^>]*\bas="fetch"',
    re.IGNORECASE,
)
_SCRIPT_TEXT_PATTERN = re.compile(
    r"<script\b[^>]*>(.*?)</script>",
    re.IGNORECASE | re.DOTALL,
)
_CONSENT_TEXT_MARKERS = (
    "before you continue to google",
    "prima di continuare su google",
    "avant de continuer sur google",
    "bevor sie zu google weitergehen",
    "antes de continuar en google",
    "voordat je doorgaat naar google",
    "g.co/privacytools",
)
_REJECT_BUTTON_LABELS = (
    "Reject all",
    "Rifiuta tutto",
    "Tout refuser",
    "Alle ablehnen",
    "Rechazar todo",
    "Tudo recusar",
    "Alles afwijzen",
    "Odrzuć wszystko",
    "Avvisa allt",
    "Afvis alle",
    "Hylää kaikki",
)
_MORE_OPTIONS_BUTTON_LABELS = (
    "More options",
    "Altre opzioni",
    "Plus d'options",
    "Weitere Optionen",
    "Más opciones",
    "Mais opções",
    "Meer opties",
    "Więcej opcji",
    "Fler alternativ",
    "Flere valgmuligheder",
    "Lisää vaihtoehtoja",
)


@dataclass(slots=True)
class BrowserArtifacts:
    """Artifacts collected from a browser session."""

    resolved_url: str | None
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
    collection_mode: CollectionMode = DEFAULT_COLLECTION_MODE,
) -> SavedList:
    """Scrape and parse a Google Maps saved list."""
    _, result = collect_saved_list_result(
        list_url,
        headless=headless,
        timeout_ms=timeout_ms,
        settle_time_ms=settle_time_ms,
        collection_mode=collection_mode,
    )
    return result


def collect_saved_list_result(
    list_url: str,
    *,
    headless: bool = True,
    timeout_ms: int = 30_000,
    settle_time_ms: int = 3_000,
    collection_mode: CollectionMode = DEFAULT_COLLECTION_MODE,
) -> tuple[BrowserArtifacts, SavedList]:
    """Collect artifacts and parse a Google Maps saved list."""
    normalized_mode = _normalize_collection_mode(collection_mode)

    if normalized_mode == "browser":
        artifacts = collect_browser_artifacts(
            list_url,
            headless=headless,
            timeout_ms=timeout_ms,
            settle_time_ms=settle_time_ms,
        )
        return artifacts, _parse_saved_list(
            list_url,
            artifacts=artifacts,
        )

    if normalized_mode == "curl":
        artifacts = collect_http_artifacts(
            list_url,
            timeout_ms=timeout_ms,
        )
        return artifacts, _parse_saved_list(
            list_url,
            artifacts=artifacts,
        )

    try:
        artifacts = collect_http_artifacts(
            list_url,
            timeout_ms=timeout_ms,
        )
        return artifacts, _parse_saved_list(
            list_url,
            artifacts=artifacts,
        )
    except (ParseError, ScrapeError):
        artifacts = collect_browser_artifacts(
            list_url,
            headless=headless,
            timeout_ms=timeout_ms,
            settle_time_ms=settle_time_ms,
        )
        return artifacts, _parse_saved_list(
            list_url,
            artifacts=artifacts,
        )


def _parse_saved_list(
    list_url: str,
    *,
    artifacts: BrowserArtifacts,
) -> SavedList:
    return parse_saved_list_artifacts(
        list_url,
        resolved_url=artifacts.resolved_url,
        runtime_state=artifacts.runtime_state,
        script_texts=artifacts.script_texts,
        html=artifacts.html,
    )


def collect_http_artifacts(
    list_url: str,
    *,
    timeout_ms: int,
) -> BrowserArtifacts:
    """Load a page over HTTP and collect runtime artifacts from preload responses."""
    curl_requests = _import_curl_requests()
    timeout_seconds = max(timeout_ms / 1_000, 1.0)

    try:
        with curl_requests.Session(
            impersonate=_HTTP_IMPERSONATE,
            allow_redirects=True,
            default_headers=True,
            timeout=timeout_seconds,
        ) as session:
            response = session.get(list_url)
            _raise_for_status(response)
            resolved_url = _normalize_response_url(getattr(response, "url", None))
            page_html = _response_text(response)
            script_texts = _extract_script_texts_from_html(page_html)
            preload_url = _extract_preloaded_fetch_url(
                page_html,
                base_url=resolved_url or list_url,
            )
            if preload_url is not None:
                try:
                    preload_response = session.get(
                        preload_url,
                        referer=resolved_url or list_url,
                    )
                    _raise_for_status(preload_response)
                except Exception:
                    pass
                else:
                    preload_text = _response_text(preload_response)
                    if preload_text.strip():
                        script_texts.append(preload_text)
    except Exception as exc:  # pragma: no cover - network error path
        raise ScrapeError(f"Failed to collect HTTP artifacts: {exc}") from exc

    return BrowserArtifacts(
        resolved_url=resolved_url,
        runtime_state=None,
        script_texts=script_texts,
        html=page_html,
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
        raise ScrapeError("CloakBrowser is not installed. Run `uv sync`.") from exc

    browser = launch(headless=headless, humanize=True)
    try:
        page = browser.new_page()
        page.goto(list_url, wait_until="domcontentloaded", timeout=timeout_ms)
        _handle_google_consent(page, timeout_ms=timeout_ms)
        try:
            page.wait_for_load_state("load", timeout=min(timeout_ms, 10_000))
        except Exception:
            pass
        _handle_google_consent(page, timeout_ms=timeout_ms)
        page.wait_for_timeout(settle_time_ms)

        resolved_url = _read_resolved_url(page)
        runtime_state = _read_runtime_state(page, timeout_ms=timeout_ms)
        script_texts = _read_script_texts(page)
        html = page.content()
    except Exception as exc:  # pragma: no cover - browser error path
        raise ScrapeError(f"Failed to collect browser artifacts: {exc}") from exc
    finally:
        browser.close()

    return BrowserArtifacts(
        resolved_url=resolved_url,
        runtime_state=runtime_state,
        script_texts=script_texts,
        html=html,
    )


def _read_resolved_url(page: Any) -> str | None:
    value = getattr(page, "url", None)
    return _normalize_response_url(value)


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


def _handle_google_consent(page: Any, *, timeout_ms: int) -> None:
    for _ in range(2):
        if not _has_google_consent_screen(page):
            return
        if _click_button_in_contexts(page, _REJECT_BUTTON_LABELS):
            _settle_after_consent(page, timeout_ms=timeout_ms)
            continue
        if _click_button_in_contexts(page, _MORE_OPTIONS_BUTTON_LABELS):
            page.wait_for_timeout(500)
            if _click_button_in_contexts(page, _REJECT_BUTTON_LABELS):
                _settle_after_consent(page, timeout_ms=timeout_ms)
                continue
        break

    if not _has_google_consent_screen(page):
        return

    diagnostics = _capture_consent_diagnostics(page)
    details = ", ".join(str(path) for path in diagnostics)
    raise ScrapeError(
        f"Detected a Google consent screen but could not reject cookies automatically. "
        f"Saved diagnostics: {details}"
    )


def _settle_after_consent(page: Any, *, timeout_ms: int) -> None:
    try:
        page.wait_for_load_state("load", timeout=min(timeout_ms, 10_000))
    except Exception:
        pass
    page.wait_for_timeout(1_000)


def _has_google_consent_screen(page: Any) -> bool:
    url = str(getattr(page, "url", "")).lower()
    if any(marker in url for marker in _CONSENT_URL_MARKERS):
        return True

    for context in _iter_contexts(page):
        body_text = _read_body_text(context).lower()
        if any(marker in body_text for marker in _CONSENT_TEXT_MARKERS):
            return True
    return False


def _click_button_in_contexts(page: Any, labels: tuple[str, ...]) -> bool:
    pattern = _button_label_pattern(labels)
    for context in _iter_contexts(page):
        if _click_button(context, pattern=pattern, labels=labels):
            return True
    return False


def _click_button(context: Any, *, pattern: re.Pattern[str], labels: tuple[str, ...]) -> bool:
    try:
        context.get_by_role("button", name=pattern).first.click(timeout=1_500)
    except Exception:
        return _click_button_with_dom(context, labels)
    return True


def _button_label_pattern(labels: tuple[str, ...]) -> re.Pattern[str]:
    escaped_labels = [re.escape(label) for label in labels]
    return re.compile(rf"^\s*(?:{'|'.join(escaped_labels)})\s*$", re.IGNORECASE)


def _iter_contexts(page: Any) -> list[Any]:
    contexts = [page]
    for frame in getattr(page, "frames", []):
        if frame is page:
            continue
        contexts.append(frame)
    return contexts


def _read_body_text(context: Any) -> str:
    try:
        value = context.evaluate("() => document.body?.innerText ?? ''")
    except Exception:
        return ""
    if not isinstance(value, str):
        return ""
    return value


def _click_button_with_dom(context: Any, labels: tuple[str, ...]) -> bool:
    script = """
    (labels) => {
      const normalize = (value) => value.trim().replace(/\\s+/g, " ").toLowerCase();
      const expected = new Set(labels.map(normalize));
      const selector = [
        "button",
        '[role="button"]',
        'input[type="button"]',
        'input[type="submit"]'
      ].join(", ");
      const candidates = Array.from(document.querySelectorAll(selector));

      for (const element of candidates) {
        const text = normalize(element.innerText || element.textContent || element.value || "");
        if (!expected.has(text)) {
          continue;
        }
        element.click();
        return true;
      }
      return false;
    }
    """
    try:
        clicked = context.evaluate(script, list(labels))
    except Exception:
        return False
    return clicked is True


def _capture_consent_diagnostics(page: Any) -> list[Path]:
    diagnostics_dir = Path(".context/diagnostics")
    diagnostics_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")

    saved_paths: list[Path] = []
    html_path = diagnostics_dir / f"google-consent-{timestamp}.html"
    text_path = diagnostics_dir / f"google-consent-{timestamp}.txt"
    screenshot_path = diagnostics_dir / f"google-consent-{timestamp}.png"

    try:
        html_path.write_text(page.content(), encoding="utf-8")
        saved_paths.append(html_path)
    except Exception:
        pass

    try:
        text_path.write_text(_read_body_text(page), encoding="utf-8")
        saved_paths.append(text_path)
    except Exception:
        pass

    try:
        page.screenshot(path=str(screenshot_path), full_page=True)
        saved_paths.append(screenshot_path)
    except Exception:
        pass

    return saved_paths


def _import_curl_requests() -> Any:
    try:
        from curl_cffi import requests as curl_requests
    except ImportError as exc:  # pragma: no cover - dependency error path
        raise ScrapeError("curl_cffi is not installed. Run `uv sync`.") from exc
    return curl_requests


def _extract_script_texts_from_html(page_html: str) -> list[str]:
    return [
        match.group(1)
        for match in _SCRIPT_TEXT_PATTERN.finditer(page_html)
        if match.group(1).strip()
    ]


def _extract_preloaded_fetch_url(page_html: str, *, base_url: str) -> str | None:
    match = _PRELOADED_FETCH_HREF_PATTERN.search(page_html)
    if match is None:
        return None
    href = html.unescape(match.group(1))
    if not href.strip():
        return None
    return urljoin(base_url, href)


def _normalize_response_url(value: object) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    if not normalized:
        return None
    return normalized


def _response_text(response: Any) -> str:
    value = getattr(response, "text", "")
    if isinstance(value, str):
        return value
    return str(value)


def _raise_for_status(response: Any) -> None:
    raise_for_status = getattr(response, "raise_for_status", None)
    if callable(raise_for_status):
        raise_for_status()


def _normalize_collection_mode(collection_mode: CollectionMode) -> CollectionMode:
    return collection_mode
