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


def specific_terms(query_terms: list[str]) -> list[str]:
    """标签命中用的特指词：>=3 字单词 + 相邻词拼接的复合词。

    jieba 会把"营业收入"拆成"营业""收入"两个 2 字词，营收类问题在长度
    过滤下会颗粒无收；而"风险""利润"这类孤立 2 字泛词又会命中无关表格
    的标签行。把相邻分词拼回复合词（营业+收入 -> 营业收入）两头兼顾：
    拼得出的复合词是"要找哪一行"的强信号，拼不出的孤立泛词不参与。
    """

    singles = [term for term in query_terms if len(term) >= 3]
    compounds = [
        first + second
        for first, second in zip(query_terms, query_terms[1:])
        if len(first + second) >= 3
    ]
    ordered: list[str] = []
    for term in singles + compounds:
        if term not in ordered:
            ordered.append(term)
    return ordered


def _boost_eligible(
    query_terms: list[str], quarterly_intent: bool, chunk
) -> bool:
    """加分/回填的统一守门：实体锚定 + 分季度守卫。"""

    source = chunk.metadata.source or ""
    if not any(term in source for term in query_terms):
        return False
    if not quarterly_intent and "分季度" in chunk.section:
        return False
    return True


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
    quarterly_intent = "季度" in query
    label_terms = specific_terms(query_terms)
    if not label_terms:
        return candidates
    for item in candidates:
        if not _boost_eligible(query_terms, quarterly_intent, item.chunk):
            continue
        hits = count_label_hits(label_terms, item.chunk.content)
        if hits:
            item.fact_bonus = min(weight * hits, max_bonus)
    return candidates


def backfill_fact_candidates(
    query: str,
    rankings: list[list[RetrievedChunk]],
    candidates: list[RetrievedChunk],
    limit: int = 5,
) -> list[RetrievedChunk]:
    """把没挤进融合池的精确事实行分片补进重排队列。

    表格分片不含公司名：词法检索里与所有公司的同名行并列（排名靠后），
    语义检索也排不进前列，RRF 融合后常掉出候选池。这里从各路召回的完整
    结果里捞回通过守门且标签命中的分片，直接交给 rerank+加分竞争。
    """

    query_terms = extract_query_terms(query)
    if not query_terms:
        return candidates
    quarterly_intent = "季度" in query
    label_terms = specific_terms(query_terms)
    if not label_terms:
        return candidates
    existing = {item.chunk.chunk_id for item in candidates}
    pool: list[tuple[int, RetrievedChunk]] = []
    for ranking in rankings:
        for item in ranking:
            if item.chunk.chunk_id in existing:
                continue
            if not _boost_eligible(query_terms, quarterly_intent, item.chunk):
                continue
            hits = count_label_hits(label_terms, item.chunk.content)
            if hits >= 1:
                pool.append((hits, item))
    # 标签命中越多越是"营业收入那一行"本尊；先到先得会捞进弱命中的邻行分片。
    pool.sort(key=lambda pair: pair[0], reverse=True)
    floor = min((item.fusion_score for item in candidates), default=0.0)
    for _hits, item in pool[:limit]:
        item.fusion_score = floor * 0.5
        candidates.append(item)
    return candidates
