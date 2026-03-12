from __future__ import annotations

import re
import unittest
from typing import Any

from google_saved_lists.scraper import (
    ScrapeError,
    _handle_google_consent,
    _has_google_consent_screen,
)


class _FakeLocator:
    def __init__(self, context: _FakeContext, pattern: re.Pattern[str]) -> None:
        self._context = context
        self._pattern = pattern
        self.first = self

    def click(self, timeout: int) -> None:
        del timeout
        for label in self._context.buttons:
            if self._pattern.search(label):
                self._context.clicked.append(label)
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
    ) -> None:
        self._text = text
        self.buttons = buttons or []
        self.url = url
        self.frames = frames or []
        self.clicked: list[str] = []
        self.timeouts: list[int] = []

    def evaluate(self, script: str) -> Any:
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


class ScraperConsentTests(unittest.TestCase):
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

    def test_raises_when_reject_button_is_missing(self) -> None:
        page = _FakeContext(
            text="Google\nPrima di continuare su Google\nAccetta tutto",
            buttons=["Accetta tutto", "Altre opzioni"],
        )

        with self.assertRaises(ScrapeError):
            _handle_google_consent(page, timeout_ms=5_000)


if __name__ == "__main__":
    unittest.main()
