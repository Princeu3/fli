"""Tests for the optional Chromium booking transport."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from fli.search import _browser
from fli.search.exceptions import SearchClientError, SearchTimeoutError


@dataclass
class BrowserCalls:
    """Record interactions made by the fake Playwright objects."""

    launch: dict = field(default_factory=dict)
    locale: str | None = None
    timeout: int | None = None
    url: str | None = None
    closed: bool = False


class FakeResponse:
    """Minimal successful Playwright response."""

    url = f"https://www.google.com/data/{_browser.BOOKING_RPC_PATH}"
    ok = True
    status = 200

    def body(self):
        """Return a recognizable RPC body."""
        return b"booking-response"


class FakePending:
    """Context manager returned by ``page.expect_response``."""

    value = FakeResponse()

    def __enter__(self):
        """Enter the expected-response context."""
        return self

    def __exit__(self, *_args):
        """Leave the expected-response context."""
        return False


class FakePage:
    """Minimal page used by the browser transport."""

    def __init__(self, calls: BrowserCalls):
        """Store the shared call recorder."""
        self.calls = calls

    def expect_response(self, predicate, *, timeout):
        assert predicate(FakeResponse())
        self.calls.timeout = timeout
        return FakePending()

    def goto(self, url, *, wait_until, timeout):
        assert wait_until == "domcontentloaded"
        assert timeout == self.calls.timeout
        self.calls.url = url


class FakeContext:
    """Minimal browser context."""

    def __init__(self, calls: BrowserCalls):
        """Store the shared call recorder."""
        self.calls = calls

    def new_page(self):
        return FakePage(self.calls)


class FakeBrowser:
    """Minimal Chromium browser."""

    def __init__(self, calls: BrowserCalls):
        """Store the shared call recorder."""
        self.calls = calls

    def new_context(self, *, locale):
        self.calls.locale = locale
        return FakeContext(self.calls)

    def close(self):
        self.calls.closed = True


class FakeChromium:
    """Minimal Chromium browser type."""

    def __init__(self, calls: BrowserCalls):
        """Store the shared call recorder."""
        self.calls = calls

    def launch(self, **kwargs):
        self.calls.launch = kwargs
        return FakeBrowser(self.calls)


class FakePlaywright:
    """Minimal Playwright object."""

    def __init__(self, calls: BrowserCalls):
        """Create the fake Chromium browser type."""
        self.chromium = FakeChromium(calls)


class FakePlaywrightManager:
    """Context manager matching ``sync_playwright()``."""

    def __init__(self, calls: BrowserCalls):
        """Create the fake Playwright context manager."""
        self.playwright = FakePlaywright(calls)

    def __enter__(self):
        """Return the fake Playwright object."""
        return self.playwright

    def __exit__(self, *_args):
        """Leave the fake Playwright context."""
        return False


def test_fetches_browser_signed_booking_response():
    calls = BrowserCalls()

    body = _browser.fetch_browser_booking_response(
        "https://www.google.com/travel/flights/booking?tfs=test",
        language="pt-BR",
        timeout_ms=12_345,
        _playwright_factory=lambda: FakePlaywrightManager(calls),
    )

    assert body == b"booking-response"
    assert calls.locale == "pt-BR"
    assert calls.timeout == 12_345
    assert calls.url.endswith("?tfs=test")
    assert calls.launch == {
        "headless": True,
        "args": ["--disable-dev-shm-usage", "--no-sandbox"],
    }
    assert calls.closed is True


def test_missing_browser_extra_has_actionable_error(monkeypatch):
    monkeypatch.setattr(_browser, "sync_playwright", None)

    with pytest.raises(SearchClientError, match=r"flights\[browser\]"):
        _browser.fetch_browser_booking_response("https://example.test")


def test_timeout_must_be_positive():
    with pytest.raises(ValueError, match="timeout_ms must be positive"):
        _browser.fetch_browser_booking_response(
            "https://example.test",
            timeout_ms=0,
            _playwright_factory=lambda: None,
        )


def test_playwright_timeout_is_mapped(monkeypatch):
    class TimeoutPage(FakePage):
        def goto(self, url, *, wait_until, timeout):
            raise _browser.PlaywrightTimeoutError("timed out")

    class TimeoutContext(FakeContext):
        def new_page(self):
            return TimeoutPage(self.calls)

    class TimeoutBrowser(FakeBrowser):
        def new_context(self, *, locale):
            self.calls.locale = locale
            return TimeoutContext(self.calls)

    class TimeoutChromium(FakeChromium):
        def launch(self, **kwargs):
            self.calls.launch = kwargs
            return TimeoutBrowser(self.calls)

    calls = BrowserCalls()
    manager = FakePlaywrightManager(calls)
    manager.playwright.chromium = TimeoutChromium(calls)

    with pytest.raises(SearchTimeoutError, match="Timed out"):
        _browser.fetch_browser_booking_response(
            "https://example.test",
            _playwright_factory=lambda: manager,
        )
    assert calls.closed is True
