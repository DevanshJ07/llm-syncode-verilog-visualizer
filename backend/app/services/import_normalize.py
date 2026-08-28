"""
Normalize a Phase 2A.1–inspected ZIP into ``NormalizedExperiment``.

Reads only inspected member paths via ``ZipFile.read`` (never ``extractall``).
Does not load tokenizers, SynCode, or models.
"""

from __future__ import annotations

import io
import json
import re
import uuid
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Optional

from app.core.grammar import grammar_sha256
from app.models.normalized import (
    NORMALIZED_SCHEMA_VERSION,
    NormalizedExperiment,
    NormalizedPromptResult,
    NormalizedTraceStep,
    SourceFileRef,
    TokenRef,
)
from app.models.provenance import Prov
from app.models.parser_analysis import ParserAnalysis
from app.services.import_zip import (
    BundleCategory,
    BundleMemberManifest,
    ZipInspectionError,
    ZipInspectionResult,
    inspect_experiment_zip,
)

# Compressed upload cap enforced by the API before calling normalize.
MAX_IMPORT_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MiB

_SAFE_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)

_HOST_META_KEYS = (
    "generated_file",
    "trace_file",
    "prompt_file",
    "dataset_dir",
    "grammar",
)


class ImportNormalizationError(ValueError):
    """Invalid experiment evidence after a secure ZIP passed inspection."""


def is_safe_experiment_id(experiment_id: str) -> bool:
    return bool(experiment_id) and bool(_SAFE_ID_RE.match(experiment_id))


def decode_text_bytes(data: bytes, *, source_path: str) -> str:
    """Decode archive text as UTF-8 / UTF-8-SIG; fail clearly on invalid bytes."""
    if data.startswith(b"\xef\xbb\xbf"):
        try:
            return data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ImportNormalizationError(
                f"invalid UTF-8-SIG text in archive member {source_path!r}: {exc}"
            ) from exc
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ImportNormalizationError(
            f"invalid UTF-8 text in archive member {source_path!r}: {exc}"
        ) from exc


def _read_member(
    zf: zipfile.ZipFile,
    path_to_info: dict[str, zipfile.ZipInfo],
    normalized_path: str,
) -> bytes:
    info = path_to_info.get(normalized_path)
    if info is None:
        raise ImportNormalizationError(
            f"inspected member missing from ZIP central directory: {normalized_path!r}"
        )
    try:
        return zf.read(info)
    except Exception as exc:  # noqa: BLE001 — surface as validation error
        raise ImportNormalizationError(
            f"unable to read archive member {normalized_path!r}: {type(exc).__name__}"
        ) from exc


def _recorded_or_unavailable_int(
    obj: dict[str, Any],
    key: str,
    *,
    source_file: str,
) -> Prov[int]:
    if key not in obj or obj[key] is None:
        return Prov[int].unavailable(
            method=f"{key} absent",
            source_file=source_file,
            source_field=key,
        )
    try:
        value = int(obj[key])
    except (TypeError, ValueError) as exc:
        raise ImportNormalizationError(
            f"invalid {key!r} in {source_file}: {exc}"
        ) from exc
    return Prov[int].recorded(
        value,
        source_file=source_file,
        source_field=key,
    )


def _token_ref_from_fields(
    obj: dict[str, Any],
    *,
    token_key: str,
    id_key: str,
    source_file: str,
) -> Prov[TokenRef]:
    has_token = token_key in obj and obj[token_key] is not None
    has_id = id_key in obj and obj[id_key] is not None
    if not has_token and not has_id:
        return Prov[TokenRef].unavailable(
            method=f"{token_key}/{id_key} absent",
            source_file=source_file,
        )
    token = obj.get(token_key) if has_token else None
    token_id = obj.get(id_key) if has_id else None
    if token_id is not None and not isinstance(token_id, int):
        try:
            token_id = int(token_id)
        except (TypeError, ValueError) as exc:
            raise ImportNormalizationError(
                f"invalid {id_key} in {source_file}: {exc}"
            ) from exc
    if token is not None and not isinstance(token, str):
        token = str(token)
    return Prov[TokenRef].recorded(
        TokenRef(token=token, token_id=token_id),
        source_file=source_file,
        source_field=f"{token_key},{id_key}",
    )


