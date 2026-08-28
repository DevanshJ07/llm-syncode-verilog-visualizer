"""Unit tests for secure ZIP inspection (Phase 2A.1) — no SynCode/Torch/models."""

from __future__ import annotations

import io
import zipfile
from typing import Iterable

import pytest

from app.services.import_zip import (
    BundleCategory,
    ZipInspectionError,
    ZipSecurityLimits,
    classify_bundle_path,
    classify_within_experiment,
    discover_experiment_roots,
    inspect_experiment_zip,
    normalize_zip_member_path,
    validate_zipinfo_security,
)

FOCUSED_PROBS = (
    "Prob004_vector2",
    "Prob039_always_if",
    "Prob043_vector5",
    "Prob126_circuit6",
)


def _build_zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _build_zip_with_infos(infos: Iterable[tuple[zipfile.ZipInfo, bytes]]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_STORED) as zf:
        for info, data in infos:
            zf.writestr(info, data)
    return buf.getvalue()


def _qwen_result_entries(
    *,
    prefix: str = "",
    experiment: str = "focused_four_qwen_512",
) -> dict[str, bytes]:
    root = f"{prefix}results/{experiment}"
    entries: dict[str, bytes] = {
        f"{root}/summary.json": b'{"ok":true}',
        f"{root}/results.csv": b"problem,pass\n",
        f"{root}/anomalies.md": b"# none\n",
    }
    for prob in FOCUSED_PROBS:
        entries[f"{root}/generated/{prob}.sv"] = b"module m; endmodule\n"
        entries[f"{root}/traces/{prob}.json"] = (
            f'{{"problem":"{prob}","prompt_file":"x","steps":[]}}'.encode()
        )
        entries[f"{root}/records/{prob}.json"] = (
            f'{{"problem":"{prob}"}}'.encode()
        )
    return entries


def _nemotron_result_entries(
    *,
    prefix: str = "",
    experiment: str = "focused_four_nemotron_512",
    include_log: bool = True,
) -> dict[str, bytes]:
    root = f"{prefix}results/{experiment}"
    entries: dict[str, bytes] = {
        f"{root}/summary.json": b'{"ok":true}',
        f"{root}/results.csv": b"problem,pass\n",
        f"{root}/anomalies.md": b"# none\n",
    }
    for prob in FOCUSED_PROBS:
        entries[f"{root}/generated/{prob}.sv"] = b"module m; endmodule\n"
        entries[f"{root}/traces/{prob}.json"] = (
            f'{{"problem":"{prob}","prompt_file":"x","steps":[]}}'.encode()
        )
        entries[f"{root}/records/{prob}.json"] = (
            f'{{"problem":"{prob}"}}'.encode()
        )
    if include_log:
        entries[f"{prefix}logs/{experiment}.log"] = b"run ok\n"
    return entries


# ---------------------------------------------------------------------------
# Path normalization
# ---------------------------------------------------------------------------


def test_normalize_accepts_relative_posix_path():
    path, is_dir = normalize_zip_member_path(
        "results/focused_four_qwen_512/generated/Prob001_x.sv"
    )
    assert path.endswith("generated/Prob001_x.sv")
    assert is_dir is False


def test_normalize_rejects_absolute_unix():
    with pytest.raises(ZipInspectionError, match="absolute"):
        normalize_zip_member_path("/etc/passwd")


def test_normalize_rejects_absolute_windows_backslash():
    with pytest.raises(ZipInspectionError, match="absolute|drive"):
        normalize_zip_member_path("\\Windows\\system32\\x")


def test_normalize_rejects_drive_qualified():
    with pytest.raises(ZipInspectionError, match="drive"):
        normalize_zip_member_path("C:/secret/x.sv")
    with pytest.raises(ZipInspectionError, match="drive"):
        normalize_zip_member_path("C:\\secret\\x.sv")


def test_normalize_rejects_dotdot_traversal():
    with pytest.raises(ZipInspectionError, match="traversal"):
        normalize_zip_member_path("../evil.sv")
    with pytest.raises(ZipInspectionError, match="traversal"):
        normalize_zip_member_path("generated/../../evil.sv")


def test_normalize_rejects_backslash_traversal():
    with pytest.raises(ZipInspectionError, match="traversal"):
        normalize_zip_member_path("..\\evil.sv")
    with pytest.raises(ZipInspectionError, match="traversal"):
        normalize_zip_member_path("a\\..\\b.sv")


def test_normalize_directory_entry():
    path, is_dir = normalize_zip_member_path("results/")
    assert path == "results"
    assert is_dir is True


# ---------------------------------------------------------------------------
# Classification / discovery
# ---------------------------------------------------------------------------


def test_discover_nested_experiment_root():
    paths = [
        "results/focused_four_qwen_512/generated/Prob004_vector2.sv",
        "results/focused_four_qwen_512/summary.json",
    ]
    roots = discover_experiment_roots(paths)
    assert roots == {
        "results/focused_four_qwen_512": "focused_four_qwen_512",
    }


