"""Regression tests for Windows-safe forensic / generation console logging."""

from __future__ import annotations

import io
import logging
from unittest.mock import patch

from app.console_safe import (
    ConsoleSafeStreamHandler,
    _console_safe_text,
    _safe_console_print,
    configure_app_console_logging,
)


class _Cp1252Stdout(io.TextIOBase):
    """Stdout stand-in that rejects characters outside cp1252."""

    encoding = "cp1252"

    def __init__(self) -> None:
        self._buf = io.BytesIO()
        self._text = io.StringIO()

    @property
    def buffer(self) -> io.BytesIO:
        return self._buf

    def write(self, s: str) -> int:
        # Raise exactly as a real Windows console would for unsupported glyphs.
        data = s.encode(self.encoding)
        self._buf.write(data)
        return self._text.write(s)

    def flush(self) -> None:
        return None

    def getvalue(self) -> str:
        return self._buf.getvalue().decode("cp1252", errors="replace")


def test_console_safe_text_escapes_arrow_for_cp1252():
    fake = _Cp1252Stdout()
    with patch("app.console_safe.sys.stdout", fake):
        safe = _console_safe_text("PARSE_EXCEPTION \u2192 boom")
    assert "\u2192" not in safe
    assert "\\u2192" in safe
    safe.encode("cp1252")


def test_console_safe_text_escapes_leftright_arrow_u2194_for_cp1252():
    """U+2194 (↔) crashed the generation loop on Windows cp1252 consoles."""
    fake = _Cp1252Stdout()
    with patch("app.console_safe.sys.stdout", fake):
        safe = _console_safe_text(
            "[generation] prefix became parse-valid (EOS\u2194$END)"
        )
    assert "\u2194" not in safe
    assert "\\u2194" in safe
    safe.encode("cp1252")


def test_safe_console_print_does_not_raise_on_cp1252_arrow():
    fake = _Cp1252Stdout()
    with patch("app.console_safe.sys.stdout", fake):
        _safe_console_print(
            "[FORENSIC step   0]  partial='x'  -> PARSE_EXCEPTION \u2192 detail",
            flush=True,
        )
    out = fake.getvalue()
    assert "FORENSIC step" in out
    assert "\\u2192" in out
    assert "\u2192" not in out


def test_safe_console_print_does_not_raise_on_cp1252_leftright_arrow():
    fake = _Cp1252Stdout()
    msg = (
        "[generation] prefix became parse-valid at step 12 "
        "— next step is finalization (EOS\u2194$END)"
    )
    with patch("app.console_safe.sys.stdout", fake):
        _safe_console_print(msg, flush=True)
    out = fake.getvalue()
    assert "prefix became parse-valid" in out
    assert "\\u2194" in out
    assert "\u2194" not in out
    out.encode("cp1252")


def test_dynamic_token_and_exception_text_with_u2194_is_safe():
    fake = _Cp1252Stdout()
    dynamic_token = "tok\u2194end"
    exc_text = "UnexpectedToken: got \u2194 in remainder"
    with patch("app.console_safe.sys.stdout", fake):
        _safe_console_print(
            f"[VERIFY] selected={dynamic_token!r}  parse_exc={exc_text!r}",
            flush=True,
        )
    out = fake.getvalue()
    assert "\\u2194" in out
    assert "\u2194" not in out
    out.encode("cp1252")


def test_logging_failure_cannot_propagate_into_patched_mask_scores_path():
    """
    Even if diagnostic I/O raises, the failure must not escape the safe helper
    (mirrors the forensic / masking diagnostic path).
    """

    class _ExplodingBuffer:
        def write(self, _b: bytes) -> int:
            raise OSError("simulated console I/O failure")

        def flush(self) -> None:
            return None

    class _ExplodingStdout(io.TextIOBase):
        encoding = "cp1252"

        def __init__(self) -> None:
            self._buf = _ExplodingBuffer()

        @property
        def buffer(self) -> _ExplodingBuffer:
            return self._buf

        def write(self, s: str) -> int:
            raise UnicodeEncodeError(
                "cp1252", "\u2194", 0, 1, "ordinal not in range(128)"
            )

        def flush(self) -> None:
            return None

    exploding = _ExplodingStdout()
    with patch("app.console_safe.sys.stdout", exploding):
        try:
            _safe_console_print(f"  diagnosis with \u2194 and \u2192", flush=True)
            raised = False
        except Exception:
            raised = True
    assert raised is False


def test_console_safe_stream_handler_escapes_u2194():
    fake = _Cp1252Stdout()
    handler = ConsoleSafeStreamHandler(stream=fake)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = logging.getLogger("test.console_safe.handler")
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    try:
        logger.info("mask step note EOS\u2194$END token=\u2192")
    finally:
        logger.handlers.clear()
    out = fake.getvalue()
    assert "\\u2194" in out
    assert "\u2194" not in out


def test_forensic_arrow_labels_use_ascii_not_unicode():
    """Source-level guard: diagnosis labels written into the forensic path use ASCII."""
    import inspect

    from app.services import llm_service

    src = inspect.getsource(llm_service._SyncodeConstraint._install_forensic_patch)
    assert "PARSE_EXCEPTION ->" in src
    assert "PARSE_EXCEPTION →" not in src
    assert 'f"  -> {diagnosis}"' in src or "  -> {diagnosis}" in src
    assert "  → {diagnosis}" not in src


def test_generation_loop_has_no_literal_leftright_arrow():
    """Guard against reintroducing U+2194 into generation-loop diagnostics."""
    import inspect

    from app.services import llm_service

    src = inspect.getsource(llm_service.LLMService._run_generate_sync)
    assert "\u2194" not in src
    assert "↔" not in src
    assert "EOS<->$END" in src
    assert "print(" not in src.replace("_safe_console_print(", "")


def test_configure_app_console_logging_replaces_root_stream_handler():
    root = logging.getLogger()
    # Ensure there is a StreamHandler to replace.
    stream = _Cp1252Stdout()
    original = logging.StreamHandler(stream)
    root.addHandler(original)
    try:
        configure_app_console_logging()
        assert any(isinstance(h, ConsoleSafeStreamHandler) for h in root.handlers)
    finally:
        root.handlers = [
            h for h in root.handlers
            if h is not original and not isinstance(h, ConsoleSafeStreamHandler)
        ]
