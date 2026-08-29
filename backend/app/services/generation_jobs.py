"""
In-memory live-generation job queue.

Architecture
------------
- Jobs are stored in process memory (lost on server restart).
- A single asyncio worker pulls FIFO jobs and awaits
  ``run_generate_and_save``, which itself runs model work on the existing
  single-thread ``ThreadPoolExecutor`` in ``llm_service``.
- Result: at most one local model generation at a time; status HTTP handlers
  stay responsive on the event loop.
- Additional requests are queued up to ``MAX_QUEUED_JOBS``; beyond that,
  POST returns HTTP 429.

Not used: Redis, Celery, databases, or extra model processes.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from app.models.schemas import (
    GenerateJobCreatedResponse,
    GenerateJobStatusResponse,
    GenerateRequest,
)
from app.services.generation_runner import run_generate_and_save
from app.services.generation_validation import GenerationFailedError, SyncodeUnavailableError

log = logging.getLogger(__name__)

# Cap waiting jobs (not including the one currently running).
MAX_QUEUED_JOBS = 8
# Keep terminal job records for recovery/polling after completion.
TERMINAL_TTL_SECONDS = 60 * 60  # 1 hour
MAX_TERMINAL_JOBS = 64


def _utc_now() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


@dataclass
class _JobRecord:
    job_id: str
    request: GenerateRequest
    status: str = "queued"  # queued | running | completed | failed
    message: str = "Queued"
    created_at: str = field(default_factory=_utc_now)
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    experiment_id: Optional[str] = None
    detail_path: Optional[str] = None
    step_count: Optional[int] = None
    early_termination: Optional[str] = None
    final_parse_valid: Optional[bool] = None
    mode: Optional[str] = None
    constraint_status: Optional[str] = None
    constraint_requested: Optional[bool] = None
    constraint_active_during_generation: Optional[bool] = None
    error: Optional[str] = None
    error_code: Optional[str] = None


class GenerationJobService:
    """Process-local job registry + single-worker asyncio queue."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._jobs: dict[str, _JobRecord] = {}
        self._queue: Optional[asyncio.Queue[str]] = None
        self._worker_task: Optional[asyncio.Task[Any]] = None
        self._runner = run_generate_and_save  # overridable in tests

    async def start(self) -> None:
        if self._queue is not None:
            return
        self._queue = asyncio.Queue()
        self._worker_task = asyncio.create_task(
            self._worker_loop(), name="generation-job-worker"
        )
        log.info(
            "[generation-jobs] worker started (max_queued=%d, single model executor)",
            MAX_QUEUED_JOBS,
        )

    async def stop(self) -> None:
        if self._worker_task is not None:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
            self._worker_task = None
        self._queue = None

    def _count_queued(self) -> int:
        with self._lock:
            return sum(1 for j in self._jobs.values() if j.status == "queued")

    def enqueue(self, request: GenerateRequest) -> GenerateJobCreatedResponse:
        """
        Create a queued job and schedule it. Returns immediately.

        Raises ``OverflowError`` when the queue is full (mapped to HTTP 429).
        Raises ``RuntimeError`` if the worker is not started.
        """
        if self._queue is None:
            raise RuntimeError("generation job worker is not running")

        if self._count_queued() >= MAX_QUEUED_JOBS:
            raise OverflowError(
                f"Generation queue is full (max {MAX_QUEUED_JOBS} waiting jobs). "
                "Wait for an active job to finish before submitting again."
            )

        job_id = str(uuid.uuid4())
        record = _JobRecord(job_id=job_id, request=request)
        with self._lock:
            self._jobs[job_id] = record
            self._cleanup_terminal_unlocked()

        # Put is non-blocking for an unbounded asyncio.Queue; capacity is
        # enforced via MAX_QUEUED_JOBS above.
        self._queue.put_nowait(job_id)
        log.info("[generation-jobs] enqueued job_id=%s", job_id)
        return GenerateJobCreatedResponse(
            job_id=job_id,
            status="queued",
            created_at=record.created_at,
            status_path=f"/generate/jobs/{job_id}",
        )

    def get_status(self, job_id: str) -> Optional[GenerateJobStatusResponse]:
        with self._lock:
            record = self._jobs.get(job_id)
            if record is None:
                return None
            return self._to_status(record)

    def _to_status(self, record: _JobRecord) -> GenerateJobStatusResponse:
        return GenerateJobStatusResponse(
            job_id=record.job_id,
            status=record.status,
            created_at=record.created_at,
            started_at=record.started_at,
            completed_at=record.completed_at,
            message=record.message,
            experiment_id=record.experiment_id,
            detail_path=record.detail_path,
            step_count=record.step_count,
            early_termination=record.early_termination,
            final_parse_valid=record.final_parse_valid,
            mode=record.mode,
            constraint_status=record.constraint_status,
            constraint_requested=record.constraint_requested,
            constraint_active_during_generation=record.constraint_active_during_generation,
            error=record.error,
            error_code=record.error_code,
        )

    def _set_progress(self, job_id: str, message: str) -> None:
        with self._lock:
            record = self._jobs.get(job_id)
            if record is None or record.status not in ("queued", "running"):
                return
            record.message = message

    def _cleanup_terminal_unlocked(self) -> None:
        """Drop old terminal jobs; never remove queued/running."""
        now = datetime.now(tz=timezone.utc)
        terminal: list[tuple[str, datetime]] = []
        for jid, rec in self._jobs.items():
            if rec.status not in ("completed", "failed"):
                continue
            stamp = rec.completed_at or rec.created_at
            try:
                dt = datetime.fromisoformat(stamp)
            except ValueError:
                dt = now
            age = (now - dt).total_seconds()
            if age > TERMINAL_TTL_SECONDS:
                terminal.append((jid, dt))
        for jid, _ in terminal:
            del self._jobs[jid]

        # Bound absolute terminal count (oldest completed_at first).
        terminals = [
            (jid, rec)
            for jid, rec in self._jobs.items()
            if rec.status in ("completed", "failed")
        ]
        if len(terminals) <= MAX_TERMINAL_JOBS:
            return

        def _key(item: tuple[str, _JobRecord]) -> str:
            return item[1].completed_at or item[1].created_at

        terminals.sort(key=_key)
        for jid, _ in terminals[: len(terminals) - MAX_TERMINAL_JOBS]:
            self._jobs.pop(jid, None)

    async def _worker_loop(self) -> None:
        assert self._queue is not None
        while True:
            job_id = await self._queue.get()
            try:
                await self._run_job(job_id)
            except Exception:  # noqa: BLE001
                log.exception("[generation-jobs] unexpected worker error job_id=%s", job_id)
            finally:
                self._queue.task_done()

    async def _run_job(self, job_id: str) -> None:
        with self._lock:
            record = self._jobs.get(job_id)
            if record is None:
                return
            record.status = "running"
            record.started_at = _utc_now()
            record.message = "Running…"
            request = record.request

        def progress(message: str) -> None:
            self._set_progress(job_id, message)

        try:
            result = await self._runner(request, progress=progress)
        except SyncodeUnavailableError as exc:
            detail = exc.to_detail()
            self._fail(
                job_id,
                error_code=str(detail.get("error") or "syncode_unavailable"),
                error=str(detail.get("message") or exc),
            )
            return
        except GenerationFailedError as exc:
            detail = exc.to_detail()
            self._fail(
                job_id,
                error_code=str(detail.get("error") or "generation_failed"),
                error=str(detail.get("message") or exc),
            )
            return
        except Exception as exc:  # noqa: BLE001
            log.error(
                "[generation-jobs] job failed job_id=%s: %s\n%s",
                job_id,
                exc,
                traceback.format_exc(),
            )
            self._fail(
                job_id,
                error_code="generation_exception",
                error=f"{type(exc).__name__}: {exc}",
            )
            return

        # Persist succeeded inside runner — only then mark completed.
        with self._lock:
            record = self._jobs.get(job_id)
            if record is None:
                return
            record.status = "completed"
            record.completed_at = _utc_now()
            record.message = result.message or "Completed"
            record.experiment_id = result.experiment_id
            record.detail_path = result.detail_path
            record.step_count = result.step_count
            record.early_termination = result.early_termination
            record.final_parse_valid = result.final_parse_valid
            record.mode = result.mode
            record.constraint_status = result.constraint_status
            record.constraint_requested = result.constraint_requested
            record.constraint_active_during_generation = (
                result.constraint_active_during_generation
            )
            record.error = None
            record.error_code = None
            self._cleanup_terminal_unlocked()
        log.info(
            "[generation-jobs] completed job_id=%s experiment_id=%s",
            job_id,
            result.experiment_id,
        )

    def _fail(self, job_id: str, *, error_code: str, error: str) -> None:
        with self._lock:
            record = self._jobs.get(job_id)
            if record is None:
                return
            record.status = "failed"
            record.completed_at = _utc_now()
            record.message = "Failed"
            record.error_code = error_code
            record.error = error
            # experiment_id stays null unless already published (we never publish early)
            self._cleanup_terminal_unlocked()


# Module singleton
generation_jobs = GenerationJobService()
