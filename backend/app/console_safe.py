"""
Windows-safe diagnostic console output.

cp1252 (and other narrow console encodings) cannot emit many Unicode glyphs
used in research diagnostics (e.g. U+2192 "→", U+2194 "↔", U+2014 "—").
Bare ``print()`` / ``logging.StreamHandler`` then raise ``UnicodeEncodeError``
and can abort generation if the exception escapes into the SynCode path.

These helpers escape unsupported characters with ``backslashreplace`` so the
message remains identifiable (e.g. ``\\u2194``) without crashing.
"""

from __future__ import annotations

import logging
import sys


def console_safe_text(text: str) -> str:
    """
    Return *text* encoded for the active stdout encoding.

    Unsupported characters become backslash escapes such as ``\\u2194``.
    """
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        return text.encode(encoding, errors="backslashreplace").decode(
            encoding, errors="replace"
        )
    except Exception:
        return text.encode("ascii", errors="backslashreplace").decode("ascii")


def safe_console_print(
    *args: object,
    sep: str = " ",
    end: str = "\n",
    flush: bool = False,
) -> None:
    """
    Observational console print that never propagates encoding / I/O failures.

    Prefer writing through ``stdout.buffer`` with an explicit encode so TextIO
    wrappers cannot re-raise ``UnicodeEncodeError`` on Windows.
    """
    try:
        msg = console_safe_text(sep.join(str(a) for a in args) + end)
        out = sys.stdout
        encoding = getattr(out, "encoding", None) or "utf-8"
        buf = getattr(out, "buffer", None)
        if buf is not None:
            buf.write(msg.encode(encoding, errors="backslashreplace"))
            if flush:
                buf.flush()
        else:
            out.write(msg)
            if flush:
                out.flush()
    except Exception:
        # Diagnostic output must never affect masking / generation.
        pass


# Back-compat aliases used by llm_service / tests.
_console_safe_text = console_safe_text
_safe_console_print = safe_console_print


class ConsoleSafeStreamHandler(logging.StreamHandler):
    """
    Project-owned logging handler that escapes unsupported console glyphs.

    Does not monkey-patch ``sys.stdout``; only records emitted through this
    handler are sanitised.
    """

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = console_safe_text(self.format(record) + self.terminator)
            stream = self.stream
            encoding = getattr(stream, "encoding", None) or "utf-8"
            buf = getattr(stream, "buffer", None)
            if buf is not None:
                buf.write(msg.encode(encoding, errors="backslashreplace"))
                self.flush()
            else:
                stream.write(msg)
                self.flush()
        except Exception:
            self.handleError(record)


def configure_app_console_logging() -> None:
    """
    Replace root console ``StreamHandler`` sinks with
    ``ConsoleSafeStreamHandler``, preserving level and formatter.

    Skips ``FileHandler`` (and subclasses).  Idempotent.  Does not
    monkey-patch ``sys.stdout``.
    """
    root = logging.getLogger()
    for index, handler in enumerate(list(root.handlers)):
        if isinstance(handler, ConsoleSafeStreamHandler):
            continue
        if not isinstance(handler, logging.StreamHandler):
            continue
        # FileHandler is a StreamHandler subclass — leave disk sinks alone.
        if isinstance(handler, logging.FileHandler):
            continue
        stream = getattr(handler, "stream", None)
        safe = ConsoleSafeStreamHandler(stream=stream)
        safe.setLevel(handler.level)
        if handler.formatter is not None:
            safe.setFormatter(handler.formatter)
        root.handlers[index] = safe
