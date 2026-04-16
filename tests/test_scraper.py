from __future__ import annotations

import re
import unittest
from typing import Any
from unittest.mock import Mock, patch

from gmaps_scraper.parser import ParseError
from gmaps_scraper.scraper import (
    BrowserArtifacts,
    ScrapeError,
    _handle_google_consent,
    _has_google_consent_screen,
    _read_resolved_url,
    collect_http_artifacts,
    collect_saved_list_result,
)


class _FakeLocator:
    def __init__(self, context: _FakeContext, pattern: re.Pattern[str]) -> None:
        self._context = context
        self._pattern = pattern
        self.first = self

    def click(self, timeout: int) -> None:
        del timeout
        if not self._context.allow_role_click:
            raise RuntimeError("Role-based click disabled")
        for label in self._context.buttons:
            if self._pattern.search(label):
                self._context.clicked.append(label)
                self._context.dismiss_consent()
                return
        raise RuntimeError("No matching button")


class _FakeContext:
    def __init__(
        self,
        *,
        text: str,
        buttons: list[str] | None = None,
        url: str = "",
        frames: list[_FakeContext] | None = None,
        allow_role_click: bool = True,
        dismiss_on_click: bool = True,
    ) -> None:
        self._text = text
        self.buttons = buttons or []
        self.url = url
        self.frames = frames or []
        self.allow_role_click = allow_role_click
        self.dismiss_on_click = dismiss_on_click
        self.clicked: list[str] = []
        self.timeouts: list[int] = []

    def evaluate(self, script: str, argument: Any = None) -> Any:
        if "querySelectorAll" in script and isinstance(argument, list):
            normalized_labels = {
                label.strip().lower()
                for label in argument
                if isinstance(label, str)
            }
            for label in self.buttons:
                if label.strip().lower() in normalized_labels:
                    self.clicked.append(label)
                    self.dismiss_consent()
                    return True
            return False
        if "innerText" in script:
            return self._text
        return None

    def get_by_role(self, role: str, name: re.Pattern[str]) -> _FakeLocator:
        if role != "button":
            raise RuntimeError("Unexpected role")
        return _FakeLocator(self, name)

    def wait_for_timeout(self, milliseconds: int) -> None:
        self.timeouts.append(milliseconds)

    def wait_for_load_state(self, state: str, timeout: int) -> None:
        del state, timeout

    def content(self) -> str:
        return f"<html><body>{self._text}</body></html>"

    def screenshot(self, *, path: str, full_page: bool) -> None:
        del path, full_page

    def dismiss_consent(self) -> None:
        if not self.dismiss_on_click:
            return
        self._text = ""
        self.buttons = []
        self.url = ""


class _FakeHttpResponse:
    def __init__(self, *, text: str, url: str) -> None:
        self.text = text
        self.url = url

    def raise_for_status(self) -> None:
        return None


class _FakeHttpSession:
    def __init__(self, responses: list[_FakeHttpResponse], **kwargs: Any) -> None:
        self._responses = responses
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.kwargs = kwargs

    def __enter__(self) -> _FakeHttpSession:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        del exc_type, exc, tb

    def get(self, url: str, **kwargs: Any) -> _FakeHttpResponse:
        self.calls.append((url, kwargs))
        return self._responses.pop(0)


class _FakeCurlRequests:
    def __init__(self, responses: list[_FakeHttpResponse]) -> None:
        self._responses = responses
        self.sessions: list[_FakeHttpSession] = []

    def Session(self, **kwargs: Any) -> _FakeHttpSession:
        session = _FakeHttpSession(self._responses, **kwargs)
        self.sessions.append(session)
        return session