def test_classify_within_experiment_sv_trace_summary():
    cat, pid = classify_within_experiment("generated/Prob004_vector2.sv")
    assert cat == BundleCategory.generated_verilog
    assert pid == "Prob004_vector2"

    cat, pid = classify_within_experiment("traces/Prob004_vector2.json")
    assert cat == BundleCategory.trace
    assert pid == "Prob004_vector2"

    cat, _ = classify_within_experiment("summary.json")
    assert cat == BundleCategory.summary


def test_unrelated_generated_path_not_classified():
    roots = discover_experiment_roots(
        ["results/focused_four_qwen_512/generated/Prob004_vector2.sv"]
    )
    cat, pid, exp_root, _, _ = classify_bundle_path(
        "docs/generated/Prob004_vector2.sv", roots
    )
    assert cat == BundleCategory.unknown
    assert exp_root is None
    assert pid == "Prob004_vector2"  # stem still parsed; not attached to experiment


def test_unrelated_traces_directory_not_classified():
    roots = {
        "results/focused_four_qwen_512": "focused_four_qwen_512",
    }
    cat, _, exp_root, _, _ = classify_bundle_path(
        "vendor/traces/Prob004_vector2.json", roots
    )
    assert cat == BundleCategory.unknown
    assert exp_root is None


# ---------------------------------------------------------------------------
# Verified Qwen / Nemotron layouts
# ---------------------------------------------------------------------------


def test_inspect_qwen_verified_layout():
    raw = _build_zip(_qwen_result_entries())
    result = inspect_experiment_zip(raw)
    assert result.ok
    assert result.has_generated_result_set is True
    assert result.enclosing_directory is None
    assert len(result.experiments) == 1
    exp = result.experiments[0]
    assert exp.experiment_root == "results/focused_four_qwen_512"
    assert exp.experiment_name == "focused_four_qwen_512"
    assert exp.prompt_ids == sorted(FOCUSED_PROBS)
    assert BundleCategory.summary.value in exp.categories_present
    assert BundleCategory.results_table.value in exp.categories_present
    assert BundleCategory.anomalies.value in exp.categories_present
    assert BundleCategory.generated_verilog.value in exp.categories_present
    assert BundleCategory.trace.value in exp.categories_present
    assert BundleCategory.record.value in exp.categories_present

    for prob in FOCUSED_PROBS:
        gen = [
            m
            for m in result.members
            if m.prompt_id == prob and m.category == BundleCategory.generated_verilog
        ]
        assert len(gen) == 1
        assert gen[0].experiment_name == "focused_four_qwen_512"
        assert gen[0].path_within_experiment == f"generated/{prob}.sv"


def test_inspect_nemotron_verified_layout_with_sibling_log():
    raw = _build_zip(_nemotron_result_entries())
    result = inspect_experiment_zip(raw)
    assert result.ok
    assert len(result.experiments) == 1
    exp = result.experiments[0]
    assert exp.experiment_root == "results/focused_four_nemotron_512"
    assert exp.experiment_name == "focused_four_nemotron_512"
    assert exp.sibling_log_path == "logs/focused_four_nemotron_512.log"
    assert exp.prompt_ids == sorted(FOCUSED_PROBS)
    log_members = [
        m for m in result.members if m.category == BundleCategory.runtime_log
    ]
    assert len(log_members) == 1
    assert log_members[0].experiment_name == "focused_four_nemotron_512"


def test_inspect_nested_inside_additional_enclosing_folder():
    raw = _build_zip(_nemotron_result_entries(prefix="upload_batch/"))
    result = inspect_experiment_zip(raw)
    assert result.enclosing_directory == "upload_batch"
    assert len(result.experiments) == 1
    exp = result.experiments[0]
    assert exp.experiment_root == "upload_batch/results/focused_four_nemotron_512"
    assert exp.experiment_name == "focused_four_nemotron_512"
    assert (
        exp.sibling_log_path
        == "upload_batch/logs/focused_four_nemotron_512.log"
    )
    assert exp.prompt_ids == sorted(FOCUSED_PROBS)


def test_setup_only_bundle_warns_no_false_experiment():
    raw = _build_zip(
        {
            "README.md": b"# runner\n",
            "requirements.txt": b"torch\n",
            "run_eval.py": b"print('hi')\n",
            "grammar/verilog.lark": b"start: module\n",
            "prompts/Prob004_vector2.txt": b"write mux\n",
            "reference/Prob004_vector2.sv": b"module ref; endmodule\n",
        }
    )
    result = inspect_experiment_zip(raw)
    assert result.experiments == []
    assert result.has_generated_result_set is False
    assert any("runner/setup package" in w for w in result.warnings)
    assert all(m.category != BundleCategory.generated_verilog for m in result.members)


