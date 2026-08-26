"""Optional browser transport for Google Flights booking results.

Google's booking page signs ``GetBookingResults`` in its own JavaScript.  The
plain HTTP transport remains the fast default, while this module lets callers
opt into a real Chromium session when Google rejects that direct RPC.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fli.search.exceptions import SearchClientError, SearchHTTPError, SearchTimeoutError

try:
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright
except ImportError:  # pragma: no cover - exercised by installations without the extra
    PlaywrightTimeoutError = TimeoutError
    sync_playwright = None


BOOKING_RPC_PATH = "FlightsFrontendService/GetBookingResults"
DEFAULT_BROWSER_TIMEOUT_MS = 30_000


def fetch_browser_booking_response(
    url: str,
    *,
    language: str | None = None,
    timeout_ms: int = DEFAULT_BROWSER_TIMEOUT_MS,
    _playwright_factory: Callable[[], Any] | None = None,
) -> bytes:
    """Return the browser-signed ``GetBookingResults`` response body.

    The browser is deliberately short-lived.  This keeps cookies and opaque
    Google session values isolated to one itinerary and avoids leaking them to
    callers or logs.
    """
    factory = _playwright_factory or sync_playwright
    if factory is None:
        raise SearchClientError(
            "Browser booking transport is not installed. Install flights[browser] "
            "and the Chromium browser binary."
        )
    if timeout_ms <= 0:
        raise ValueError("timeout_ms must be positive")

    try:
        with factory() as playwright:
            browser = playwright.chromium.launch(
                headless=True,
                args=["--disable-dev-shm-usage", "--no-sandbox"],
            )
            try:
                context = browser.new_context(locale=language or "en-US")
                page = context.new_page()
                with page.expect_response(
                    lambda response: BOOKING_RPC_PATH in response.url,
                    timeout=timeout_ms,
                ) as pending:
                    page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)

                response = pending.value
                if not response.ok:
                    raise SearchHTTPError(
                        "Google Flights returned an error response for browser booking results.",
                        status_code=response.status,
                    )
                return response.body()
            finally:
                browser.close()
    except PlaywrightTimeoutError as error:
        raise SearchTimeoutError(
            "Timed out waiting for Google Flights booking options in Chromium."
        ) from error
    except SearchClientError:
        raise
    except Exception as error:
        raise SearchClientError(
            f"Browser booking transport failed: {error.__class__.__name__}"
        ) from error
