from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from google_saved_lists.scraper import (
    BrowserSessionConfig,
    ScrapeError,
    _handle_google_consent,
    _has_google_consent_screen,
    _launch_browser_context,
    _read_resolved_url,
    collect_browser_artifacts,
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
        runtime_state: Any = None,
        script_texts: list[str] | None = None,
    ) -> None:
        self._text = text
        self.buttons = buttons or []
        self.url = url
        self.frames = frames or []
        self.allow_role_click = allow_role_click
        self.dismiss_on_click = dismiss_on_click
        self.runtime_state = runtime_state
        self.script_texts = script_texts or []
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
        if "APP_INITIALIZATION_STATE" in script:
            return self.runtime_state
        if "document.scripts" in script:
            return self.script_texts
        return None

    def get_by_role(self, role: str, name: re.Pattern[str]) -> _FakeLocator:
        if role != "button":
            raise RuntimeError("Unexpected role")
        return _FakeLocator(self, name)

    def goto(self, url: str, wait_until: str, timeout: int) -> None:
        del wait_until, timeout
        if not self.url:
            self.url = url

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


class _FakeBrowserContext:
    def __init__(self, page: _FakeContext) -> None:
        self._page = page
        self.closed = False

    def new_page(self) -> _FakeContext:
        return self._page

    def close(self) -> None:
        self.closed = True


class ScraperConsentTests(unittest.TestCase):
    def test_launches_ephemeral_context_without_profile_dir(self) -> None:
        launched: list[dict[str, Any]] = []
        expected_context = object()

        def fake_launch_context(**kwargs: Any) -> object:
            launched.append(kwargs)
            return expected_context

        fake_module = SimpleNamespace(
            launch_context=fake_launch_context,
            launch_persistent_context=lambda *_args, **_kwargs: None,
        )

        with patch.dict("sys.modules", {"cloakbrowser": fake_module}):
            context = _launch_browser_context(
                headless=False,
                browser_session=BrowserSessionConfig(),
            )

        self.assertIs(context, expected_context)
        self.assertEqual(
            launched,
            [
                {
                    "headless": False,
                    "humanize": True,
                }
            ],
        )

    def test_launches_persistent_context_with_profile_dir_and_proxy(self) -> None:
        launched: list[tuple[Path, dict[str, Any]]] = []
        expected_context = object()

        def fake_launch_persistent_context(
            profile_dir: Path,
            **kwargs: Any,
        ) -> object:
            launched.append((profile_dir, kwargs))
            return expected_context

        fake_module = SimpleNamespace(
            launch_context=lambda **_kwargs: None,
            launch_persistent_context=fake_launch_persistent_context,
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            profile_dir = Path(tmp_dir) / "session"
            with patch.dict("sys.modules", {"cloakbrowser": fake_module}):
                context = _launch_browser_context(
                    headless=True,
                    browser_session=BrowserSessionConfig(
                        profile_dir=profile_dir,
                        proxy="http://proxy.example:8080",
                    ),
                )
            self.assertTrue(profile_dir.is_dir())

        self.assertIs(context, expected_context)
        self.assertEqual(
            launched,
            [
                (
                    profile_dir,
                    {
                        "headless": True,
                        "humanize": True,
                        "proxy": "http://proxy.example:8080",
                    },
                )
            ],
        )

    def test_collect_browser_artifacts_closes_context(self) -> None:
        page = _FakeContext(
            text="",
            url="https://www.google.com/maps/@30.5370705,125.4120472,6z/data=!4m3!11m2!2sUGEPbA20Qd-OH4uoWjmDgQ!3e3?entry=ttu",
            runtime_state=["runtime"],
            script_texts=["script"],
        )
        context = _FakeBrowserContext(page)

        with patch("google_saved_lists.scraper._launch_browser_context", return_value=context):
            artifacts = collect_browser_artifacts(
                "https://maps.app.goo.gl/MG2Vd5pWBkL7hXL18",
                headless=True,
                timeout_ms=5_000,
                settle_time_ms=0,
            )

        self.assertTrue(context.closed)
        self.assertEqual(
            artifacts.resolved_url,
            "https://www.google.com/maps/@30.5370705,125.4120472,6z/data=!4m3!11m2!2sUGEPbA20Qd-OH4uoWjmDgQ!3e3?entry=ttu",
        )
        self.assertEqual(artifacts.runtime_state, ["runtime"])
        self.assertEqual(artifacts.script_texts, ["script"])

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

        with patch("google_saved_lists.scraper._capture_consent_diagnostics", return_value=[]):
            with self.assertRaises(ScrapeError):
                _handle_google_consent(page, timeout_ms=5_000)


if __name__ == "__main__":
    unittest.main()