def _validate_steps(steps: Any, *, source_file: str, problem_id: str) -> list[dict]:
    if not isinstance(steps, list):
        raise ImportNormalizationError(
            f"trace {source_file}: 'steps' must be a list for {problem_id}"
        )
    if not steps:
        return []
    indices: list[int] = []
    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            raise ImportNormalizationError(
                f"trace {source_file}: step {i} is not an object"
            )
        if "step" not in step:
            raise ImportNormalizationError(
                f"trace {source_file}: step object missing 'step' index"
            )
        try:
            idx = int(step["step"])
        except (TypeError, ValueError) as exc:
            raise ImportNormalizationError(
                f"trace {source_file}: invalid step index: {exc}"
            ) from exc
        indices.append(idx)
    if len(indices) != len(set(indices)):
        raise ImportNormalizationError(
            f"trace {source_file}: duplicate step numbers for {problem_id}"
        )
    ordered = sorted(indices)
    expected = list(range(ordered[0], ordered[0] + len(ordered)))
    if ordered != expected:
        raise ImportNormalizationError(
            f"trace {source_file}: non-contiguous step numbers {ordered} "
            f"for {problem_id}"
        )
    return sorted(steps, key=lambda s: int(s["step"]))


def _normalize_step(raw: dict[str, Any], *, source_file: str) -> NormalizedTraceStep:
    idx = int(raw["step"])

    prefix = Prov[str].unavailable(method="full prefix absent")
    if "prefix_tail" in raw and raw["prefix_tail"] is not None:
        prefix = Prov[str].recorded(
            str(raw["prefix_tail"]),
            source_file=source_file,
            source_field="prefix_tail",
            method="recorded prefix_tail (may be truncated)",
        )

    raw_pref = _token_ref_from_fields(
        raw,
        token_key="raw_argmax_token",
        id_key="raw_argmax_token_id",
        source_file=source_file,
    )
    selected = _token_ref_from_fields(
        raw,
        token_key="selected_token",
        id_key="selected_token_id",
        source_file=source_file,
    )
    constrained = _token_ref_from_fields(
        raw,
        token_key="constrained_argmax_token",
        id_key="constrained_argmax_token_id",
        source_file=source_file,
    )

    if "raw_argmax_blocked" in raw and raw["raw_argmax_blocked"] is not None:
        masking = Prov[bool].recorded(
            bool(raw["raw_argmax_blocked"]),
            source_file=source_file,
            source_field="raw_argmax_blocked",
        )
    else:
        masking = Prov[bool].unavailable(method="mask intervention unknown")

    block_payload: dict[str, Any] = {}
    for key in (
        "raw_argmax_blocked",
        "selected_equals_constrained_argmax",
        "constrained_argmax_finite",
    ):
        if key in raw and raw[key] is not None:
            block_payload[key] = raw[key]
    if block_payload:
        blocked: Prov[dict[str, Any]] = Prov[dict[str, Any]].recorded(
            block_payload, source_file=source_file, source_field="mask_flags"
        )
    else:
        blocked = Prov[dict[str, Any]].unavailable()

    parser_payload: dict[str, Any] = {}
    for key in ("syncode_parse_failed", "generated_prefix_tokens"):
        if key in raw and raw[key] is not None:
            parser_payload[key] = raw[key]
    if parser_payload:
        parser_info: Prov[dict[str, Any]] = Prov[dict[str, Any]].recorded(
            parser_payload, source_file=source_file, source_field="parser_flags"
        )
    else:
        parser_info = Prov[dict[str, Any]].unavailable()

    if "top_raw_tokens" in raw and raw["top_raw_tokens"] is not None:
        top_raw: Prov[list[Any]] = Prov[list[Any]].recorded(
            list(raw["top_raw_tokens"]),
            source_file=source_file,
            source_field="top_raw_tokens",
        )
    else:
        top_raw = Prov[list[Any]].unavailable(method="top_raw_tokens absent")

    if "vocab_logits" in raw and raw["vocab_logits"] is not None:
        logits: Prov[Any] = Prov[Any].recorded(
            raw["vocab_logits"],
            source_file=source_file,
            source_field="vocab_logits",
            method="recorded logits only; probabilities not derived",
        )
    else:
        logits = Prov[Any].unavailable(method="vocab_logits absent")

    return NormalizedTraceStep(
        step_index=idx,
        prefix_before_selected=prefix,
        raw_preferred=raw_pref,
        selected=selected,
        constrained_preferred=constrained,
        masking_changed_selection=masking,
        valid_token_count=_recorded_or_unavailable_int(
            raw, "allowed_token_count", source_file=source_file
        ),
        newly_masked_token_count=_recorded_or_unavailable_int(
            raw, "newly_masked_token_count", source_file=source_file
        ),
        blocked_token_info=blocked,
        recorded_top_raw_tokens=top_raw,
        recorded_vocab_logits=logits,
        parser_info=parser_info,
    )