def test_summary_results_anomalies_attach_to_correct_experiment():
    raw = _build_zip(
        {
            **_qwen_result_entries(experiment="focused_four_qwen_512"),
            "results/other_run/summary.json": b"{}",
            "results/other_run/generated/Prob004_vector2.sv": b"module x; endmodule\n",
        }
    )
    result = inspect_experiment_zip(raw)
    names = {e.experiment_name for e in result.experiments}
    assert names == {"focused_four_qwen_512", "other_run"}
    qwen = next(e for e in result.experiments if e.experiment_name == "focused_four_qwen_512")
    other = next(e for e in result.experiments if e.experiment_name == "other_run")
    assert BundleCategory.summary.value in qwen.categories_present
    assert BundleCategory.summary.value in other.categories_present
    qwen_summaries = [
        m
        for m in result.members
        if m.category == BundleCategory.summary
        and m.experiment_name == "focused_four_qwen_512"
    ]
    assert len(qwen_summaries) == 1
    assert qwen_summaries[0].normalized_path.endswith(
        "results/focused_four_qwen_512/summary.json"
    )


def test_directory_entries_ignored_safely():
    entries = _qwen_result_entries()
    entries["results/"] = b""
    entries["results/focused_four_qwen_512/"] = b""
    entries["results/focused_four_qwen_512/generated/"] = b""
    raw = _build_zip(entries)
    result = inspect_experiment_zip(raw)
    assert result.has_generated_result_set
    assert len(result.experiments) == 1


# ---------------------------------------------------------------------------
# Rejected archives
# ---------------------------------------------------------------------------


def test_reject_malformed_non_zip():
    with pytest.raises(ZipInspectionError, match="invalid|corrupted"):
        inspect_experiment_zip(b"this is not a zip")


def test_reject_absolute_and_drive_and_traversal_members():
    cases = [
        "/abs/x.sv",
        "C:/abs/x.sv",
        "../x.sv",
        "..\\x.sv",
        "a/../../b.sv",
    ]
    for name in cases:
        info = zipfile.ZipInfo(filename=name)
        info.file_size = 4
        info.compress_size = 4
        raw = _build_zip_with_infos([(info, b"data")])
        with pytest.raises(ZipInspectionError):
            inspect_experiment_zip(raw)


def test_reject_case_colliding_paths():
    raw = _build_zip(
        {
            "results/exp/generated/Prob004_vector2.sv": b"a",
            "results/exp/generated/prob004_vector2.sv": b"b",
        }
    )
    with pytest.raises(ZipInspectionError, match="case-colliding|duplicate"):
        inspect_experiment_zip(raw)


def test_reject_encrypted_flag_via_zipinfo_fixture():
    """Encrypted detection uses ZipInfo.flag_bits; writer APIs rarely emit ZipCrypto."""
    info = zipfile.ZipInfo("results/exp/generated/Prob004_vector2.sv")
    info.flag_bits |= 0x1
    info.file_size = 10
    info.compress_size = 10
    with pytest.raises(ZipInspectionError, match="encrypted"):
        validate_zipinfo_security(
            info,
            limits=ZipSecurityLimits(),
            normalized_path="results/exp/generated/Prob004_vector2.sv",
            is_directory=False,
        )


def test_reject_symlink_entry_via_zipinfo_fixture():
    info = zipfile.ZipInfo("results/exp/generated/link.sv")
    info.create_system = 3
    info.external_attr = (0o120777 << 16)
    info.file_size = 4
    info.compress_size = 4
    with pytest.raises(ZipInspectionError, match="symbolic-link"):
        validate_zipinfo_security(
            info,
            limits=ZipSecurityLimits(),
            normalized_path="results/exp/generated/link.sv",
            is_directory=False,
        )


def test_reject_excessive_member_count():
    limits = ZipSecurityLimits(max_member_count=2)
    raw = _build_zip({"a.txt": b"1", "b.txt": b"2", "c.txt": b"3"})
    with pytest.raises(ZipInspectionError, match="member count"):
        inspect_experiment_zip(raw, limits=limits)


def test_reject_oversized_member():
    limits = ZipSecurityLimits(max_member_uncompressed_bytes=8)
    raw = _build_zip(
        {"results/exp/generated/Prob004_vector2.sv": b"0123456789"}
    )
    with pytest.raises(ZipInspectionError, match="uncompressed size"):
        inspect_experiment_zip(raw, limits=limits)


def test_reject_excessive_total_uncompressed():
    limits = ZipSecurityLimits(max_total_uncompressed_bytes=10)
    raw = _build_zip(
        {
            "results/exp/generated/Prob004_vector2.sv": b"012345",
            "results/exp/generated/Prob039_always_if.sv": b"012345",
        }
    )
    with pytest.raises(ZipInspectionError, match="total uncompressed"):
        inspect_experiment_zip(raw, limits=limits)


def test_reject_suspicious_compression_ratio():
    limits = ZipSecurityLimits(
        max_compression_ratio=2.0,
        min_compressed_bytes_for_ratio_check=1,
    )
    info = zipfile.ZipInfo("results/exp/generated/Prob004_vector2.sv")
    info.file_size = 1000
    info.compress_size = 10
    with pytest.raises(ZipInspectionError, match="compression ratio"):
        validate_zipinfo_security(
            info,
            limits=limits,
            normalized_path="results/exp/generated/Prob004_vector2.sv",
            is_directory=False,
        )
