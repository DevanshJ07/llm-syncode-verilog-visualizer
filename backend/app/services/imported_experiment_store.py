"""
Persistence for imported normalized experiments.

Stored separately from live ``ExperimentStore`` JSON under
``logs/imported_experiments/`` (configurable). Uses safe UUID filenames,
JSON only (no pickle), and atomic replacement where practical.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

from app.core.config import settings
from app.models.normalized import ImportedExperimentSummary, NormalizedExperiment
from app.models.provenance import ProvenanceKind
from app.services.import_normalize import is_safe_experiment_id


class ImportedStoreError(ValueError):
    """Malformed ID or corrupt on-disk imported experiment."""


class ImportedExperimentStore:
    def __init__(self, base_dir: str | Path | None = None):
        self.base_dir = Path(base_dir or settings.imported_experiments_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def new_id(self) -> str:
        return str(uuid.uuid4())

    def _path(self, experiment_id: str) -> Path:
        if not is_safe_experiment_id(experiment_id):
            raise ImportedStoreError(
                f"malformed imported experiment id: {experiment_id!r}"
            )
        base = self.base_dir.resolve()
        path = (self.base_dir / f"{experiment_id}.json").resolve()
        try:
            path.relative_to(base)
        except ValueError as exc:
            raise ImportedStoreError(
                "imported experiment path escapes storage directory"
            ) from exc
        return path

    def save(self, experiment: NormalizedExperiment) -> None:
        if experiment.source_type != "imported":
            raise ImportedStoreError(
                "refusing to persist non-imported experiment in imported store"
            )
        path = self._path(experiment.experiment_id)
        tmp = path.with_suffix(".json.tmp")
        payload = experiment.model_dump_json(indent=2)
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, path)

    def load(self, experiment_id: str) -> NormalizedExperiment | None:
        path = self._path(experiment_id)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ImportedStoreError(
                f"malformed imported experiment file for {experiment_id}"
            ) from exc
        try:
            return NormalizedExperiment.model_validate(data)
        except Exception as exc:  # noqa: BLE001 — pydantic / schema errors
            raise ImportedStoreError(
                f"malformed imported experiment schema for {experiment_id}"
            ) from exc

    def list_summaries(self) -> list[ImportedExperimentSummary]:
        summaries: list[ImportedExperimentSummary] = []
        for path in sorted(self.base_dir.glob("*.json"), reverse=True):
            if not is_safe_experiment_id(path.stem):
                continue
            try:
                exp = self.load(path.stem)
            except ImportedStoreError:
                continue
            if exp is None:
                continue
            summaries.append(to_imported_summary(exp))
        return summaries

    def list_ids(self) -> list[str]:
        return [
            p.stem
            for p in sorted(self.base_dir.glob("*.json"), reverse=True)
            if is_safe_experiment_id(p.stem)
        ]


def to_imported_summary(exp: NormalizedExperiment) -> ImportedExperimentSummary:
    model_name = None
    if (
        not exp.llm_metadata.is_unavailable
        and exp.llm_metadata.value
        and exp.llm_metadata.provenance.kind != ProvenanceKind.unavailable
    ):
        for key in ("model_name", "model"):
            if exp.llm_metadata.value.get(key):
                model_name = str(exp.llm_metadata.value[key])
                break

    has_generated = any(
        not pr.generated_output.is_unavailable and pr.generated_output.value is not None
        for pr in exp.prompt_results
    )
    return ImportedExperimentSummary(
        experiment_id=exp.experiment_id,
        experiment_name=exp.experiment_name,
        source_type=exp.source_type,
        created_at=exp.created_at,
        schema_version=exp.schema_version,
        prompt_count=len(exp.prompt_results),
        prompt_ids=[pr.problem_id for pr in exp.prompt_results],
        import_warning_count=len(exp.import_warnings),
        has_generated_outputs=has_generated,
        model_name=model_name,
    )


# Module-level singleton — override ``base_dir`` in tests via constructor.
imported_store = ImportedExperimentStore()
