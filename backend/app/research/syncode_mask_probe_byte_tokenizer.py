"""SynCode ByteTokenizer evidence for candidates (research-only)."""

from __future__ import annotations

from typing import Any, Optional

from app.models.syncode_mask_probe import ByteTokenizerCandidateEvidence


def collect_byte_tokenizer_evidence(
    byte_tokenizer: Any,
    token_id: int,
    *,
    hf_decoded_text: Optional[str] = None,
    raw_vocab_entry: Optional[str] = None,
) -> ByteTokenizerCandidateEvidence:
    warnings: list[str] = []
    vocab_type = None
    try:
        vt = getattr(byte_tokenizer, "vocab_type", None)
        vocab_type = getattr(vt, "name", None) or (str(vt) if vt is not None else None)
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"vocab_type unavailable: {exc}")

    method = "syncode.mask_store.byte_tokenizer.ByteTokenizer.decode([id])"
    syncode_bytes: Optional[bytes] = None
    try:
        syncode_bytes = byte_tokenizer.decode([token_id], skip_special_tokens=False)
        if not isinstance(syncode_bytes, (bytes, bytearray)):
            warnings.append(
                f"ByteTokenizer.decode returned {type(syncode_bytes).__name__}, "
                "expected bytes"
            )
            syncode_bytes = None
        else:
            syncode_bytes = bytes(syncode_bytes)
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"ByteTokenizer.decode failed: {type(exc).__name__}: {exc}")

    # Also try vocab_byte table when present.
    if syncode_bytes is None:
        try:
            vb = getattr(byte_tokenizer, "vocab_byte", None) or {}
            if token_id in vb:
                syncode_bytes = bytes(vb[token_id])
                method = "ByteTokenizer.vocab_byte[token_id]"
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"vocab_byte lookup failed: {exc}")

    eq_status = "UNAVAILABLE"
    eq_detail = ""
    matches: Optional[bool] = None
    if syncode_bytes is None:
        eq_detail = "SynCode byte sequence unavailable"
    elif hf_decoded_text is None:
        eq_detail = (
            "HF decode unavailable; cannot compare ByteTokenizer bytes to "
            "decoded character UTF-8 without fabricating a conversion"
        )
    else:
        try:
            hf_utf8 = hf_decoded_text.encode("utf-8")
            matches = syncode_bytes == hf_utf8
            if matches:
                eq_status = "VERIFIED"
                eq_detail = "ByteTokenizer bytes equal UTF-8 of HF decode"
            else:
                # For BYTE_LEVEL tokenizers this may still be intentional adapter mapping.
                vt_name = (vocab_type or "").upper()
                if "BYTE_LEVEL" in vt_name or "BYTE_FALLBACK" in vt_name:
                    eq_status = "INFERENCE"
                    eq_detail = (
                        "ByteTokenizer bytes differ from HF UTF-8; for "
                        f"vocab_type={vocab_type} this may be adapter mapping, "
                        "not proof of a bug"
                    )
                else:
                    eq_status = "CONTRADICTED"
                    eq_detail = (
                        "ByteTokenizer bytes differ from HF UTF-8 for RAW-like vocab"
                    )
        except Exception as exc:  # noqa: BLE001
            eq_detail = f"equivalence comparison failed: {exc}"

    seq = list(syncode_bytes) if syncode_bytes is not None else None
    return ByteTokenizerCandidateEvidence(
        token_id=token_id,
        vocab_type=vocab_type,
        raw_vocab_entry=raw_vocab_entry,
        syncode_byte_sequence=seq,
        syncode_bytes_hex=syncode_bytes.hex() if syncode_bytes is not None else "",
        syncode_bytes_repr=repr(syncode_bytes) if syncode_bytes is not None else "",
        method_symbol=method,
        matches_hf_decoded_utf8_bytes=matches,
        equivalence_status=eq_status,  # type: ignore[arg-type]
        equivalence_detail=eq_detail,
        warnings=warnings,
    )
