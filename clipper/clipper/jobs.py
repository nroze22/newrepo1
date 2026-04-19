"""In-process background job manager with live log streaming.

Each job runs in a background thread, appends messages to an asyncio.Queue per
subscriber, and updates a progress percentage. The web UI subscribes via
Server-Sent Events and sees progress in real time.
"""

from __future__ import annotations

import asyncio
import itertools
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass
class LogLine:
    ts: float
    level: str  # "info" | "warn" | "error" | "done"
    message: str
    progress: float | None = None  # 0..1 when known


@dataclass
class Job:
    id: str
    kind: str  # "ingest", "score", "render", "rescore", ...
    label: str
    status: JobStatus = JobStatus.PENDING
    progress: float = 0.0
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    error: str | None = None
    result: Any = None
    log: list[LogLine] = field(default_factory=list)


class JobContext:
    """Handle passed into the job function for emitting progress + logs."""

    def __init__(self, manager: "JobManager", job: Job) -> None:
        self._mgr = manager
        self._job = job

    def info(self, msg: str, progress: float | None = None) -> None:
        self._mgr._emit(self._job, LogLine(time.time(), "info", msg, progress))

    def warn(self, msg: str) -> None:
        self._mgr._emit(self._job, LogLine(time.time(), "warn", msg, None))

    def progress(self, pct: float, msg: str | None = None) -> None:
        self._mgr._emit(self._job, LogLine(time.time(), "info", msg or "", max(0.0, min(1.0, pct))))


class JobManager:
    def __init__(self, *, max_concurrent: int | None = None) -> None:
        self._jobs: dict[str, Job] = {}
        self._subscribers: dict[str, list[asyncio.Queue[LogLine | None]]] = {}
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        import os as _os
        cc = max_concurrent if max_concurrent is not None else int(
            _os.environ.get("CLIPPER_MAX_CONCURRENT_JOBS", "2")
        )
        self._semaphore = threading.Semaphore(max(1, cc))
        self.max_concurrent = max(1, cc)

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def submit(self, kind: str, label: str, target: Callable[[JobContext], Any]) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], kind=kind, label=label)
        with self._lock:
            self._jobs[job.id] = job
            self._subscribers[job.id] = []
        thread = threading.Thread(target=self._run, args=(job, target), daemon=True)
        thread.start()
        return job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def list(self, limit: int = 50) -> list[Job]:
        jobs = sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)
        return jobs[:limit]

    async def subscribe(self, job_id: str) -> asyncio.Queue[LogLine | None]:
        queue: asyncio.Queue[LogLine | None] = asyncio.Queue()
        with self._lock:
            if job_id not in self._jobs:
                raise KeyError(job_id)
            self._subscribers[job_id].append(queue)
            # Replay existing history so new subscribers get the full picture.
            for line in list(self._jobs[job_id].log):
                queue.put_nowait(line)
            if self._jobs[job_id].status in (JobStatus.SUCCEEDED, JobStatus.FAILED):
                queue.put_nowait(None)
        return queue

    def unsubscribe(self, job_id: str, queue: asyncio.Queue) -> None:
        with self._lock:
            subs = self._subscribers.get(job_id, [])
            if queue in subs:
                subs.remove(queue)

    def _run(self, job: Job, target: Callable[[JobContext], Any]) -> None:
        ctx = JobContext(self, job)
        # Throttle concurrent runs — prevents multiple full pipelines from
        # blowing the Anthropic token budget or pummeling the GPU.
        acquired = self._semaphore.acquire(blocking=False)
        if not acquired:
            ctx.info(f"Waiting (≤{self.max_concurrent} concurrent jobs)…", progress=0.0)
            self._semaphore.acquire()
        try:
            job.status = JobStatus.RUNNING
            job.started_at = time.time()
            try:
                ctx.info(f"{job.label} started", progress=0.0)
                job.result = target(ctx)
                job.status = JobStatus.SUCCEEDED
                self._emit(job, LogLine(time.time(), "done", "Completed", 1.0))
            except Exception as e:
                job.status = JobStatus.FAILED
                job.error = f"{type(e).__name__}: {e}"
                tb = traceback.format_exc(limit=4)
                self._emit(job, LogLine(time.time(), "error", job.error + "\n" + tb, None))
            finally:
                job.finished_at = time.time()
                self._broadcast(job, None)
        finally:
            self._semaphore.release()

    def _emit(self, job: Job, line: LogLine) -> None:
        if line.progress is not None:
            job.progress = line.progress
        job.log.append(line)
        self._broadcast(job, line)

    def _broadcast(self, job: Job, line: LogLine | None) -> None:
        with self._lock:
            subs = list(self._subscribers.get(job.id, []))
        loop = self._loop
        for q in subs:
            if loop:
                loop.call_soon_threadsafe(q.put_nowait, line)
            else:
                try:
                    q.put_nowait(line)
                except Exception:
                    pass


# Module-level default manager (shared by the FastAPI app).
manager = JobManager()
_counter = itertools.count(1)


def next_id() -> int:
    return next(_counter)
