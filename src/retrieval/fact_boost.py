# -*- coding: utf-8 -*-
"""精确事实行加分：让含查询目标表格行的块在排序中胜出同页兄弟分片。

背景（Golden Set v0 发现）：长表按行拆分后，"营业收入"所在分片与同表
其他行分片在 rerank 眼里竞争力相同，精确数字块常排在第 6 名之后被裁掉。

规则：块内容里的 markdown 表格行，其首列（标签格）包含问题中的关键词
（如"营业收入""净利润"）时加分。正文段落没有表格行结构，天然不加分，
因此该加分只影响"表格行分片 vs 其他内容"的相对顺序。
"""

from __future__ import annotations

import jieba

from src.domain.models import RetrievedChunk

# 问句虚词与泛称不参与标签匹配，避免"公司"之类命中"公司的中文名称"行
STOP_TERMS = {
    "公司", "年报", "报告", "年度", "多少", "哪些", "什么", "是否",
    "怎么", "如何", "以及", "请问", "分别", "时候", "去年", "今年",
}


def extract_query_terms(query: str) -> list[str]:
    """分词并过滤虚词、单字与纯数字，保留可作为表格行标签的关键词。"""

    return [
        token.strip()
        for token in jieba.cut(query)
        if len(token.strip()) >= 2
        and not token.strip().isdigit()
        and token.strip() not in STOP_TERMS
    ]


def _table_label_cells(content: str) -> list[str]:
    """提取块内所有 markdown 表格行的首列文本（去空格归一化）。

    docling 会在长单元格中插空格（"归属于上市 公司股东的 净利润"），
    标签比对前必须去掉；行列分隔符本身不受影响。
    """

    labels: list[str] = []
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        parts = stripped.split("|")
        if len(parts) >= 3:
            label = parts[1].replace(" ", "")
            if label:
                labels.append(label)
    return labels


def count_label_hits(query_terms: list[str], content: str) -> int:
    """问题关键词命中表格标签行的数量。"""

    labels = _table_label_cells(content)
    if not labels:
        return 0
    return sum(1 for term in query_terms if any(term in label for label in labels))


def apply_fact_line_boost(
    query: str,
    candidates: list[RetrievedChunk],
    weight: float = 0.15,
    max_bonus: float = 0.45,
) -> list[RetrievedChunk]:
    """给标签行命中问题关键词的候选加分；返回同一列表（原地更新）。

    实体锚定：问题关键词还需命中块的来源文件名（公司名天然在年报文件名里）
    才加分。否则"金正大净利润"会把所有公司的净利润行一起抬进 top-5，
    反而破坏文档路由。
    """

    query_terms = extract_query_terms(query)
    if not query_terms:
        return candidates
    for item in candidates:
        source = item.chunk.metadata.source or ""
        if not any(term in source for term in query_terms):
            continue
        hits = count_label_hits(query_terms, item.chunk.content)
        if hits:
            item.fact_bonus = min(weight * hits, max_bonus)
    return candidates
