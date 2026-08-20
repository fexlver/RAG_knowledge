"""食品安全法规和标准元数据提取。"""

from __future__ import annotations

import re
from pathlib import Path

from src.domain import DocumentMetadata

STANDARD_CODE_PATTERN = re.compile(
    r"\b(?:GB/T|GB/Z|GB|DB\d{2}/T?|SC|SN/T|NY/T|QB/T)\s*\d+(?:\.\d+)?(?:[-—]\d{4})?\b",
    re.IGNORECASE,
)
DATE_PATTERN = r"(20\d{2})[年./-](\d{1,2})[月./-](\d{1,2})日?"


def _normalize_date(match: re.Match[str] | None) -> str:
    if not match:
        return ""
    return (
        f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
    )


def _extract_labeled_date(text: str, labels: tuple[str, ...]) -> str:
    label_pattern = "|".join(map(re.escape, labels))
    return _normalize_date(
        re.search(rf"(?:{label_pattern})[^\d]{{0,10}}{DATE_PATTERN}", text)
    )


def _guess_document_type(text: str) -> str:
    if "国家标准" in text or re.search(r"\bGB(?:/T)?\b", text, re.IGNORECASE):
        return "国家标准"
    if "地方标准" in text or re.search(r"\bDB\d{2}", text, re.IGNORECASE):
        return "地方标准"
    if "公告" in text:
        return "公告"
    if "办法" in text or "条例" in text or "法规" in text:
        return "法规"
    if "指南" in text:
        return "指南"
    return "其他"


def extract_metadata(source: str, text: str, content_hash: str) -> DocumentMetadata:
    """从文件名和首页文本中提取可用于过滤的基础元数据。"""

    sample = f"{Path(source).stem}\n{text[:4000]}"
    standard_match = STANDARD_CODE_PATTERN.search(sample)
    publish_date = _extract_labeled_date(sample, ("发布日期", "发布"))
    effective_date = _extract_labeled_date(sample, ("实施日期", "施行日期", "实施"))
    expiry_date = _extract_labeled_date(sample, ("废止日期", "失效日期"))
    status = (
        "已失效"
        if expiry_date or re.search(r"已废止|已失效|废止日期", sample)
        else "现行/待核验"
    )

    title = Path(source).stem
    first_lines = [line.strip() for line in text.splitlines()[:8] if line.strip()]
    for line in first_lines:
        if 4 <= len(line) <= 80 and not re.fullmatch(DATE_PATTERN, line):
            title = line
            break

    return DocumentMetadata(
        source=Path(source).name,
        title=title,
        document_type=_guess_document_type(sample),
        standard_code=standard_match.group(0).replace(" ", "")
        if standard_match
        else "",
        publish_date=publish_date,
        effective_date=effective_date,
        expiry_date=expiry_date,
        validity_status=status,
        version=(
            re.search(r"\d{4}$", standard_match.group(0)).group(0)
            if standard_match and re.search(r"\d{4}$", standard_match.group(0))
            else ""
        ),
        content_hash=content_hash,
    )
