"""UTF-8 safe logging utilities.

On Windows, ``sys.stdout`` defaults to the ``cp1252`` encoding, which
cannot encode characters like ``\\ufeff`` (BOM), ``\\u2026`` (ellipsis),
or emoji. When a job title contains such characters and is logged via
``logging.StreamHandler(sys.stdout)``, the handler raises
``UnicodeEncodeError`` and the log line is lost (or worse, the process
crashes).

This module provides :func:`get_utf8_stream_handler` which wraps
``sys.stdout`` (or ``sys.stderr``) in a writer that reconfigures the
stream to UTF-8 with ``errors="replace"`` so that any character can be
logged without crashing. On Python 3.7+ this uses
``stream.reconfigure(encoding="utf-8", errors="replace")``; on older
versions it falls back to a wrapper.

Usage::

    from utils.utf8_logging import get_utf8_stream_handler
    logging.basicConfig(handlers=[get_utf8_stream_handler()], ...)
"""

from __future__ import annotations

import io
import sys
from typing import Any


def _reconfigure_stream(stream: Any) -> Any:
    """Reconfigure ``stream`` to use UTF-8 with errors='replace'.

    If the stream cannot be reconfigured (e.g. it's a redirect to a
    pipe that doesn't support reconfigure), wrap it in a UTF-8 writer.
    """
    if stream is None:
        return sys.stdout

    # Python 3.7+ has reconfigure on TextIOBase.
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is not None:
        try:
            reconfigure(encoding="utf-8", errors="replace")
            return stream
        except (ValueError, OSError, LookupError):
            # Some streams (e.g. pytest caps) don't allow reconfigure.
            pass

    # Fallback: wrap in a UTF-8 writer.
    underlying = getattr(stream, "buffer", None)
    if underlying is not None:
        return io.TextIOWrapper(underlying, encoding="utf-8", errors="replace", line_buffering=True)
    return stream


def get_utf8_stream_handler(stream: Any = None) -> "logging.StreamHandler":  # noqa: F821
    """Return a :class:`logging.StreamHandler` that safely writes UTF-8.

    Args:
        stream: The stream to wrap (default: ``sys.stdout``).

    Returns:
        A StreamHandler whose stream has been reconfigured to UTF-8
        with ``errors="replace"``, so any character can be logged
        without raising ``UnicodeEncodeError``.
    """
    import logging

    target = stream if stream is not None else sys.stdout
    safe_stream = _reconfigure_stream(target)
    return logging.StreamHandler(safe_stream)


def safe_log(msg: str, *args: Any) -> str:
    """Sanitize ``msg`` for safe logging on any console.

    Replaces characters that cannot be encoded in the current stdout
    encoding with ``?``. Use this as a last-resort escape hatch if you
    cannot use :func:`get_utf8_stream_handler` (e.g. in a library that
    must not reconfigure global stream state).

    For new code, prefer :func:`get_utf8_stream_handler` at the
    logging.basicConfig level.
    """
    encoding = getattr(sys.stdout, "encoding", "utf-8") or "utf-8"
    try:
        msg.encode(encoding)
        return msg
    except UnicodeEncodeError:
        return msg.encode(encoding, errors="replace").decode(encoding, errors="replace")


__all__ = ["get_utf8_stream_handler", "safe_log"]
