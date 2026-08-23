"""精确事实行加分的匹配规则测试。"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.domain.models import DocumentChunk, DocumentMetadata, RetrievedChunk
from src.retrieval.fact_boost import (
    apply_fact_line_boost,
    count_label_hits,
    extract_query_terms,
)


_COUNTER = iter(range(1000))


def make_chunk(content: str, rerank_score: float = 0.5, source: str = "测试.pdf") -> RetrievedChunk:
    metadata = DocumentMetadata(source=source, content_hash="a" * 64)
    return RetrievedChunk(
        chunk=DocumentChunk(
            chunk_id=f"c{next(_COUNTER)}",
            doc_id="d1",
            content=content,
            chunk_index=0,
            page_number=7,
            section="主要会计数据",
            metadata=metadata,
            locator={},
        ),
        rerank_score=rerank_score,
    )


def test_query_terms_filter_stopwords_and_digits():
    terms = extract_query_terms("万向钱潮2023年归属于上市公司股东的净利润是多少？")
    assert "万向钱潮" in terms or ("万向" in terms and "钱潮" in terms)
    assert "净利润" in terms
    assert "2023" not in terms
    assert "是多少" not in terms
    assert all(len(t) >= 2 for t in terms)


def test_label_match_tolerates_docling_spaces():
    content = "| 归属于上市公 司股东的净利 润（元） | 821,520,182. 86 | 1.54% |"
    terms = extract_query_terms("公司2023年归属于上市公司股东的净利润是多少？")
    assert count_label_hits(terms, content) >= 1


def test_sibling_fragment_without_label_gets_no_bonus():
    content = "| 总资产 （元） | 15,749,530,781 4.69 | 15.84 |"
    terms = extract_query_terms("公司2023年归属于上市公司股东的净利润是多少？")
    assert count_label_hits(terms, content) == 0


def test_prose_paragraph_never_boosted():
    content = "报告期内公司实现营业收入 112.81 亿元，同比增长 14.3%，经营情况稳中向好。"
    terms = extract_query_terms("公司2023年营业收入是多少？")
    assert count_label_hits(terms, content) == 0


def test_apply_boost_lifts_low_ranked_exact_fragment():
    # 真实场景中兄弟分片与目标分片的 rerank 分差约 0.02-0.07
    exact = make_chunk(
        "| 营业收入 | 11,280,990,458.17 | 14.3 |",
        rerank_score=0.40,
        source="601555_东吴证券_2023.pdf",
    )
    sibling = make_chunk(
        "| 总资产 | 157,495,307,814.69 | 15.84 |",
        rerank_score=0.45,
        source="601555_东吴证券_2023.pdf",
    )
    query = "东吴证券2023年营业收入是多少？"
    apply_fact_line_boost(query, [exact, sibling], weight=0.15)
    assert exact.fact_bonus > 0
    assert sibling.fact_bonus == 0
    assert sorted([exact, sibling], key=lambda i: i.final_score, reverse=True)[0] is exact


def test_boost_anchored_to_source_filename_no_cross_doc_lift():
    # 问题问金正大，候选是永安林业的净利润行：标签命中但文件名不含实体，不加分
    wrong_doc = make_chunk(
        "| 归属于上市公司股东的净利润 | 123,456,789.01 |", rerank_score=0.60
    )
    wrong_doc.chunk.metadata.source = "000663_永安林业_2023.pdf"
    query = "金正大2023年归属于上市公司股东的净利润是多少？"
    apply_fact_line_boost(query, [wrong_doc], weight=0.15)
    assert wrong_doc.fact_bonus == 0

    # 同样的行换成金正大的文件名则加分
    right_doc = make_chunk(
        "| 归属于上市公司股东的净利润 | 123,456,789.01 |", rerank_score=0.40
    )
    right_doc.chunk.metadata.source = "002470_金正大_2023.pdf"
    apply_fact_line_boost(query, [right_doc], weight=0.15)
    assert right_doc.fact_bonus > 0


def test_generic_term_company_does_not_hit_basic_info_row():
    content = "| 公司的中文 名称 | 东吴证券股份有限公司 |"
    terms = extract_query_terms("公司2023年营业收入是多少？")
    assert count_label_hits(terms, content) == 0


def test_quarterly_section_skipped_unless_quarterly_intent():
    quarterly = make_chunk(
        "| 归属于上市公司股东的净利润 | -40,258,108.32 | -111,168,825.86 |",
        source="002470_金正大_2023.pdf",
    )
    quarterly.chunk.section = "3.2 报告期分季度的主要会计数据"
    annual = make_chunk(
        "| 归属于上市公司股东的净利润 | -971,206,197.07 |",
        source="002470_金正大_2023.pdf",
    )
    annual.chunk.section = "3.1 近 3 年的主要会计数据和财务指标"

    annual_query = "金正大2023年归属于上市公司股东的净利润是多少？"
    apply_fact_line_boost(annual_query, [quarterly, annual], weight=0.15)
    assert quarterly.fact_bonus == 0
    assert annual.fact_bonus > 0

    quarterly.fact_bonus = 0.0
    quarterly_query = "金正大2023年第四季度归属于上市公司股东的净利润是多少？"
    apply_fact_line_boost(quarterly_query, [quarterly], weight=0.15)
    assert quarterly.fact_bonus > 0


def test_backfill_rescues_fragment_beyond_fusion_pool():
    from src.retrieval.fact_boost import backfill_fact_candidates

    exact = make_chunk(
        "| 归属于上市公司股东的净利润 | 971,207,098.07 |",
        source="002470_金正大_2023.pdf",
    )
    exact.chunk.section = "六、主要会计数据和财务指标"
    weak = make_chunk("| 净利润 | 123 |", source="002470_金正大_2023.pdf")
    in_pool = make_chunk("报告期经营情况讨论。", source="002470_金正大_2023.pdf")
    in_pool.fusion_score = 0.9
    weak.fusion_score = 0.2

    query = "金正大2023年归属于上市公司股东的净利润是多少？"
    candidates = backfill_fact_candidates(query, [[weak, exact]], [in_pool], limit=1)

    assert exact in candidates  # 命中数多的优先进池
    assert weak not in candidates  # 名额有限时弱命中被让位