class ScraperConsentTests(unittest.TestCase):
    def test_reads_resolved_url_from_page(self) -> None:
        page = _FakeContext(
            text="",
            url="https://www.google.com/maps/@30.5370705,125.4120472,6z/data=!4m3!11m2!2sUGEPbA20Qd-OH4uoWjmDgQ!3e3?entry=ttu",
        )

        self.assertEqual(_read_resolved_url(page), page.url)

    def test_detects_italian_consent_screen(self) -> None:
        page = _FakeContext(
            text="Google\nPrima di continuare su Google\nRifiuta tutto\nAccetta tutto",
            buttons=["Rifiuta tutto", "Accetta tutto"],
        )

        self.assertTrue(_has_google_consent_screen(page))

    def test_rejects_cookies_from_main_page(self) -> None:
        page = _FakeContext(
            text="Google\nPrima di continuare su Google\nRifiuta tutto\nAccetta tutto",
            buttons=["Rifiuta tutto", "Accetta tutto", "Altre opzioni"],
        )

        _handle_google_consent(page, timeout_ms=5_000)

        self.assertEqual(page.clicked, ["Rifiuta tutto"])

    def test_rejects_cookies_from_iframe(self) -> None:
        frame = _FakeContext(
            text="Google\nPrima di continuare su Google\nRifiuta tutto\nAccetta tutto",
            buttons=["Rifiuta tutto", "Accetta tutto"],
        )
        page = _FakeContext(text="", frames=[frame])

        _handle_google_consent(page, timeout_ms=5_000)

        self.assertEqual(frame.clicked, ["Rifiuta tutto"])

    def test_falls_back_to_dom_click_when_role_click_fails(self) -> None:
        page = _FakeContext(
            text="Google\nPrima di continuare su Google\nRifiuta tutto\nAccetta tutto",
            buttons=["Rifiuta tutto", "Accetta tutto"],
            allow_role_click=False,
        )

        _handle_google_consent(page, timeout_ms=5_000)

        self.assertEqual(page.clicked, ["Rifiuta tutto"])

    def test_raises_when_reject_button_is_missing(self) -> None:
        page = _FakeContext(
            text="Google\nPrima di continuare su Google\nAccetta tutto",
            buttons=["Accetta tutto", "Altre opzioni"],
            dismiss_on_click=False,
        )

        with patch("gmaps_scraper.scraper._capture_consent_diagnostics", return_value=[]):
            with self.assertRaises(ScrapeError):
                _handle_google_consent(page, timeout_ms=5_000)


class HttpArtifactTests(unittest.TestCase):
    def test_collect_http_artifacts_fetches_preloaded_payload(self) -> None:
        fake_requests = _FakeCurlRequests(
            responses=[
                _FakeHttpResponse(
                    text=(
                        "<html><head>"
                        '<link href="/maps/preview/entitylist/getlist?pb=123" '
                        'as="fetch" rel="preload">'
                        '<script>window.APP_INITIALIZATION_STATE=["inline"];</script>'
                        "</head></html>"
                    ),
                    url="https://www.google.com/maps/@/data=!3m1!4b1",
                ),
                _FakeHttpResponse(
                    text=")]}'\n[[\"payload\"]]",
                    url="https://www.google.com/maps/preview/entitylist/getlist?pb=123",
                ),
            ]
        )

        with patch(
            "gmaps_scraper.scraper._import_curl_requests",
            return_value=fake_requests,
        ):
            artifacts = collect_http_artifacts(
                "https://maps.app.goo.gl/example",
                timeout_ms=15_000,
            )

        self.assertEqual(artifacts.resolved_url, "https://www.google.com/maps/@/data=!3m1!4b1")
        self.assertEqual(
            artifacts.script_texts,
            ['window.APP_INITIALIZATION_STATE=["inline"];', ")]}'\n[[\"payload\"]]"],
        )
        self.assertIn("/maps/preview/entitylist/getlist?pb=123", artifacts.html)

        session = fake_requests.sessions[0]
        self.assertEqual(session.kwargs["impersonate"], "chrome")
        self.assertEqual(session.calls[0][0], "https://maps.app.goo.gl/example")
        self.assertEqual(
            session.calls[1][0],
            "https://www.google.com/maps/preview/entitylist/getlist?pb=123",
        )

    def test_collect_http_artifacts_returns_html_without_preload(self) -> None:
        fake_requests = _FakeCurlRequests(
            responses=[
                _FakeHttpResponse(
                    text=(
                        "<html><body>No preload here</body>"
                        "<script>const value = 1;</script></html>"
                    ),
                    url="https://www.google.com/maps",
                )
            ]
        )

        with patch(
            "gmaps_scraper.scraper._import_curl_requests",
            return_value=fake_requests,
        ):
            artifacts = collect_http_artifacts(
                "https://maps.app.goo.gl/example",
                timeout_ms=15_000,
            )

        self.assertEqual(artifacts.resolved_url, "https://www.google.com/maps")
        self.assertEqual(artifacts.script_texts, ["const value = 1;"])


