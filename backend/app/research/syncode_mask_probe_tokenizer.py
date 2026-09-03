"""HuggingFace tokenizer candidate evidence (research-only)."""

from __future__ import annotations

from typing import Any, Optional

from app.models.syncode_mask_probe import TokenizerCandidateEvidence


def _safe_convert_ids_to_tokens(tokenizer: Any, token_id: int) -> Optional[str]:
    try:
        if hasattr(tokenizer, "convert_ids_to_tokens"):
            val = tokenizer.convert_ids_to_tokens(token_id)
            if isinstance(val, list):
                return None if not val else str(val[0])
            return None if val is None else str(val)
    except Exception:  # noqa: BLE001
        return None
    return None


def collect_tokenizer_candidate_evidence(
    tokenizer: Any,
    token_id: int,
    *,
    expected_decode: Optional[str] = None,
    original_trace_token_text: Optional[str] = None,
) -> TokenizerCandidateEvidence:
    warnings: list[str] = []
    raw_entry = _safe_convert_ids_to_tokens(tokenizer, token_id)

    decoded: Optional[str] = None
    try:
        # cleanup disabled — do not call clean_up_tokenization_spaces=True paths.
        kwargs = {"skip_special_tokens": False}
        # transformers >=4 supports clean_up_tokenization_spaces=
        try:
            decoded = tokenizer.decode(
                [token_id], clean_up_tokenization_spaces=False, **kwargs
            )
        except TypeError:
            decoded = tokenizer.decode([token_id], **kwargs)
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"decode failed: {type(exc).__name__}: {exc}")
        decoded = None

    codepoints = [ord(ch) for ch in decoded] if isinstance(decoded, str) else []
    utf8 = list(decoded.encode("utf-8")) if isinstance(decoded, str) else []
    utf8_hex = bytes(utf8).hex() if utf8 else ""

    roundtrip: list[int] = []
    roundtrip_ok: Optional[bool] = None
    if isinstance(decoded, str):
        try:
            enc = tokenizer.encode(decoded, add_special_tokens=False)
            roundtrip = [int(x) for x in list(enc)]
            roundtrip_ok = token_id in roundtrip and (
                roundtrip == [token_id] or len(roundtrip) >= 1
            )
            # Strict single-id reverse:
            if roundtrip == [token_id]:
                roundtrip_ok = True
            elif token_id not in roundtrip:
                roundtrip_ok = False
            else:
                roundtrip_ok = False
                warnings.append(
                    "encode(decode(id)) is not uniquely reversible to [id]; "
                    "reported as evidence, not automatically an error"
                )
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"round-trip encode failed: {type(exc).__name__}: {exc}")

    is_special: Optional[bool] = None
    try:
        specials = set(getattr(tokenizer, "all_special_ids", []) or [])
        is_special = token_id in specials
    except Exception:  # noqa: BLE001
        is_special = None

    if expected_decode is not None and decoded is not None and decoded != expected_decode:
        warnings.append(
            f"decode mismatch vs expected: got {decoded!r} expected {expected_decode!r}"
        )

    trace_eq: Optional[bool] = None
    if original_trace_token_text is not None and decoded is not None:
        trace_eq = original_trace_token_text == decoded

    return TokenizerCandidateEvidence(
        token_id=token_id,
        convert_ids_to_tokens=raw_entry,
        decode_cleanup_disabled=decoded,
        decode_repr=repr(decoded) if decoded is not None else None,
        unicode_codepoints=codepoints,
        utf8_bytes=utf8,
        utf8_hex=utf8_hex,
        encode_roundtrip_ids=roundtrip,
        roundtrip_returns_original_id=roundtrip_ok,
        is_special=is_special,
        original_trace_token_text=original_trace_token_text,
        trace_text_equals_decode=trace_eq,
        warnings=warnings,
    )
