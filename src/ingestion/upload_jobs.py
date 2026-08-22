"""上传入库任务管理：后台线程执行，前端轮询进度。

同步上传在 Docling 管线下动辄十几分钟，连接期间前端毫无反馈。
这里把入库搬进后台线程，接口立即返回 job_id，前端轮询获取每个文件
的阶段（解析/向量化/写入）、块数、解析器、耗时与错误信息。

约束：入库全程持有互斥锁，同一时刻只跑一个任务--Docling 的版面模型
吃满 CPU，并发只会互相拖慢并放大内存占用。
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from src.ingestion.service import DocumentIngestionService, IngestionResult

# 已终态的阶段
TERMINAL_STAGES = {"success", "skipped", "failed"}

_STAGE_LABELS = {
    "queued": "排队中",
    "parsing": "解析文档",
    "embedding": "生成向量",
    "writing": "写入索引",
    "success": "完成",
    "skipped": "跳过",
    "failed": "失败",
}


@dataclass(slots=True)
class FileProgress:
    name: str
    stage: str = "queued"
    detail: str = ""
    chunk_count: int = 0
    parser: str = ""
    duration_seconds: float = 0.0
    started_at: float | None = None
    finished_at: float | None = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "stage": self.stage,
            "stage_label": _STAGE_LABELS.get(self.stage, self.stage),
            "detail": self.detail,
            "chunk_count": self.chunk_count,
            "parser": self.parser,
            "duration_seconds": self.duration_seconds,
        }


@dataclass(slots=True)
class UploadJob:
    job_id: str
    files: list[FileProgress]
    duplicate_mode: str = "skip"
    status: str = "running"  # running | done
    created_at: float = field(default_factory=time.time)
    finished_at: float | None = None

    def to_dict(self) -> dict:
        finished_files = sum(1 for item in self.files if item.stage in TERMINAL_STAGES)
        return {
            "job_id": self.job_id,
            "status": self.status,
            "duplicate_mode": self.duplicate_mode,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
            "finished_files": finished_files,
            "total_files": len(self.files),
            "files": [item.to_dict() for item in self.files],
        }


def _cleanup_temp(path: Path) -> None:
    """删除上传临时文件；ingest 已把原文复制进受控目录。"""

    try:
        if path.is_file():
            path.unlink()
        parent = path.parent
        if parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
    except OSError:
        pass


class UploadJobManager:
    """注册上传任务并在后台线程串行执行。"""

    def __init__(self, retention_seconds: float = 3600):
        self._jobs: dict[str, UploadJob] = {}
        self._lock = threading.Lock()
        self._ingest_lock = threading.Lock()
        self._retention_seconds = retention_seconds

    def create_job(
        self, files: list[FileProgress], duplicate_mode: str = "skip"
    ) -> UploadJob:
        job = UploadJob(
            job_id=uuid.uuid4().hex[:16],
            files=list(files),
            duplicate_mode=duplicate_mode,
        )
        with self._lock:
            self._purge_expired_locked()
            self._jobs[job.job_id] = job
        return job

    def get_job(self, job_id: str) -> UploadJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    def start(
        self,
        job: UploadJob,
        pairs: list[tuple[FileProgress, Path]],
        service: DocumentIngestionService,
    ) -> None:
        thread = threading.Thread(
            target=self._run,
            args=(job, pairs, service),
            name=f"upload-{job.job_id}",
            daemon=True,
        )
        thread.start()

    def _run(
        self,
        job: UploadJob,
        pairs: list[tuple[FileProgress, Path]],
        service: DocumentIngestionService,
    ) -> None:
        try:
            for progress, path in pairs:
                progress.started_at = time.time()
                progress.stage = "parsing"
                try:
                    with self._ingest_lock:
                        result: IngestionResult = service.ingest(
                            path,
                            job.duplicate_mode,
                            progress=lambda stage, detail: self._advance(
                                progress, stage, detail
                            ),
                        )
                    progress.stage = result.status
                    progress.detail = result.detail
                    progress.chunk_count = result.chunk_count
                    progress.parser = result.parser
                    progress.duration_seconds = result.duration_seconds
                except Exception as error:  # noqa: BLE001 - 单文件失败不阻断后续文件
                    progress.stage = "failed"
                    progress.detail = str(error)
                finally:
                    progress.finished_at = time.time()
                    _cleanup_temp(path)
        finally:
            job.status = "done"
            job.finished_at = time.time()

    @staticmethod
    def _advance(progress: FileProgress, stage: str, detail: str) -> None:
        progress.stage = stage
        progress.detail = detail

    def _purge_expired_locked(self) -> None:
        now = time.time()
        expired = [
            job_id
            for job_id, job in self._jobs.items()
            if job.finished_at is not None
            and now - job.finished_at > self._retention_seconds
        ]
        for job_id in expired:
            del self._jobs[job_id]
