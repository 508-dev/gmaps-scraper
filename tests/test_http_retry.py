from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from typing import Any
from unittest.mock import patch

from curl_cffi.requests import exceptions as curl_exceptions

from gmaps_scraper.place_scraper import _collect_preview_place_enrichment
from gmaps_scraper.scraper import (
    ScrapeError,
    _get_with_retry,
    _parse_retry_after_seconds,
    collect_http_artifacts,
)


class _HTTPStatusError(Exception):
    pass


class _Response:
    def __init__(
        self,
        *,
        status_code: int = 200,
        text: str = "",
        url: str = "",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self.text = text
        self.url = url
        self.headers = headers or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise _HTTPStatusError(f"HTTP {self.status_code}")


class _Session:
    """Replays scripted outcomes: a response is returned, an exception is raised."""

    def __init__(self, outcomes: list[_Response | Exception], **kwargs: Any) -> None:
        self._outcomes = outcomes
        self.kwargs = kwargs
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def __enter__(self) -> _Session:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        del exc_type, exc, tb

    def get(self, url: str, **kwargs: Any) -> _Response:
        self.calls.append((url, kwargs))
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class _CurlRequests:
    def __init__(self, outcomes: list[_Response | Exception]) -> None:
        self._outcomes = outcomes
        self.sessions: list[_Session] = []

    def Session(self, **kwargs: Any) -> _Session:
        session = _Session(self._outcomes, **kwargs)
        self.sessions.append(session)
        return session


_LIST_PAGE_HTML = (
    "<html><head>"
    '<link href="/maps/preview/entitylist/getlist?pb=123" as="fetch" rel="preload">'
    "</head></html>"
)
_PLACE_PAGE_HTML = (
    '<html><head><link href="/maps/preview/place?pb=456" as="fetch" rel="preload"></head></html>'
)


class GetWithRetryTests(unittest.TestCase):
    def setUp(self) -> None:
        sleep_patcher = patch("gmaps_scraper.scraper.time.sleep")
        self.sleep = sleep_patcher.start()
        self.addCleanup(sleep_patcher.stop)

    def test_success_is_returned_without_retrying(self) -> None:
        ok = _Response(status_code=200)
        session = _Session([ok])

        self.assertIs(_get_with_retry(session, "https://example.test/a", referer="r"), ok)

        self.assertEqual(session.calls, [("https://example.test/a", {"referer": "r"})])
        self.sleep.assert_not_called()

    def test_non_retryable_error_status_is_returned_without_retrying(self) -> None:
        not_found = _Response(status_code=404)
        session = _Session([not_found])

        self.assertIs(_get_with_retry(session, "https://example.test/a"), not_found)

        self.assertEqual(len(session.calls), 1)
        self.sleep.assert_not_called()

    def test_transient_status_is_retried_with_exponential_backoff_until_success(
        self,
    ) -> None:
        ok = _Response(status_code=200)
        session = _Session([_Response(status_code=503), _Response(status_code=502), ok])

        with patch(
            "gmaps_scraper.scraper.random.uniform",
            side_effect=lambda low, high: high,
        ) as uniform:
            self.assertIs(_get_with_retry(session, "https://example.test/a"), ok)

        self.assertEqual(len(session.calls), 3)
        self.assertEqual([call.args[0] for call in self.sleep.call_args_list], [1.0, 2.0])
        self.assertEqual([call.args for call in uniform.call_args_list], [(0.5, 1.0), (1.0, 2.0)])

    def test_retry_after_seconds_overrides_backoff(self) -> None:
        session = _Session([_Response(status_code=429, headers={"Retry-After": "7"}), _Response()])

        _get_with_retry(session, "https://example.test/a")

        self.sleep.assert_called_once_with(7.0)

    def test_retry_after_at_the_cap_is_honored(self) -> None:
        session = _Session([_Response(status_code=503, headers={"Retry-After": "30"}), _Response()])

        _get_with_retry(session, "https://example.test/a")

        self.sleep.assert_called_once_with(30.0)

    def test_retry_after_beyond_the_cap_gives_up_immediately(self) -> None:
        blocked = _Response(status_code=429, headers={"Retry-After": "120"})
        session = _Session([blocked])

        self.assertIs(_get_with_retry(session, "https://example.test/a"), blocked)

        self.assertEqual(len(session.calls), 1)
        self.sleep.assert_not_called()

    def test_transient_transport_errors_are_retried(self) -> None:
        for error in (
            curl_exceptions.ConnectionError("connection reset"),
            curl_exceptions.Timeout("operation timed out"),
            curl_exceptions.DNSError("could not resolve host"),
            curl_exceptions.SSLError("tls handshake failed"),
        ):
            with self.subTest(error=type(error).__name__):
                self.sleep.reset_mock()
                ok = _Response()
                session = _Session([error, ok])

                self.assertIs(_get_with_retry(session, "https://example.test/a"), ok)

                self.assertEqual(len(session.calls), 2)
                self.sleep.assert_called_once()

    def test_permanent_errors_are_raised_without_retrying(self) -> None:
        for error in (
            curl_exceptions.InvalidURL("bad url"),
            curl_exceptions.CertificateVerifyError("self-signed certificate"),
            curl_exceptions.TooManyRedirects("redirect loop"),
            curl_exceptions.ProxyError("proxy auth failed"),
            TypeError("unexpected keyword argument"),
        ):
            with self.subTest(error=type(error).__name__):
                session = _Session([error, _Response()])

                with self.assertRaises(type(error)):
                    _get_with_retry(session, "https://example.test/a")

                self.assertEqual(len(session.calls), 1)
                self.sleep.assert_not_called()

    def test_transport_error_is_raised_after_last_attempt(self) -> None:
        session = _Session(
            [
                curl_exceptions.Timeout("t1"),
                curl_exceptions.Timeout("t2"),
                curl_exceptions.Timeout("t3"),
            ]
        )

        with self.assertRaisesRegex(curl_exceptions.Timeout, "t3"):
            _get_with_retry(session, "https://example.test/a")

        self.assertEqual(len(session.calls), 3)

    def test_last_transient_response_is_returned_after_retries_are_exhausted(self) -> None:
        last = _Response(status_code=503)
        session = _Session([_Response(status_code=503), _Response(status_code=503), last])

        self.assertIs(_get_with_retry(session, "https://example.test/a"), last)

        self.assertEqual(len(session.calls), 3)
        self.assertEqual(self.sleep.call_count, 2)


class ParseRetryAfterTests(unittest.TestCase):
    def test_http_date_in_the_future(self) -> None:
        retry_at = datetime.now(UTC) + timedelta(seconds=20)
        response = _Response(headers={"Retry-After": format_datetime(retry_at, usegmt=True)})

        seconds = _parse_retry_after_seconds(response)

        assert seconds is not None
        self.assertTrue(15 <= seconds <= 20, seconds)

    def test_http_date_in_the_past_means_retry_now(self) -> None:
        response = _Response(headers={"Retry-After": "Wed, 21 Oct 2015 07:28:00 GMT"})

        self.assertEqual(_parse_retry_after_seconds(response), 0.0)

    def test_invalid_values_are_ignored(self) -> None:
        for value in ("", "soon", "-5", "1.5", "nan", "\u00b2"):
            with self.subTest(value=value):
                response = _Response(headers={"Retry-After": value})
                self.assertIsNone(_parse_retry_after_seconds(response))

    def test_missing_header_is_ignored(self) -> None:
        self.assertIsNone(_parse_retry_after_seconds(_Response()))


class CollectorRetryTests(unittest.TestCase):
    def setUp(self) -> None:
        sleep_patcher = patch("gmaps_scraper.scraper.time.sleep")
        self.sleep = sleep_patcher.start()
        self.addCleanup(sleep_patcher.stop)

    def test_saved_list_page_recovers_from_rate_limit(self) -> None:
        curl = _CurlRequests(
            [
                _Response(status_code=429, headers={"Retry-After": "1"}),
                _Response(text=_LIST_PAGE_HTML, url="https://www.google.com/maps/@/data"),
                _Response(text=')]}\'\n[["payload"]]'),
            ]
        )

        with patch("gmaps_scraper.scraper._import_curl_requests", return_value=curl):
            artifacts = collect_http_artifacts(
                "https://maps.app.goo.gl/example",
                timeout_ms=15_000,
            )

        self.assertEqual(artifacts.script_texts, [')]}\'\n[["payload"]]'])
        calls = curl.sessions[0].calls
        self.assertEqual([url for url, _ in calls[:2]], ["https://maps.app.goo.gl/example"] * 2)
        self.assertIn("entitylist/getlist", calls[2][0])
        self.sleep.assert_called_once_with(1.0)

    def test_saved_list_preload_is_retried_with_its_referer(self) -> None:
        curl = _CurlRequests(
            [
                _Response(text=_LIST_PAGE_HTML, url="https://www.google.com/maps/@/data"),
                _Response(status_code=500),
                _Response(text=')]}\'\n[["payload"]]'),
            ]
        )

        with patch("gmaps_scraper.scraper._import_curl_requests", return_value=curl):
            artifacts = collect_http_artifacts(
                "https://maps.app.goo.gl/example",
                timeout_ms=15_000,
            )

        self.assertEqual(artifacts.script_texts, [')]}\'\n[["payload"]]'])
        preload_calls = curl.sessions[0].calls[1:]
        self.assertEqual(len(preload_calls), 2)
        for _, kwargs in preload_calls:
            self.assertEqual(kwargs, {"referer": "https://www.google.com/maps/@/data"})

    def test_saved_list_page_still_fails_loudly_after_retries(self) -> None:
        curl = _CurlRequests([_Response(status_code=503) for _ in range(3)])

        with (
            patch("gmaps_scraper.scraper._import_curl_requests", return_value=curl),
            self.assertRaisesRegex(ScrapeError, "HTTP 503"),
        ):
            collect_http_artifacts("https://maps.app.goo.gl/example", timeout_ms=15_000)

        self.assertEqual(len(curl.sessions[0].calls), 3)

    def test_place_preview_enrichment_recovers_from_transient_failures(self) -> None:
        curl = _CurlRequests(
            [
                curl_exceptions.ConnectionError("connection reset"),
                _Response(text=_PLACE_PAGE_HTML, url="https://www.google.com/maps/place/x"),
                _Response(status_code=503),
                _Response(text="preview-payload"),
            ]
        )

        with (
            patch("gmaps_scraper.place_scraper._import_curl_requests", return_value=curl),
            patch(
                "gmaps_scraper.place_scraper._extract_preview_place_enrichment",
                side_effect=lambda text: {"payload_text": text},
            ),
        ):
            enrichment = _collect_preview_place_enrichment(
                "https://maps.app.goo.gl/place",
                resolved_url="https://www.google.com/maps/place/x",
                timeout_ms=15_000,
            )

        self.assertEqual(enrichment, {"payload_text": "preview-payload"})
        self.assertEqual(len(curl.sessions[0].calls), 4)


if __name__ == "__main__":
    unittest.main()
