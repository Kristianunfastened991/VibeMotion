from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from threading import Event, Lock
from typing import Callable
import os
import traceback
import uuid

from app.models.schemas import JobStatus


_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="vibemotion-job")
_lock = Lock()
_jobs: dict[str, JobStatus] = {}
_cancel_events: dict[str, Event] = {}


class JobCancelled(RuntimeError):
    pass


def _log_job(job: JobStatus) -> None:
    stamp = datetime.now().strftime("%H:%M:%S")
    print(
        f"[{stamp}] {job.kind} {job.project_id} {job.status} {job.progress}% - {job.message}",
        flush=True,
    )


def create_job(project_id: str, kind: str, message: str) -> JobStatus:
    job = JobStatus(
        job_id=f"job-{uuid.uuid4().hex[:10]}",
        project_id=project_id,
        kind=kind,
        status="queued",
        progress=0,
        message=message,
    )
    with _lock:
        _jobs[job.job_id] = job
        _cancel_events[job.job_id] = Event()
    _log_job(job)
    return job


def get_job(job_id: str) -> JobStatus | None:
    with _lock:
        return _jobs.get(job_id)


def update_job(job_id: str, **changes) -> JobStatus:
    with _lock:
        job = _jobs[job_id]
        updated = job.model_copy(update=changes)
        _jobs[job_id] = updated
    _log_job(updated)
    return updated


def get_job_cancel_event(job_id: str) -> Event | None:
    with _lock:
        return _cancel_events.get(job_id)


def is_job_cancelled(job_id: str) -> bool:
    event = get_job_cancel_event(job_id)
    return bool(event and event.is_set())


def cancel_job(job_id: str, message: str = "Cancelled") -> JobStatus | None:
    with _lock:
        job = _jobs.get(job_id)
        if job is None:
            return None
        event = _cancel_events.setdefault(job_id, Event())
        event.set()
        if job.status in {"completed", "failed", "cancelled"}:
            return job
        updated = job.model_copy(update={"status": "cancelled", "message": message})
        _jobs[job_id] = updated
    _log_job(updated)
    return updated


def submit_job(job: JobStatus, worker: Callable[[str], None]) -> JobStatus:
    def _runner() -> None:
        if is_job_cancelled(job.job_id):
            update_job(job.job_id, status="cancelled", message="Cancelled before start")
            return
        update_job(job.job_id, status="running")
        try:
            worker(job.job_id)
            if is_job_cancelled(job.job_id):
                update_job(job.job_id, status="cancelled", message="Cancelled")
            else:
                update_job(job.job_id, status="completed", progress=100, message="Done")
        except JobCancelled as exc:
            update_job(job.job_id, status="cancelled", message=str(exc) or "Cancelled")
        except Exception as exc:
            detail = f"{exc}\n{traceback.format_exc()}" if os.environ.get("VIBEMOTION_DEBUG_ERRORS") == "1" else str(exc)
            update_job(
                job.job_id,
                status="failed",
                error=detail,
                message=str(exc),
            )

    _executor.submit(_runner)
    return job
