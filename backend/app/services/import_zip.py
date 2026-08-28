"""
Secure, read-only ZIP inspection for SynViz imported experiment bundles.

Phase 2A.1: validate archive safety and recognize layout categories using
``ZipInfo`` metadata only.  Does **not** call ``extractall``, does not write
members to disk, and does not decode token IDs or load models/SynCode.

Verified result-bundle layout (Qwen ZIP / Nemotron files repackaged as ZIP):

    [optional_enclosing/]
      results/<experiment_name>/
        anomalies.md
        generated/<problem>.sv
        records/<problem>.json
        results.csv
        summary.json
        traces/<problem>.json
      logs/<experiment_name>.log          # optional sibling (Nemotron)

Classification requires a recognized ``results/<experiment_name>/`` root.
Paths that merely contain the words ``generated`` or ``traces`` elsewhere
are not treated as experiment outputs.
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Iterable, Optional, Union

from pydantic import BaseModel, Field

ZipSource = Union[bytes, bytearray, BinaryIO, Path, str]

# ---------------------------------------------------------------------------
# Conservative security limits (named constants for Phase 2A.2 reuse)
# ---------------------------------------------------------------------------

MAX_ZIP_MEMBER_COUNT = 5_000
MAX_MEMBER_UNCOMPRESSED_BYTES = 50 * 1024 * 1024  # 50 MiB
MAX_TOTAL_UNCOMPRESSED_BYTES = 200 * 1024 * 1024  # 200 MiB
MAX_COMPRESSION_RATIO = 100.0
MIN_COMPRESSED_BYTES_FOR_RATIO_CHECK = 64

_S_IFLNK = 0o120000
_S_IFMT = 0o170000

# Prompt IDs in focused bundles: Prob004_vector2, Prob039_always_if, …
_PROBLEM_ID_RE = re.compile(r"^Prob\d+_[A-Za-z0-9_\-]+$")

_EXPERIMENT_SUBDIRS = frozenset({"generated", "traces", "records"})
_EXPERIMENT_ROOT_FILES = frozenset({"summary.json", "results.csv", "anomalies.md"})


class ZipInspectionError(ValueError):
    """Archive rejected for security or structural reasons (maps to HTTP 4xx later)."""


class BundleCategory(str, Enum):
    generated_verilog = "generated_verilog"
    trace = "trace"
    record = "record"
    summary = "summary"
    results_table = "results_table"
    anomalies = "anomalies"
    runtime_log = "runtime_log"
    prompt_or_reference = "prompt_or_reference"
    metadata = "metadata"
    unknown = "unknown"
    directory = "directory"


@dataclass(frozen=True)
class ZipSecurityLimits:
    max_member_count: int = MAX_ZIP_MEMBER_COUNT
    max_member_uncompressed_bytes: int = MAX_MEMBER_UNCOMPRESSED_BYTES
    max_total_uncompressed_bytes: int = MAX_TOTAL_UNCOMPRESSED_BYTES
    max_compression_ratio: float = MAX_COMPRESSION_RATIO
    min_compressed_bytes_for_ratio_check: int = MIN_COMPRESSED_BYTES_FOR_RATIO_CHECK


class BundleMemberManifest(BaseModel):
    """One non-directory archive member after path normalization."""

    raw_name: str
    normalized_path: str
    category: BundleCategory
    prompt_id: Optional[str] = None
    experiment_root: Optional[str] = None
    experiment_name: Optional[str] = None
    path_within_experiment: Optional[str] = None
    compressed_size: int = 0
    uncompressed_size: int = 0
    crc: Optional[int] = None
    is_directory: bool = False


class ExperimentRootInfo(BaseModel):
    """One recognized ``results/<experiment_name>/`` tree inside the archive."""

    experiment_root: str
    experiment_name: str
    prompt_ids: list[str] = Field(default_factory=list)
    categories_present: list[str] = Field(default_factory=list)
    sibling_log_path: Optional[str] = None
    member_count: int = 0


class ZipInspectionResult(BaseModel):
    """Structured inspection/manifest for a candidate experiment ZIP."""

    ok: bool = True
    # Optional outer folder wrapping the whole upload (before ``results/``).
    enclosing_directory: Optional[str] = None
    experiments: list[ExperimentRootInfo] = Field(default_factory=list)
    members: list[BundleMemberManifest] = Field(default_factory=list)
    unknown_files: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    member_count: int = 0
    total_uncompressed_bytes: int = 0
    categories_present: list[str] = Field(default_factory=list)
    has_generated_result_set: bool = False


def _is_windows_drive_path(name: str) -> bool:
    if len(name) >= 2 and name[1] == ":":
        return True
    if len(name) >= 3 and name[1] == ":" and name[2] in "/\\":
        return True
    return False


def normalize_zip_member_path(raw_name: str) -> tuple[str, bool]:
    """
    Normalize a ZIP member name to a safe relative POSIX path.

    Returns ``(normalized_path, is_directory)``.
    """
    if raw_name is None:
        raise ZipInspectionError("ZIP member name is missing")

    name = str(raw_name)
    if name == "":
        raise ZipInspectionError("ZIP member name is empty")

    if name.startswith("/") or name.startswith("\\"):
        raise ZipInspectionError(f"absolute ZIP member path rejected: {raw_name!r}")
    if _is_windows_drive_path(name.replace("\\", "/")) or _is_windows_drive_path(name):
        raise ZipInspectionError(
            f"Windows drive-qualified ZIP member path rejected: {raw_name!r}"
        )

    tentative = name.replace("\\", "/") if "\\" in name else name
    if tentative.startswith("/"):
        raise ZipInspectionError(f"absolute ZIP member path rejected: {raw_name!r}")

    is_dir = tentative.endswith("/")
    trimmed = tentative[:-1] if is_dir else tentative
    if trimmed == "" and is_dir:
        return "", True

    parts: list[str] = []
    for part in trimmed.split("/"):
        if part == "":
            raise ZipInspectionError(
                f"empty path segment in ZIP member name: {raw_name!r}"
            )
        if part == ".":
            continue
        if part == "..":
            raise ZipInspectionError(
                f"path traversal ('..') in ZIP member name rejected: {raw_name!r}"
            )
        parts.append(part)

    if not parts:
        raise ZipInspectionError(f"invalid normalized ZIP member path: {raw_name!r}")

    normalized = "/".join(parts)
    posix = PurePosixPath(normalized)
    if posix.is_absolute() or normalized.startswith("/"):
        raise ZipInspectionError(f"absolute ZIP member path rejected: {raw_name!r}")
    if ".." in posix.parts:
        raise ZipInspectionError(
            f"path traversal after normalization rejected: {raw_name!r}"
        )
    return normalized, is_dir


def _unix_mode(info: zipfile.ZipInfo) -> Optional[int]:
    if info.create_system != 3:
        return None
    return (info.external_attr >> 16) & 0xFFFF


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    mode = _unix_mode(info)
    if mode is None:
        return False
    return (mode & _S_IFMT) == _S_IFLNK


def _is_encrypted(info: zipfile.ZipInfo) -> bool:
    return bool(info.flag_bits & 0x1)


def _is_special_file(info: zipfile.ZipInfo) -> bool:
    mode = _unix_mode(info)
    if mode is None:
        return False
    file_type = mode & _S_IFMT
    if file_type in (0, 0o100000, 0o040000, _S_IFLNK):
        return False
    return True


def validate_zipinfo_security(
    info: zipfile.ZipInfo,
    *,
    limits: ZipSecurityLimits,
    normalized_path: str,
    is_directory: bool,
) -> None:
    """Apply per-member security checks using ZipInfo metadata only."""
    if _is_encrypted(info):
        raise ZipInspectionError(
            f"encrypted ZIP entry rejected: {info.filename!r}"
        )
    if _is_symlink(info):
        raise ZipInspectionError(
            f"symbolic-link ZIP entry rejected: {info.filename!r}"
        )
    if _is_special_file(info):
        raise ZipInspectionError(
            f"unsupported special-file ZIP entry rejected: {info.filename!r}"
        )

    uncompressed = int(info.file_size or 0)
    compressed = int(info.compress_size or 0)

    if not is_directory:
        if uncompressed < 0 or compressed < 0:
            raise ZipInspectionError(
                f"negative size in ZIP entry rejected: {info.filename!r}"
            )
        if uncompressed > limits.max_member_uncompressed_bytes:
            raise ZipInspectionError(
                f"ZIP member uncompressed size exceeds limit "
                f"({uncompressed} > {limits.max_member_uncompressed_bytes}): "
                f"{normalized_path!r}"
            )
        if (
            compressed >= limits.min_compressed_bytes_for_ratio_check
            and compressed > 0
            and uncompressed / compressed > limits.max_compression_ratio
        ):
            raise ZipInspectionError(
                f"suspicious compression ratio rejected for {normalized_path!r} "
                f"(uncompressed={uncompressed}, compressed={compressed})"
            )


def infer_prompt_id(filename: str) -> Optional[str]:
    stem = PurePosixPath(filename).stem
    if _PROBLEM_ID_RE.match(stem):
        return stem
    return None


def discover_experiment_roots(file_paths: Iterable[str]) -> dict[str, str]:
    """
    Map ``experiment_root → experiment_name`` for recognized result trees.

    A root is ``…/results/<experiment_name>`` when a member path continues with
    ``generated|traces|records`` or an experiment root file
    (``summary.json``, ``results.csv``, ``anomalies.md``).
    """
    roots: dict[str, str] = {}
    for path in file_paths:
        parts = path.split("/")
        for i, part in enumerate(parts):
            if part != "results" or i + 1 >= len(parts):
                continue
            exp_name = parts[i + 1]
            if not exp_name or exp_name in {".", ".."}:
                continue
            if i + 2 >= len(parts):
                continue
            nxt = parts[i + 2]
            nxt_lower = nxt.lower()
            if nxt in _EXPERIMENT_SUBDIRS or nxt_lower in _EXPERIMENT_ROOT_FILES:
                root = "/".join(parts[: i + 2])
                roots[root] = exp_name
    return roots


def detect_optional_enclosing_directory(
    file_paths: Iterable[str],
    experiment_roots: dict[str, str],
) -> Optional[str]:
    """
    If every path shares a single directory prefix *before* ``results/…``,
    return that prefix (may contain multiple segments).

    Does **not** assume a single-level wrap.  Returns ``None`` when paths
    disagree or when ``results/`` sits at the archive root.
    """
    if not experiment_roots:
        return None

    prefixes: set[str] = set()
    for root in experiment_roots:
        idx = root.find("/results/")
        if idx == -1:
            if root.startswith("results/"):
                prefixes.add("")
            else:
                return None
        else:
            prefixes.add(root[:idx])

    if len(prefixes) != 1:
        return None
    prefix = next(iter(prefixes))
    return prefix or None


def classify_within_experiment(
    path_within_experiment: str,
) -> tuple[BundleCategory, Optional[str]]:
    """
    Classify a path relative to ``results/<experiment_name>/``.

    Only exact subdir/file contracts match — not arbitrary paths containing
    the substring ``generated`` or ``traces``.
    """
    path = path_within_experiment.replace("\\", "/").lstrip("./")
    if not path:
        return BundleCategory.unknown, None

    name = PurePosixPath(path).name
    name_lower = name.lower()
    parts = path.split("/")
    prompt_id = infer_prompt_id(name)

    if len(parts) == 1:
        if name_lower == "summary.json":
            return BundleCategory.summary, None
        if name_lower == "results.csv":
            return BundleCategory.results_table, None
        if name_lower == "anomalies.md":
            return BundleCategory.anomalies, None
        return BundleCategory.unknown, prompt_id

    sub = parts[0]
    if sub == "generated" and len(parts) == 2 and name_lower.endswith((".sv", ".v")):
        return BundleCategory.generated_verilog, prompt_id
    if sub == "traces" and len(parts) == 2 and name_lower.endswith(".json"):
        return BundleCategory.trace, prompt_id
    if sub == "records" and len(parts) == 2 and name_lower.endswith(".json"):
        return BundleCategory.record, prompt_id
    if sub in {"prompts", "prompt", "reference", "references"}:
        return BundleCategory.prompt_or_reference, prompt_id

    return BundleCategory.unknown, prompt_id


def match_sibling_experiment_log(
    path: str,
    experiment_roots: dict[str, str],
) -> tuple[Optional[str], Optional[str]]:
    """
    Match ``…/logs/<experiment_name>.log`` to a discovered experiment.

    Returns ``(experiment_root, experiment_name)`` or ``(None, None)``.
    """
    parts = path.split("/")
    if len(parts) < 2:
        return None, None
    if parts[-2] != "logs":
        return None, None
    name = parts[-1]
    if not name.lower().endswith(".log"):
        return None, None
    stem = PurePosixPath(name).stem
    for root, exp_name in experiment_roots.items():
        if exp_name == stem:
            return root, exp_name
    return None, None


def classify_bundle_path(
    normalized_path: str,
    experiment_roots: dict[str, str],
) -> tuple[BundleCategory, Optional[str], Optional[str], Optional[str], Optional[str]]:
    """
    Classify a normalized archive path.

    Returns
    ``(category, prompt_id, experiment_root, experiment_name, path_within_experiment)``.
    """
    path = normalized_path.replace("\\", "/").lstrip("./")

    # Prefer longest matching experiment root.
    matched_root: Optional[str] = None
    for root in sorted(experiment_roots.keys(), key=len, reverse=True):
        if path == root or path.startswith(root + "/"):
            matched_root = root
            break

    if matched_root is not None:
        exp_name = experiment_roots[matched_root]
        within = "" if path == matched_root else path[len(matched_root) + 1 :]
        cat, pid = classify_within_experiment(within)
        return cat, pid, matched_root, exp_name, within or None

    log_root, log_name = match_sibling_experiment_log(path, experiment_roots)
    if log_root is not None:
        return BundleCategory.runtime_log, None, log_root, log_name, None

    return BundleCategory.unknown, infer_prompt_id(path), None, None, None


def _looks_like_setup_only(file_paths: Iterable[str]) -> bool:
    """Heuristic: runner/setup package without ``results/<exp>/`` trees."""
    paths = list(file_paths)
    if not paths:
        return False
    if discover_experiment_roots(paths):
        return False
    setup_hints = (
        "readme",
        "requirements",
        "grammar",
        ".py",
        ".lark",
        "prompt",
        "reference",
        "dataset",
    )
    lowered = [p.lower() for p in paths]
    return any(any(h in p for h in setup_hints) for p in lowered)


def _open_zip(source: ZipSource) -> zipfile.ZipFile:
    try:
        if isinstance(source, (bytes, bytearray)):
            return zipfile.ZipFile(io.BytesIO(source), mode="r")
        if isinstance(source, (str, Path)):
            return zipfile.ZipFile(Path(source), mode="r")
        if hasattr(source, "seek"):
            try:
                source.seek(0)
            except Exception:  # noqa: BLE001
                pass
        return zipfile.ZipFile(source, mode="r")
    except zipfile.BadZipFile as exc:
        raise ZipInspectionError(f"invalid or corrupted ZIP archive: {exc}") from exc
    except OSError as exc:
        raise ZipInspectionError(f"unable to open ZIP archive: {exc}") from exc


def inspect_experiment_zip(
    source: ZipSource,
    *,
    limits: ZipSecurityLimits | None = None,
) -> ZipInspectionResult:
    """
    Inspect a ZIP archive for SynViz import without extracting members.

    Uses ``ZipFile.infolist()`` metadata only; member payloads are not read.
    """
    limits = limits or ZipSecurityLimits()
    result = ZipInspectionResult()

    with _open_zip(source) as zf:
        infos = list(zf.infolist())
        if len(infos) > limits.max_member_count:
            raise ZipInspectionError(
                f"ZIP member count exceeds limit "
                f"({len(infos)} > {limits.max_member_count})"
            )

        normalized_entries: list[tuple[zipfile.ZipInfo, str, bool]] = []
        for info in infos:
            norm, is_dir = normalize_zip_member_path(info.filename)
            if info.is_dir():
                is_dir = True
            normalized_entries.append((info, norm, is_dir))

        file_paths = [n for _, n, is_dir in normalized_entries if not is_dir and n]
        experiment_roots = discover_experiment_roots(file_paths)
        result.enclosing_directory = detect_optional_enclosing_directory(
            file_paths, experiment_roots
        )

        seen_casefold: dict[str, str] = {}
        total_uncompressed = 0
        categories: set[str] = set()
        per_exp: dict[str, ExperimentRootInfo] = {
            root: ExperimentRootInfo(experiment_root=root, experiment_name=name)
            for root, name in experiment_roots.items()
        }
        prompt_ids_by_root: dict[str, set[str]] = {r: set() for r in experiment_roots}
        cats_by_root: dict[str, set[str]] = {r: set() for r in experiment_roots}
        counts_by_root: dict[str, int] = {r: 0 for r in experiment_roots}

        for info, norm, is_dir in normalized_entries:
            if is_dir or norm == "":
                continue

            validate_zipinfo_security(
                info,
                limits=limits,
                normalized_path=norm,
                is_directory=False,
            )

            key = norm.casefold()
            if key in seen_casefold:
                prev = seen_casefold[key]
                if prev == norm:
                    raise ZipInspectionError(
                        f"duplicate normalized ZIP member path rejected: {norm!r}"
                    )
                raise ZipInspectionError(
                    f"case-colliding ZIP member paths rejected on Windows: "
                    f"{prev!r} vs {norm!r}"
                )
            seen_casefold[key] = norm

            uncompressed = int(info.file_size or 0)
            total_uncompressed += uncompressed
            if total_uncompressed > limits.max_total_uncompressed_bytes:
                raise ZipInspectionError(
                    f"total uncompressed ZIP size exceeds limit "
                    f"({total_uncompressed} > {limits.max_total_uncompressed_bytes})"
                )

            cat, prompt_id, exp_root, exp_name, within = classify_bundle_path(
                norm, experiment_roots
            )
            categories.add(cat.value)

            member = BundleMemberManifest(
                raw_name=info.filename,
                normalized_path=norm,
                category=cat,
                prompt_id=prompt_id,
                experiment_root=exp_root,
                experiment_name=exp_name,
                path_within_experiment=within,
                compressed_size=int(info.compress_size or 0),
                uncompressed_size=uncompressed,
                crc=int(info.CRC) if info.CRC is not None else None,
                is_directory=False,
            )
            result.members.append(member)

            if exp_root and exp_root in per_exp:
                counts_by_root[exp_root] += 1
                cats_by_root[exp_root].add(cat.value)
                if prompt_id:
                    prompt_ids_by_root[exp_root].add(prompt_id)
                if cat == BundleCategory.runtime_log:
                    per_exp[exp_root].sibling_log_path = norm

            if cat == BundleCategory.unknown:
                result.unknown_files.append(norm)

        for root, info in per_exp.items():
            info.prompt_ids = sorted(prompt_ids_by_root[root])
            info.categories_present = sorted(cats_by_root[root])
            info.member_count = counts_by_root[root]
        result.experiments = sorted(
            per_exp.values(), key=lambda e: e.experiment_root
        )

        result.member_count = len(result.members)
        result.total_uncompressed_bytes = total_uncompressed
        result.categories_present = sorted(categories)
        result.has_generated_result_set = any(
            BundleCategory.generated_verilog.value in e.categories_present
            for e in result.experiments
        )

        # Soft warnings — missing optional components must not invent data.
        if not experiment_roots:
            if _looks_like_setup_only(file_paths):
                result.warnings.append(
                    "no generated result set found under results/<experiment>/; "
                    "this archive looks like a runner/setup package, not a "
                    "complete SynViz experiment result"
                )
            else:
                result.warnings.append(
                    "no generated result set found under results/<experiment>/"
                )
        else:
            for exp in result.experiments:
                present = set(exp.categories_present)
                if BundleCategory.generated_verilog.value not in present:
                    result.warnings.append(
                        f"{exp.experiment_name}: no generated Verilog (.sv/.v) "
                        f"under {exp.experiment_root}/generated/"
                    )
                if BundleCategory.trace.value not in present:
                    result.warnings.append(
                        f"{exp.experiment_name}: no trace JSON under "
                        f"{exp.experiment_root}/traces/"
                    )
                if BundleCategory.record.value not in present:
                    result.warnings.append(
                        f"{exp.experiment_name}: no record JSON under "
                        f"{exp.experiment_root}/records/"
                    )
                if BundleCategory.summary.value not in present:
                    result.warnings.append(
                        f"{exp.experiment_name}: summary.json not detected"
                    )
        if result.unknown_files:
            result.warnings.append(
                f"{len(result.unknown_files)} unclassified file(s) present"
            )

    return result