def _reconstruct_from_steps(
    steps: list[NormalizedTraceStep],
    *,
    trace_path: str,
) -> Prov[str]:
    if not steps:
        return Prov[str].unavailable(
            method="no trace steps to reconstruct",
            source_file=trace_path,
        )
    parts: list[str] = []
    for step in steps:
        if step.selected.is_unavailable or step.selected.value is None:
            return Prov[str].unavailable(
                method="selected_token missing; cannot reconstruct",
                source_file=trace_path,
            )
        tok = step.selected.value.token
        if tok is None:
            return Prov[str].unavailable(
                method="selected_token string missing; cannot reconstruct",
                source_file=trace_path,
            )
        parts.append(tok)
    return Prov[str].derived(
        "".join(parts),
        source_file=trace_path,
        source_field="steps[].selected_token",
        method="concatenate selected_token strings",
    )


def _group_by_problem(
    members: list[BundleMemberManifest],
) -> dict[str, dict[str, list[BundleMemberManifest]]]:
    grouped: dict[str, dict[str, list[BundleMemberManifest]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for m in members:
        if not m.prompt_id:
            continue
        if m.category == BundleCategory.generated_verilog:
            grouped[m.prompt_id]["generated"].append(m)
        elif m.category == BundleCategory.trace:
            grouped[m.prompt_id]["trace"].append(m)
        elif m.category == BundleCategory.record:
            grouped[m.prompt_id]["record"].append(m)
        elif m.category == BundleCategory.prompt_or_reference:
            grouped[m.prompt_id]["prompt_or_reference"].append(m)
    return grouped


def _extract_metadata_from_summary_config(
    config: dict[str, Any] | None,
    *,
    source_file: str,
) -> dict[str, Prov[dict[str, Any]]]:
    """
    Model / runtime metadata lives in ``summary.json["config"]``, not in
    per-prompt ``records/<problem>.json``.
    """
    empty = Prov[dict[str, Any]].unavailable(
        method="summary.json config absent",
        source_file=source_file,
    )
    if not config:
        return {
            "llm": empty,
            "grammar": empty,
            "tokenizer": empty,
            "decoding": empty,
            "runtime": empty,
        }

    def pick(*keys: str) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for k in keys:
            if k in config and config[k] is not None:
                out[k] = config[k]
        return out

    # Normalize aliases into a stable recorded shape without inventing values.
    llm = pick(
        "model",
        "model_name",
        "model_revision",
        "revision",
        "device",
        "input_device",
        "trust_remote_code",
        "enable_thinking",
        "thinking",
    )
    if "device" not in llm and "input_device" in llm:
        llm["device"] = llm["input_device"]
    if "enable_thinking" not in llm and "thinking" in llm:
        llm["enable_thinking"] = llm["thinking"]
    if "model_revision" not in llm and "revision" in llm:
        llm["model_revision"] = llm["revision"]

    grammar = pick(
        "grammar",
        "grammar_path",
        "grammar_hash",
        "grammar_sha256",
    )
    tokenizer = pick("tokenizer", "tokenizer_name", "tokenizer_revision")
    decoding = pick(
        "max_new_tokens",
        "token_limit",
        "decoding",
        "temperature",
        "top_p",
        "do_sample",
    )
    runtime = pick(
        "versions",
        "library_versions",
        "syncode_version",
        "torch_version",
        "transformers_version",
        "created_at",
        "duration_seconds",
        "dataset_dir",
    )

    def wrap(d: dict[str, Any], label: str) -> Prov[dict[str, Any]]:
        if not d:
            return Prov[dict[str, Any]].unavailable(
                method=f"{label} keys absent in summary config",
                source_file=source_file,
            )
        return Prov[dict[str, Any]].recorded(
            d,
            source_file=source_file,
            source_field="config",
            method=f"summary.json config keys for {label}",
        )

    return {
        "llm": wrap(llm, "llm"),
        "grammar": wrap(grammar, "grammar"),
        "tokenizer": wrap(tokenizer, "tokenizer"),
        "decoding": wrap(decoding, "decoding"),
        "runtime": wrap(runtime, "runtime"),
    }


_RECORD_MASK_COUNT_KEYS = (
    "mask_steps",
    "newly_masked_token_count",
    "allowed_token_count",
    "masked_token_count",
    "grammar_masked_count",
)


def _recorded_mask_counts(
    record_obj: dict[str, Any],
    *,
    source_file: str,
) -> Prov[dict[str, Any]]:
    counts: dict[str, Any] = {}
    for key in _RECORD_MASK_COUNT_KEYS:
        if key in record_obj and record_obj[key] is not None:
            counts[key] = record_obj[key]
    if not counts:
        return Prov[dict[str, Any]].unavailable(
            method="mask counts absent",
            source_file=source_file,
        )
    return Prov[dict[str, Any]].recorded(
        counts,
        source_file=source_file,
        source_field=",".join(counts.keys()),
    )


def _recorded_grammar_hash(grammar_meta: Prov[dict[str, Any]]) -> Optional[str]:
    if grammar_meta.is_unavailable or not grammar_meta.value:
        return None
    for key in ("grammar_sha256", "grammar_hash", "sha256"):
        val = grammar_meta.value.get(key)
        if val:
            return str(val).lower()
    return None


def _grammar_match_status(grammar_meta: Prov[dict[str, Any]]) -> str:
    """Return unknown | match | mismatch based only on a recorded hash."""
    recorded_hash = _recorded_grammar_hash(grammar_meta)
    if not recorded_hash:
        return "unknown"
    current = grammar_sha256().lower()
    return "match" if recorded_hash == current else "mismatch"


def _build_grammar_metadata(
    grammar_meta: Prov[dict[str, Any]],
    *,
    import_warnings: list[str],
) -> Prov[dict[str, Any]]:
    match_status = _grammar_match_status(grammar_meta)
    note = (
        "imported grammar hash absent; equality to canonical grammar not "
        "inferred from filename"
    )

    if grammar_meta.is_unavailable or not grammar_meta.value:
        return Prov[dict[str, Any]].unavailable(
            method=(
                "grammar path/hash absent; match status unknown "
                "(filename verilog.lark is not evidence of equality)"
            ),
            warnings=[f"grammar_match_status={match_status}"],
        )

    payload = dict(grammar_meta.value)
    payload["grammar_match_status"] = match_status
    warnings = [note] if match_status == "unknown" else []
    if match_status == "mismatch":
        import_warnings.append(
            "recorded grammar hash differs from current canonical grammar"
        )
    return Prov[dict[str, Any]].recorded(
        payload,
        source_file=grammar_meta.provenance.source_file,
        source_field=grammar_meta.provenance.source_field,
        method=note if match_status == "unknown" else grammar_meta.provenance.method,
        warnings=warnings,
    )


def _normalize_verdict_token(value: str) -> Optional[str]:
    s = value.strip().lower()
    if s in {"valid", "true", "pass", "ok", "1"}:
        return "valid"
    if s in {"invalid", "false", "fail", "0"}:
        return "invalid"
    return None


def _verdicts_disagree(recorded: str, recomputed: str) -> bool:
    a = _normalize_verdict_token(recorded)
    b = _normalize_verdict_token(recomputed)
    if a is not None and b is not None:
        return a != b
    return recorded.strip().lower() != recomputed.strip().lower()


def normalize_imported_bundle(
    zip_bytes: bytes,
    *,
    recompute_with_current_grammar: bool = False,
    experiment_id: str | None = None,
    inspection: ZipInspectionResult | None = None,
) -> NormalizedExperiment:
    """
    Inspect (if needed) and normalize a ZIP experiment bundle.

    Requires exactly one recognized ``results/<experiment>/`` root.
    """
    if not isinstance(zip_bytes, (bytes, bytearray)):
        raise ImportNormalizationError("ZIP payload must be bytes")

    try:
        inspection = inspection or inspect_experiment_zip(bytes(zip_bytes))
    except ZipInspectionError:
        raise

    if len(inspection.experiments) == 0:
        raise ImportNormalizationError(
            "no generated result set found under results/<experiment>/; "
            "setup/runner packages are not importable as experiment results"
        )
    if len(inspection.experiments) > 1:
        names = [e.experiment_name for e in inspection.experiments]
        raise ImportNormalizationError(
            f"multiple experiment roots found ({names}); "
            "Phase 2A.2 accepts exactly one experiment per ZIP"
        )

    exp_info = inspection.experiments[0]
    exp_root = exp_info.experiment_root
    exp_name = exp_info.experiment_name
    exp_members = [m for m in inspection.members if m.experiment_root == exp_root]

    path_to_info: dict[str, zipfile.ZipInfo] = {}
    with zipfile.ZipFile(io.BytesIO(bytes(zip_bytes)), mode="r") as zf:
        raw_to_norm = {m.raw_name: m.normalized_path for m in inspection.members}
        for info in zf.infolist():
            if info.is_dir():
                continue
            norm = raw_to_norm.get(info.filename)
            if norm is None:
                continue
            path_to_info[norm] = info

        grouped = _group_by_problem(exp_members)
        if not grouped:
            raise ImportNormalizationError(
                f"experiment {exp_name}: no prompt-associated "
                "generated/trace/record files"
            )

        import_warnings: list[str] = list(inspection.warnings)
        prompt_results: list[NormalizedPromptResult] = []

        # Experiment-level metadata from summary.json["config"] only.
        summary_members = [
            m
            for m in exp_members
            if m.category == BundleCategory.summary
        ]
        summary_config: dict[str, Any] | None = None
        summary_path = (
            summary_members[0].normalized_path if summary_members else None
        )
        if summary_path:
            stext = decode_text_bytes(
                _read_member(zf, path_to_info, summary_path),
                source_path=summary_path,
            )
            try:
                summary_obj = json.loads(stext)
            except json.JSONDecodeError as exc:
                raise ImportNormalizationError(
                    f"malformed JSON in {summary_path}: {exc}"
                ) from exc
            if isinstance(summary_obj, dict):
                cfg = summary_obj.get("config")
                if isinstance(cfg, dict):
                    summary_config = cfg
                elif cfg is not None:
                    raise ImportNormalizationError(
                        f"{summary_path}: 'config' must be an object when present"
                    )
            else:
                raise ImportNormalizationError(
                    f"{summary_path}: top-level JSON must be an object"
                )

        meta = _extract_metadata_from_summary_config(
            summary_config,
            source_file=summary_path or "summary.json",
        )

        # Per-prompt token limit comes from summary config when present.
        experiment_token_limit = Prov[int].unavailable(
            method="max_new_tokens absent from summary config"
        )
        if not meta["decoding"].is_unavailable and meta["decoding"].value:
            for key in ("max_new_tokens", "token_limit"):
                if meta["decoding"].value.get(key) is not None:
                    experiment_token_limit = Prov[int].recorded(
                        int(meta["decoding"].value[key]),
                        source_file=summary_path,
                        source_field=f"config.{key}",
                    )
                    break

        canonical_hash: Optional[str] = None
        if recompute_with_current_grammar:
            canonical_hash = grammar_sha256()

        for problem_id in sorted(grouped.keys()):
            files = grouped[problem_id]
            warnings: list[str] = []
            source_refs: list[SourceFileRef] = []
            warn_once: set[str] = set()

            def add_warn(msg: str) -> None:
                if msg not in warn_once:
                    warn_once.add(msg)
                    warnings.append(msg)

            gens = files.get("generated", [])
            if not gens:
                raise ImportNormalizationError(
                    f"problem {problem_id}: missing generated .sv/.v file"
                )
            if len(gens) > 1:
                paths = [g.normalized_path for g in gens]
                raise ImportNormalizationError(
                    f"problem {problem_id}: duplicate generated outputs {paths}"
                )
            gen_m = gens[0]
            gen_bytes = _read_member(zf, path_to_info, gen_m.normalized_path)
            gen_text = decode_text_bytes(
                gen_bytes, source_path=gen_m.normalized_path
            )
            source_refs.append(
                SourceFileRef(
                    path=gen_m.normalized_path,
                    category=gen_m.category.value,
                    role="authoritative_output",
                )
            )
            generated_output = Prov[str].recorded(
                gen_text,
                source_file=gen_m.normalized_path,
                method="authoritative archive .sv/.v",
            )

            traces = files.get("trace", [])
            records = files.get("record", [])
            if len(traces) > 1:
                raise ImportNormalizationError(
                    f"problem {problem_id}: duplicate trace files"
                )
            if len(records) > 1:
                raise ImportNormalizationError(
                    f"problem {problem_id}: duplicate record files"
                )

            steps: list[NormalizedTraceStep] = []
            reconstructed = Prov[str].unavailable(
                method="no trace steps to reconstruct"
            )
            matches = Prov[bool].unavailable(method="comparison not performed")
            termination = Prov[str].unavailable()
            gen_tok_count = Prov[int].unavailable()
            token_limit = experiment_token_limit
            grammar_valid = Prov[bool].unavailable(
                method="grammar_valid absent (not false)"
            )
            grammar_verdict = Prov[str].unavailable(
                method="grammar verdict absent (not false)"
            )
            parse_error = Prov[str].unavailable()
            findings = Prov[Any].unavailable(method="findings absent")
            mask_counts = Prov[dict[str, Any]].unavailable(
                method="mask counts absent"
            )
            prompt_text = Prov[str].unavailable(
                method="prompt not present inside archive"
            )
            reference = Prov[str].unavailable(
                method="reference not present inside archive"
            )

            if traces:
                tpath = traces[0].normalized_path
                source_refs.append(
                    SourceFileRef(path=tpath, category="trace", role="trace")
                )
                ttext = decode_text_bytes(
                    _read_member(zf, path_to_info, tpath), source_path=tpath
                )
                try:
                    trace_obj = json.loads(ttext)
                except json.JSONDecodeError as exc:
                    raise ImportNormalizationError(
                        f"malformed JSON in {tpath}: {exc}"
                    ) from exc
                if not isinstance(trace_obj, dict):
                    raise ImportNormalizationError(
                        f"trace {tpath}: top-level JSON must be an object"
                    )
                t_problem = trace_obj.get("problem")
                if t_problem is not None and str(t_problem) != problem_id:
                    raise ImportNormalizationError(
                        f"trace {tpath}: problem {t_problem!r} does not match "
                        f"filename problem {problem_id!r}"
                    )
                if trace_obj.get("prompt_file"):
                    add_warn(
                        "prompt_file is recorded host metadata only; "
                        "prompt text unavailable unless packaged in the ZIP"
                    )
                raw_steps = _validate_steps(
                    trace_obj.get("steps"),
                    source_file=tpath,
                    problem_id=problem_id,
                )
                steps = [
                    _normalize_step(s, source_file=tpath) for s in raw_steps
                ]
                reconstructed = _reconstruct_from_steps(steps, trace_path=tpath)
                if (
                    not reconstructed.is_unavailable
                    and reconstructed.value is not None
                ):
                    same = reconstructed.value == gen_text
                    matches = Prov[bool].derived(
                        same,
                        source_file=tpath,
                        method=(
                            "compare selected_token concatenation to .sv/.v"
                        ),
                    )
                    if not same:
                        add_warn(
                            "selected_token reconstruction differs from "
                            "authoritative .sv/.v; keeping .sv/.v"
                        )

            if records:
                rpath = records[0].normalized_path
                source_refs.append(
                    SourceFileRef(path=rpath, category="record", role="record")
                )
                rtext = decode_text_bytes(
                    _read_member(zf, path_to_info, rpath), source_path=rpath
                )
                try:
                    record_obj = json.loads(rtext)
                except json.JSONDecodeError as exc:
                    raise ImportNormalizationError(
                        f"malformed JSON in {rpath}: {exc}"
                    ) from exc
                if not isinstance(record_obj, dict):
                    raise ImportNormalizationError(
                        f"record {rpath}: top-level JSON must be an object"
                    )
                r_problem = record_obj.get("problem")
                if r_problem is not None and str(r_problem) != problem_id:
                    raise ImportNormalizationError(
                        f"record {rpath}: problem {r_problem!r} does not match "
                        f"filename problem {problem_id!r}"
                    )

                if (
                    "termination" in record_obj
                    and record_obj["termination"] is not None
                ):
                    termination = Prov[str].recorded(
                        str(record_obj["termination"]),
                        source_file=rpath,
                        source_field="termination",
                    )
                else:
                    termination = Prov[str].unavailable(
                        method="termination absent",
                        source_file=rpath,
                        source_field="termination",
                    )
                if (
                    "generated_tokens" in record_obj
                    and record_obj["generated_tokens"] is not None
                ):
                    gen_tok_count = Prov[int].recorded(
                        int(record_obj["generated_tokens"]),
                        source_file=rpath,
                        source_field="generated_tokens",
                    )
                elif steps:
                    gen_tok_count = Prov[int].derived(
                        len(steps),
                        source_file=rpath,
                        method="len(trace steps)",
                    )

                if (
                    "grammar_valid" in record_obj
                    and record_obj["grammar_valid"] is not None
                ):
                    gv = record_obj["grammar_valid"]
                    if not isinstance(gv, bool):
                        raise ImportNormalizationError(
                            f"record {rpath}: grammar_valid must be a boolean"
                        )
                    # Preserve False as a recorded boolean (not unavailable).
                    grammar_valid = Prov[bool].recorded(
                        gv,
                        source_file=rpath,
                        source_field="grammar_valid",
                    )
                    grammar_verdict = Prov[str].recorded(
                        "valid" if gv else "invalid",
                        source_file=rpath,
                        source_field="grammar_valid",
                    )
                if "verdict" in record_obj and record_obj["verdict"] is not None:
                    grammar_verdict = Prov[str].recorded(
                        str(record_obj["verdict"]),
                        source_file=rpath,
                        source_field="verdict",
                    )

                if (
                    "parse_error" in record_obj
                    and record_obj["parse_error"] is not None
                ):
                    parse_error = Prov[str].recorded(
                        str(record_obj["parse_error"]),
                        source_file=rpath,
                        source_field="parse_error",
                    )

                if "findings" in record_obj and record_obj["findings"] is not None:
                    findings = Prov[Any].recorded(
                        record_obj["findings"],
                        source_file=rpath,
                        source_field="findings",
                    )

                mask_counts = _recorded_mask_counts(
                    record_obj, source_file=rpath
                )

                if (
                    "mask_steps" in record_obj
                    and record_obj["mask_steps"] is not None
                    and steps
                ):
                    try:
                        mask_steps = int(record_obj["mask_steps"])
                    except (TypeError, ValueError):
                        mask_steps = None
                    if mask_steps is not None and mask_steps != len(steps):
                        add_warn(
                            f"record mask_steps={mask_steps} differs from "
                            f"trace length={len(steps)}"
                        )

                for host_key in _HOST_META_KEYS:
                    if record_obj.get(host_key):
                        add_warn(
                            f"{host_key} is recorded host metadata only and "
                            "was not opened from the local filesystem"
                        )

            for m in files.get("prompt_or_reference", []):
                text = decode_text_bytes(
                    _read_member(zf, path_to_info, m.normalized_path),
                    source_path=m.normalized_path,
                )
                source_refs.append(
                    SourceFileRef(
                        path=m.normalized_path,
                        category=m.category.value,
                        role="prompt_or_reference",
                    )
                )
                lower = m.normalized_path.lower()
                if "reference" in lower:
                    reference = Prov[str].recorded(
                        text, source_file=m.normalized_path
                    )
                else:
                    prompt_text = Prov[str].recorded(
                        text, source_file=m.normalized_path
                    )

            recomputed_verdict = Prov[str].unavailable(
                method="recompute_with_current_grammar disabled or not run"
            )
            recomputed_err = Prov[str].unavailable(
                method="recompute_with_current_grammar disabled or not run"
            )
            parser_analysis = Prov[ParserAnalysis].unavailable(
                method="recompute_with_current_grammar disabled or not run"
            )
            if recompute_with_current_grammar:
                # Lark-only final-output check against backend/grammar/verilog.lark.
                # Does not invoke SynCode or build a mask store.
                from app.services.verilog_validation import (  # noqa: PLC0415
                    validate_verilog_output,
                )
                from app.services.parser_analysis import (  # noqa: PLC0415
                    analyze_verilog_source,
                    status_disagrees_with_recorded_verdict,
                )
                from app.models.provenance import ProvenanceKind  # noqa: PLC0415

                val = validate_verilog_output(gen_text)
                recomputed_verdict = Prov[str].recomputed(
                    "valid" if val.final_parse_valid else "invalid",
                    method="validate_verilog_output",
                    grammar_sha256=canonical_hash,
                )
                if val.final_parse_error:
                    recomputed_err = Prov[str].recomputed(
                        val.final_parse_error,
                        method="validate_verilog_output",
                        grammar_sha256=canonical_hash,
                    )
                else:
                    recomputed_err = Prov[str].recomputed(
                        "",
                        method="validate_verilog_output",
                        grammar_sha256=canonical_hash,
                    )

                analysis = analyze_verilog_source(
                    gen_text,
                    provenance_kind=ProvenanceKind.recomputed,
                    method="analyze_verilog_source",
                    grammar_hash=canonical_hash,
                )
                parser_analysis = Prov[ParserAnalysis].recomputed(
                    analysis,
                    method="analyze_verilog_source",
                    grammar_sha256=canonical_hash,
                )

                if (
                    not grammar_verdict.is_unavailable
                    and grammar_verdict.value is not None
                    and (
                        status_disagrees_with_recorded_verdict(
                            str(grammar_verdict.value), analysis
                        )
                        or (
                            recomputed_verdict.value is not None
                            and _verdicts_disagree(
                                str(grammar_verdict.value),
                                str(recomputed_verdict.value),
                            )
                        )
                    )
                ):
                    add_warn(
                        "recorded grammar verdict disagrees with "
                        "recomputed canonical-grammar verdict / parser analysis"
                    )

            prompt_results.append(
                NormalizedPromptResult(
                    problem_id=problem_id,
                    prompt_text=prompt_text,
                    reference_program=reference,
                    generated_output=generated_output,
                    reconstructed_from_tokens=reconstructed,
                    reconstruction_matches_authoritative=matches,
                    termination_reason=termination,
                    generated_token_count=gen_tok_count,
                    token_limit=token_limit,
                    grammar_valid=grammar_valid,
                    grammar_verdict=grammar_verdict,
                    parse_error=parse_error,
                    findings=findings,
                    mask_counts=mask_counts,
                    recomputed_grammar_verdict=recomputed_verdict,
                    recomputed_parse_error=recomputed_err,
                    parser_analysis=parser_analysis,
                    steps=steps,
                    source_files=source_refs,
                    warnings=warnings,
                )
            )

        grammar_metadata = _build_grammar_metadata(
            meta["grammar"], import_warnings=import_warnings
        )

        runtime_extra: dict[str, Any] = {
            "experiment_root": exp_root,
            "experiment_name": exp_name,
            "enclosing_directory": inspection.enclosing_directory,
            "sibling_log_path": exp_info.sibling_log_path,
            "recompute_with_current_grammar": recompute_with_current_grammar,
        }
        if not meta["runtime"].is_unavailable and meta["runtime"].value:
            runtime_val = {**meta["runtime"].value, **runtime_extra}
            runtime_metadata = Prov[dict[str, Any]].recorded(
                runtime_val,
                source_file=meta["runtime"].provenance.source_file,
                method="summary config runtime keys + archive layout",
            )
        else:
            runtime_metadata = Prov[dict[str, Any]].recorded(
                runtime_extra,
                method="archive layout metadata",
            )

        created = datetime.now(tz=timezone.utc).isoformat()
        if (
            not meta["runtime"].is_unavailable
            and meta["runtime"].value
            and meta["runtime"].value.get("created_at")
        ):
            created = str(meta["runtime"].value["created_at"])

        return NormalizedExperiment(
            schema_version=NORMALIZED_SCHEMA_VERSION,
            experiment_id=experiment_id or str(uuid.uuid4()),
            source_type="imported",
            experiment_name=exp_name,
            created_at=created,
            llm_metadata=meta["llm"],
            grammar_metadata=grammar_metadata,
            tokenizer_metadata=meta["tokenizer"],
            decoding_metadata=meta["decoding"],
            runtime_metadata=runtime_metadata,
            prompt_results=prompt_results,
            import_warnings=import_warnings,
        )
