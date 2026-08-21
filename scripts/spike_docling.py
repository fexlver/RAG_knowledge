"""Docling + RapidOCR 解析质量 spike（企业升级 Phase 0）。

对 corpus/raw 下的年报 PDF 执行 Docling 转换，输出 Markdown 投影与元素统计，
用于评估版面识别、表格结构、图片处理与单页解析耗时，不接入现有入库链路。

用法（使用独立环境，避免污染 food-rag）：
    .venv-docling/Scripts/python.exe scripts/spike_docling.py --limit 5
    .venv-docling/Scripts/python.exe scripts/spike_docling.py --limit 3 --no-ocr --tag noocr

输出（默认 output/docling-spike，指定 --tag 时为 output/docling-spike-{tag}）：
    output/docling-spike/markdown/{stem}.md    Markdown 投影
    output/docling-spike/json/{stem}.json      结构化 DoclingDocument（前 N 份）
    output/docling-spike/stats.csv             每份文档的解析指标
    output/docling-spike/summary.json          汇总指标
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def out_dir(tag: str | None) -> Path:
    return ROOT / "output" / ("docling-spike" if not tag else f"docling-spike-{tag}")


def build_converter(do_ocr: bool):
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions, RapidOcrOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    pipeline_options = PdfPipelineOptions()
    pipeline_options.do_ocr = do_ocr
    if do_ocr:
        pipeline_options.ocr_options = RapidOcrOptions()
    pipeline_options.do_table_structure = True
    return DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)}
    )


def convert_one(converter, path: Path):
    """通过字节流转换：docling-parse 原生库在 Windows 下无法加载含中文的路径。"""
    import io

    from docling.datamodel.base_models import DocumentStream

    return converter.convert(
        DocumentStream(name=path.name, stream=io.BytesIO(path.read_bytes()))
    )


def analyze_document(document) -> dict:
    """统计 DoclingDocument 的元素构成。"""
    labels = Counter()
    text_chars = 0
    for item, _level in document.iterate_items():
        labels[item.label.value] += 1
        if getattr(item, "text", None):
            text_chars += len(item.text)
    return {
        "pages": len(getattr(document, "pages", {}) or {}),
        "tables": len(document.tables),
        "pictures": len(document.pictures),
        "text_chars": text_chars,
        "labels": dict(labels),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", default="corpus/raw")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--json-limit", type=int, default=3)
    parser.add_argument("--no-ocr", action="store_true")
    parser.add_argument("--tag", default=None, help="输出目录后缀，如 noocr/ocr，便于对比实验")
    args = parser.parse_args()

    out = out_dir(args.tag)

    corpus = (ROOT / args.corpus).resolve()
    files = sorted(corpus.glob("*.pdf"))
    if args.limit > 0:
        files = files[: args.limit]
    if not files:
        print(f"[error] {corpus} 下没有 PDF", flush=True)
        return 1

    (out / "markdown").mkdir(parents=True, exist_ok=True)
    (out / "json").mkdir(parents=True, exist_ok=True)

    print(f"[info] 初始化 Docling（OCR={'开' if not args.no_ocr else '关'}）…", flush=True)
    started = time.time()
    converter = build_converter(do_ocr=not args.no_ocr)
    print(f"[info] 初始化完成 {time.time() - started:.1f}s，共 {len(files)} 份文档", flush=True)

    rows = []
    for index, path in enumerate(files, 1):
        row = {"file": path.name, "size_kb": round(path.stat().st_size / 1024)}
        try:
            started = time.time()
            result = convert_one(converter, path)
            elapsed = time.time() - started
            document = result.document
            stats = analyze_document(document)
            row.update(stats)
            row["parse_seconds"] = round(elapsed, 1)
            row["seconds_per_page"] = round(elapsed / max(stats["pages"], 1), 2)
            (out / "markdown" / f"{path.stem}.md").write_text(
                document.export_to_markdown(), encoding="utf-8"
            )
            if index <= args.json_limit:
                (out / "json" / f"{path.stem}.json").write_text(
                    json.dumps(document.export_to_dict(), ensure_ascii=False), encoding="utf-8"
                )
            print(
                f"[{index}/{len(files)}] {path.name}: {stats['pages']}页 "
                f"{stats['tables']}表 {stats['pictures']}图 {elapsed:.1f}s",
                flush=True,
            )
        except Exception as error:  # noqa: BLE001 - spike 需要完整记录失败原因
            row["error"] = f"{type(error).__name__}: {error}"
            print(f"[{index}/{len(files)}] {path.name}: 失败 {row['error']}", flush=True)
        rows.append(row)

    fieldnames = ["file", "size_kb", "pages", "tables", "pictures", "text_chars",
                  "parse_seconds", "seconds_per_page", "error"] + \
                 [key for row in rows for key in row.get("labels", {})]
    fieldnames = list(dict.fromkeys(fieldnames))
    with (out / "stats.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            item = dict(row)  # 不改动 rows，后续 summary 还要用 labels
            labels = item.pop("labels", {})
            writer.writerow({**item, **labels})

    ok_rows = [row for row in rows if not row.get("error")]
    summary = {
        "documents": len(rows),
        "failed": len(rows) - len(ok_rows),
        "total_pages": sum(row.get("pages", 0) for row in ok_rows),
        "total_tables": sum(row.get("tables", 0) for row in ok_rows),
        "total_pictures": sum(row.get("pictures", 0) for row in ok_rows),
        "total_parse_seconds": round(sum(row.get("parse_seconds", 0) for row in ok_rows), 1),
        "avg_seconds_per_page": round(
            sum(row.get("seconds_per_page", 0) * row.get("pages", 0) for row in ok_rows)
            / max(sum(row.get("pages", 0) for row in ok_rows), 1), 2
        ),
        "label_totals": dict(sum((Counter(row.get("labels", {})) for row in ok_rows), Counter())),
    }
    (out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
