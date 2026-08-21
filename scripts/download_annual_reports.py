"""下载巨潮资讯网年度报告种子语料。

用途：为企业级升级 Phase 0（Docling/RapidOCR 解析 spike）准备真实复杂文档。
选样策略：跨行业采集深沪两市 2023 年年度报告，每行业限量，全局按公司去重。

用法：
    python scripts/download_annual_reports.py [--target 100]

输出：
    corpus/raw/{secCode}_{secName}_{year}.pdf   原始文件（断点续跑，已存在即跳过）
    corpus/manifest.csv                          元数据清单（行业/标题/公告ID/哈希等）
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

API = "http://www.cninfo.com.cn/new/hisAnnouncement/query"
STATIC = "http://static.cninfo.com.cn/"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "corpus" / "raw"
MANIFEST = ROOT / "corpus" / "manifest.csv"

# 证监会行业分类，覆盖制造/医药/科技/消费/能源/金融/周期，保证语料多样性。
# (板块 column, plate, 行业名)
SOURCES = [
    ("szse", "sz", "医药制造业"),
    ("szse", "sz", "计算机、通信和其他电子设备制造业"),
    ("szse", "sz", "汽车制造业"),
    ("szse", "sz", "电气机械和器材制造业"),
    ("szse", "sz", "化学原料和化学制品制造业"),
    ("szse", "sz", "食品制造业"),
    ("szse", "sz", "酒、饮料和精制茶制造业"),
    ("szse", "sz", "农、林、牧、渔业"),
    ("szse", "sz", "土木工程建筑业"),
    ("sse", "sh", "电力、热力生产和供应业"),
    ("sse", "sh", "货币金融服务"),
    ("sse", "sh", "保险业"),
    ("sse", "sh", "资本市场服务"),
    ("sse", "sh", "黑色金属冶炼和压延加工业"),
    ("sse", "sh", "煤炭开采和洗选业"),
    ("sse", "sh", "铁路、船舶、航空航天和其他运输设备制造业"),
]

# 摘要/英文/问询类公告不是完整年报，剔除；修订版仅在无正选时兜底。
EXCLUDE = ("摘要", "英文", "取消", "更新", "补充", "更正", "问询", "回复", "披露", "提示性")
FALLBACK = ("修订",)


def query_page(column: str, plate: str, trade: str, page: int, se_date: str) -> list[dict]:
    form = {
        "pageNum": page,
        "pageSize": 30,
        "column": column,
        "tabName": "fulltext",
        "stock": "",
        "searchkey": "",
        "secid": "",
        "plate": plate,
        "category": "category_ndbg_szsh",
        "trade": trade,
        "seDate": se_date,
    }
    for attempt in range(3):
        try:
            response = requests.post(API, data=form, headers=HEADERS, timeout=20)
            response.raise_for_status()
            return response.json().get("announcements") or []
        except (requests.RequestException, ValueError) as error:
            if attempt == 2:
                print(f"[warn] 查询失败 {trade} 第{page}页: {error}", flush=True)
                return []
            time.sleep(2)
    return []


def pick_title(title: str) -> int:
    """返回标题优先级：0 正选，1 修订版兜底，-1 排除。"""
    if "年度报告" not in title:
        return -1
    if any(word in title for word in EXCLUDE):
        return -1
    if any(word in title for word in FALLBACK):
        return 1
    return 0


def collect_candidates(target: int) -> list[dict]:
    """按行业配额收集候选公告，全局按公司代码去重。"""
    per_industry = target // len(SOURCES) + 1
    seen: set[str] = set()
    best: dict[str, dict] = {}
    se_date = "2024-01-01~2024-12-31"  # 2023 年度报告于 2024 年发布
    for column, plate, trade in SOURCES:
        quota = per_industry
        page = 1
        while quota > 0 and page <= 10:
            rows = query_page(column, plate, trade, page, se_date)
            if not rows:
                break
            for row in rows:
                title = (row.get("announcementTitle") or "").strip()
                priority = pick_title(title)
                if priority < 0 or not row.get("adjunctUrl"):
                    continue
                code = row["secCode"]
                current = best.get(code)
                if current is None:
                    best[code] = {**row, "_industry": trade, "_priority": priority}
                    seen.add(code)
                    quota -= 1
                elif priority < current["_priority"]:
                    best[code] = {**row, "_industry": trade, "_priority": priority}
                if quota <= 0:
                    break
            page += 1
            time.sleep(0.5)
        print(f"[info] {trade}: 完成，累计 {len(best)} 家公司", flush=True)
    return sorted(best.values(), key=lambda item: item["secCode"])


def download(row: dict) -> dict | None:
    year_match = re.search(r"(20\d{2})年年度报告", row["announcementTitle"])
    year = year_match.group(1) if year_match else "2023"
    name = re.sub(r'[\\/:*?"<>|\s]+', "", row["secName"])
    path = RAW_DIR / f"{row['secCode']}_{name}_{year}.pdf"
    record = {
        "sec_code": row["secCode"],
        "sec_name": row["secName"],
        "industry": row["_industry"],
        "year": year,
        "title": row["announcementTitle"],
        "announcement_id": row["announcementId"],
        "published_at": datetime.fromtimestamp(row["announcementTime"] / 1000).strftime("%Y-%m-%d"),
        "file": str(path.relative_to(ROOT)),
        "size_kb": row.get("adjunctSize") or "",
        "sha256": "",
        "status": "",
    }
    if path.exists() and path.stat().st_size > 0:
        record["status"] = "exists"
        record["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        return record
    url = STATIC + row["adjunctUrl"]
    for attempt in range(3):
        try:
            response = requests.get(url, headers=HEADERS, timeout=60)
            response.raise_for_status()
            if not response.content[:5].startswith(b"%PDF"):
                raise ValueError("非 PDF 内容")
            path.write_bytes(response.content)
            record["status"] = "downloaded"
            record["size_kb"] = round(len(response.content) / 1024)
            record["sha256"] = hashlib.sha256(response.content).hexdigest()
            return record
        except (requests.RequestException, ValueError) as error:
            if attempt == 2:
                record["status"] = f"failed: {error}"
                print(f"[warn] 下载失败 {row['secCode']}: {error}", flush=True)
                return record
            time.sleep(3)
    return record


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", type=int, default=100)
    args = parser.parse_args()
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    print(f"[info] 目标 {args.target} 份，开始查询公告列表…", flush=True)
    candidates = collect_candidates(args.target)[: args.target]
    print(f"[info] 去重后共 {len(candidates)} 家公司，开始下载…", flush=True)
    records = []
    for index, row in enumerate(candidates, 1):
        record = download(row)
        if record:
            records.append(record)
        print(f"[{index}/{len(candidates)}] {row['secCode']} {row['secName']} {record and record['status']}", flush=True)
        if record and record["status"] == "downloaded":
            time.sleep(1.2)
    with MANIFEST.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(records[0].keys()))
        writer.writeheader()
        writer.writerows(records)
    ok = sum(1 for item in records if item["status"] in ("downloaded", "exists"))
    print(f"[done] 成功 {ok}/{len(records)}，清单已写入 {MANIFEST}", flush=True)
    return 0 if ok == len(candidates) else 1


if __name__ == "__main__":
    sys.exit(main())
