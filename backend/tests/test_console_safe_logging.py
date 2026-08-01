"""Regression tests for Windows-safe forensic console logging."""

from __future__ import annotations

import io
from unittest.mock import patch

from app.services.llm_service import _console_safe_text, _safe_console_print


class _Cp1252Stdout(io.TextIOBase):
    """Stdout stand-in that rejects characters outside cp1252 (like U+2192)."""

    encoding = "cp1252"

    def __init__(self) -> None:
        self.buffer = io.StringIO()

    def write(self, s: str) -> int:
        # Raise exactly as a real Windows console would for unsupported glyphs.
        s.encode(self.encoding)
        return self.buffer.write(s)

    def flush(self) -> None:
        return None

    def getvalue(self) -> str:
        return self.buffer.getvalue()


def test_console_safe_text_escapes_arrow_for_cp1252():
    fake = _Cp1252Stdout()
    with patch("app.services.llm_service.sys.stdout", fake):
        safe = _console_safe_text("PARSE_EXCEPTION \u2192 boom")
    assert "\u2192" not in safe
    assert "\\u2192" in safe
    # Must be encodable as cp1252 after sanitisation.
    safe.encode("cp1252")


def test_safe_console_print_does_not_raise_on_cp1252_arrow():
    fake = _Cp1252Stdout()
    with patch("app.services.llm_service.sys.stdout", fake):
        _safe_console_print(
            "[FORENSIC step   0]  partial='x'  -> PARSE_EXCEPTION \u2192 detail",
            flush=True,
        )
    out = fake.getvalue()
    assert "FORENSIC step" in out
    assert "\\u2192" in out
    assert "\u2192" not in out


def test_logging_failure_cannot_propagate_into_patched_mask_scores_path():
    """
    Simulate the bootstrap failure path: a diagnostic print that would raise
    UnicodeEncodeError must not escape from the forensic print site.
    """
    class _ExplodingStdout(_Cp1252Stdout):
        def write(self, s: str) -> int:
            # Force a hard failure even after encoding — helper must swallow it.
            raise UnicodeEncodeError("cp1252", "\u2192", 0, 1, "ordinal not in range(128)")

    exploding = _ExplodingStdout()
    with patch("app.services.llm_service.sys.stdout", exploding):
        # Must not raise — mirrors the try/except around forensic diagnostic print.
        try:
            _safe_console_print(f"  \u2192 diagnosis with arrow", flush=True)
            raised = False
        except Exception:
            raised = True
    assert raised is False


def test_forensic_arrow_labels_use_ascii_not_unicode():
    """Source-level guard: diagnosis labels written into the forensic path use ASCII."""
    import inspect

    from app.services import llm_service

    src = inspect.getsource(llm_service._SyncodeConstraint._install_forensic_patch)
    assert "PARSE_EXCEPTION ->" in src
    assert "PARSE_EXCEPTION →" not in src
    assert 'f"  -> {diagnosis}"' in src or "  -> {diagnosis}" in src
    assert "  → {diagnosis}" not in src