class SavedListFallbackTests(unittest.TestCase):
    def test_collect_saved_list_result_prefers_http_when_parse_succeeds(self) -> None:
        http_artifacts = BrowserArtifacts(
            resolved_url="https://www.google.com/maps/@/data=!3m1!4b1",
            runtime_state=None,
            script_texts=["http-script"],
            html="<html></html>",
        )
        parsed = Mock()

        with (
            patch("gmaps_scraper.scraper.collect_http_artifacts", return_value=http_artifacts),
            patch(
                "gmaps_scraper.scraper.collect_browser_artifacts"
            ) as collect_browser_artifacts,
            patch("gmaps_scraper.scraper.parse_saved_list_artifacts", return_value=parsed),
        ):
            artifacts, result = collect_saved_list_result("https://maps.app.goo.gl/example")

        self.assertIs(artifacts, http_artifacts)
        self.assertIs(result, parsed)
        collect_browser_artifacts.assert_not_called()

    def test_collect_saved_list_result_uses_http_only_mode(self) -> None:
        http_artifacts = BrowserArtifacts(
            resolved_url="https://www.google.com/maps/@/data=!3m1!4b1",
            runtime_state=None,
            script_texts=["http-script"],
            html="<html></html>",
        )
        parsed = Mock()

        with (
            patch("gmaps_scraper.scraper.collect_http_artifacts", return_value=http_artifacts),
            patch(
                "gmaps_scraper.scraper.collect_browser_artifacts"
            ) as collect_browser_artifacts,
            patch("gmaps_scraper.scraper.parse_saved_list_artifacts", return_value=parsed),
        ):
            artifacts, result = collect_saved_list_result(
                "https://maps.app.goo.gl/example",
                collection_mode="curl",
            )

        self.assertIs(artifacts, http_artifacts)
        self.assertIs(result, parsed)
        collect_browser_artifacts.assert_not_called()

    def test_collect_saved_list_result_falls_back_to_browser_after_http_parse_error(self) -> None:
        http_artifacts = BrowserArtifacts(
            resolved_url="https://www.google.com/maps/@/data=!3m1!4b1",
            runtime_state=None,
            script_texts=["http-script"],
            html="<html></html>",
        )
        browser_artifacts = BrowserArtifacts(
            resolved_url="https://www.google.com/maps/@/data=!3m1!4b1",
            runtime_state=["browser-runtime"],
            script_texts=["browser-script"],
            html="<html></html>",
        )
        parsed = Mock()

        with (
            patch("gmaps_scraper.scraper.collect_http_artifacts", return_value=http_artifacts),
            patch(
                "gmaps_scraper.scraper.collect_browser_artifacts",
                return_value=browser_artifacts,
            ) as collect_browser_artifacts,
            patch(
                "gmaps_scraper.scraper.parse_saved_list_artifacts",
                side_effect=[ParseError("bad http payload"), parsed],
            ),
        ):
            artifacts, result = collect_saved_list_result("https://maps.app.goo.gl/example")

        self.assertIs(artifacts, browser_artifacts)
        self.assertIs(result, parsed)
        collect_browser_artifacts.assert_called_once()

    def test_collect_saved_list_result_uses_browser_only_mode(self) -> None:
        browser_artifacts = BrowserArtifacts(
            resolved_url="https://www.google.com/maps/@/data=!3m1!4b1",
            runtime_state=["browser-runtime"],
            script_texts=["browser-script"],
            html="<html></html>",
        )
        parsed = Mock()

        with (
            patch("gmaps_scraper.scraper.collect_http_artifacts") as collect_http_artifacts,
            patch(
                "gmaps_scraper.scraper.collect_browser_artifacts",
                return_value=browser_artifacts,
            ) as collect_browser_artifacts,
            patch("gmaps_scraper.scraper.parse_saved_list_artifacts", return_value=parsed),
        ):
            artifacts, result = collect_saved_list_result(
                "https://maps.app.goo.gl/example",
                collection_mode="browser",
            )

        self.assertIs(artifacts, browser_artifacts)
        self.assertIs(result, parsed)
        collect_http_artifacts.assert_not_called()
        collect_browser_artifacts.assert_called_once()

    def test_browser_only_mode_preserves_headless_setting(self) -> None:
        browser_artifacts = BrowserArtifacts(
            resolved_url="https://www.google.com/maps/@/data=!3m1!4b1",
            runtime_state=["browser-runtime"],
            script_texts=["browser-script"],
            html="<html></html>",
        )
        parsed = Mock()

        with (
            patch("gmaps_scraper.scraper.collect_http_artifacts") as collect_http_artifacts,
            patch(
                "gmaps_scraper.scraper.collect_browser_artifacts",
                return_value=browser_artifacts,
            ) as collect_browser_artifacts,
            patch("gmaps_scraper.scraper.parse_saved_list_artifacts", return_value=parsed),
        ):
            artifacts, result = collect_saved_list_result(
                "https://maps.app.goo.gl/example",
                headless=False,
                collection_mode="browser",
            )

        self.assertIs(artifacts, browser_artifacts)
        self.assertIs(result, parsed)
        collect_http_artifacts.assert_not_called()
        collect_browser_artifacts.assert_called_once_with(
            "https://maps.app.goo.gl/example",
            headless=False,
            timeout_ms=30_000,
            settle_time_ms=3_000,
        )

if __name__ == "__main__":
    unittest.main()
