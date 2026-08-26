r"""Parsing helpers for Google Flights' FlightsFrontendService wire format.

The Service returns JSONP-flavoured responses of the form::

    )]}'\n\n
    <chunk1_byte_len>\n
    [["wrb.fr", null, "<inner JSON string>"]]
    <chunk2_byte_len>\n
    [["wrb.fr", null, "<inner JSON string>"]]
    ...

`GetShoppingResults` and `GetCalendarGraph` happen to emit a single chunk so
the legacy parsers in this package could get away with `lstrip(")]}'")`.
`GetBookingResults` emits two chunks, so we need a proper multi-chunk reader.

The length headers have varied between UTF-8 bytes and character-oriented
counts across transports. The reader therefore treats them as framing markers
and lets ``JSONDecoder.raw_decode`` find each payload's exact boundary.

This module centralises that reader and exposes :func:`iter_wrb_chunks` which
yields the decoded inner JSON of each ``wrb.fr`` chunk.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from typing import Any

logger = logging.getLogger(__name__)

_PREFIX = ")]}'"


def iter_wrb_chunks(body: str | bytes) -> Iterator[Any]:
    """Yield the inner JSON object of every ``wrb.fr`` chunk in ``body``.

    Robust to single-chunk responses with no length headers (the older
    ``GetShoppingResults`` / ``GetCalendarGraph`` shape) — those are parsed
    by falling back to a single JSON load over the trimmed body.
    """
    try:
        text = body.decode("utf-8") if isinstance(body, bytes) else body
    except UnicodeDecodeError:
        logger.warning("Failed to decode wrb.fr body as UTF-8", exc_info=True)
        return

    text = text.lstrip()
    if text.startswith(_PREFIX):
        text = text[len(_PREFIX) :]
    text = text.lstrip()

    if not text:
        return

    # Fast path: no length headers (legacy single-chunk responses).
    if not text[:1].isdigit():
        try:
            outer = json.loads(text)
        except (ValueError, json.JSONDecodeError):
            logger.warning("Failed to decode single-chunk wrb.fr body as JSON", exc_info=True)
            return
        yield from _chunks_from_outer(outer)
        return

    decoder = json.JSONDecoder()
    cursor = 0
    while cursor < len(text):
        while cursor < len(text) and text[cursor].isspace():
            cursor += 1
        if cursor >= len(text):
            break

        # Read the decimal length prefix terminated by \n.
        end = text.find("\n", cursor)
        if end == -1:
            break
        try:
            int(text[cursor:end].strip())
        except ValueError:
            logger.warning(
                "Malformed length header at offset %d; truncating chunk stream",
                cursor,
            )
            break
        cursor = end + 1
        while cursor < len(text) and text[cursor].isspace():
            cursor += 1
        try:
            outer, cursor = decoder.raw_decode(text, cursor)
        except (ValueError, json.JSONDecodeError):
            logger.warning("Discarding malformed wrb.fr chunk", exc_info=True)
            break
        yield from _chunks_from_outer(outer)


def _chunks_from_outer(outer: Any) -> Iterator[Any]:
    """Walk a top-level chunk list and yield decoded inner-JSON payloads."""
    if not isinstance(outer, list):
        return
    for row in outer:
        if not isinstance(row, list) or len(row) < 3:
            continue
        if row[0] != "wrb.fr":
            continue
        inner = row[2]
        if not isinstance(inner, str) or not inner:
            # Payload-less row: Google declined the call and parked an error
            # code in slot 5. Raise instead of yielding nothing, so callers
            # don't report a hard block as "no flights on this route".
            code = row[5][0] if len(row) > 5 and isinstance(row[5], list) and row[5] else None
            if isinstance(code, int):
                from fli.search.exceptions import SearchRejectedError

                raise SearchRejectedError(code)
            continue
        try:
            yield json.loads(inner)
        except (ValueError, json.JSONDecodeError):
            logger.warning("Failed to decode wrb.fr inner JSON payload", exc_info=True)
            continue


def parse_first_wrb_payload(body: str | bytes) -> Any:
    """Return the inner JSON of the first ``wrb.fr`` chunk, or None."""
    for chunk in iter_wrb_chunks(body):
        return chunk
    return None
