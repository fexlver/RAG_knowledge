"""上传入库后台任务的阶段推进、失败隔离与临时文件清理测试。"""

import time
from pathlib import Path

from src.ingestion.service import IngestionResult
from src.ingestion.upload_jobs import FileProgress, UploadJobManager


class FakeIngestService:
    def __init__(self):
        self.seen_stages: list[str] = []

    def ingest(self, path, duplicate_mode="skip", progress=None):
        assert duplicate_mode == "skip"
        if progress:
            for stage, detail in (
                ("parsing", "正在解析文档"),
                ("embedding", "已切分 2 块"),
                ("writing", "正在写入索引"),
            ):
                progress(stage, detail)
                self.seen_stages.append(stage)
        return IngestionResult(
            file_name=Path(path).name,
            status="success",
            chunk_count=2,
            detail="入库完成",
            parser="fake",
            duration_seconds=0.5,
        )


class FailingService:
    def ingest(self, path, duplicate_mode="skip", progress=None):
        if progress:
            progress("parsing", "正在解析文档")
        raise RuntimeError("解析崩溃")


def _wait_until_done(job, timeout=5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and job.status != "done":
        time.sleep(0.02)
    assert job.status == "done"


def test_manager_runs_job_and_reports_stages(tmp_path):
    manager = UploadJobManager()
    source = tmp_path / "年报.txt"
    source.write_text("第1章 总则", encoding="utf-8")
    progress = FileProgress(name="年报.txt")
    job = manager.create_job([progress], "skip")
    service = FakeIngestService()

    manager.start(job, [(progress, source)], service)
    _wait_until_done(job)

    assert service.seen_stages == ["parsing", "embedding", "writing"]
    assert progress.stage == "success"
    assert progress.parser == "fake"
    assert progress.chunk_count == 2
    assert progress.duration_seconds == 0.5
    assert manager.get_job(job.job_id) is job
    assert not source.exists()


def test_failed_ingest_isolated_and_temp_cleaned(tmp_path):
    manager = UploadJobManager()
    first = tmp_path / "bad.txt"
    first.write_text("坏文件", encoding="utf-8")
    second = tmp_path / "good.txt"
    second.write_text("好文件", encoding="utf-8")
    bad_progress = FileProgress(name="bad.txt")
    good_progress = FileProgress(name="good.txt")
    job = manager.create_job([bad_progress, good_progress], "skip")

    class FlakyService:
        def __init__(self):
            self.inner = FakeIngestService()

        def ingest(self, path, duplicate_mode="skip", progress=None):
            if Path(path).name == "bad.txt":
                raise RuntimeError("解析崩溃")
            return self.inner.ingest(path, duplicate_mode, progress)

    manager.start(job, [(bad_progress, first), (good_progress, second)], FlakyService())
    _wait_until_done(job)

    assert bad_progress.stage == "failed"
    assert "解析崩溃" in bad_progress.detail
    assert good_progress.stage == "success"
    assert job.finished_at is not None
    assert not first.exists() and not second.exists()


def test_pre_failed_entries_skip_processing(tmp_path):
    manager = UploadJobManager()
    invalid = FileProgress(name="报表.docx", stage="failed", detail="仅支持 PDF/TXT。")
    job = manager.create_job([invalid], "skip")

    manager.start(job, [], FakeIngestService())
    _wait_until_done(job)

    payload = job.to_dict()
    assert payload["finished_files"] == 1
    assert payload["total_files"] == 1
    assert payload["files"][0]["stage_label"] == "失败"
