"""Golden Set 构建器的事实提取与章节定位测试。"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from eval.build_golden import (
    company_of,
    extract_financials,
    find_section_pages,
)


def _table_element(heading: str, content: str, page: int) -> dict:
    return {
        "kind": "table",
        "content": content,
        "page_number": page,
        "heading_path": [heading],
    }


def test_company_of_extracts_middle_segment():
    assert company_of("601555_东吴证券_2023.pdf") == "东吴证券"
    assert company_of("000752_ST西发_2023.pdf") == "ST西发"
    assert company_of("年报.pdf") == "年报"


def test_financials_prefer_three_year_table_over_quarterly():
    annual = _table_element(
        "3.1 近 3 年的主要会计数据和财务指标",
        "| 营业收入 | 11,280,990,458.17 | 10,233,442,689.90 | 14.3 |\n"
        "| 归属于上市 公司股东的 净利润 | 2,002,031,162.53 | 1,845,000,000 | 8.5 |",
        page=8,
    )
    quarterly = _table_element(
        "3.2 报告期分季度的主要会计数据",
        "| 营业收入 | 2,254,473,360.84 | 3,100,000,000 |",
        page=9,
    )
    # 分季度表在元素列表中更靠前，也必须输给“近 3 年”表的优先级
    result = extract_financials([quarterly, annual])

    assert result["revenue"]["value"] == "11,280,990,458.17"
    assert result["revenue"]["page"] == 8
    # docling 在长单元格中插的空格不影响标签匹配
    assert result["net_profit"]["value"] == "2,002,031,162.53"


def test_financials_fallback_without_three_year_table():
    only_quarterly = _table_element(
        "3.2 报告期分季度的主要会计数据",
        "| 营业收入 | 2,254,473,360.84 |",
        page=9,
    )
    result = extract_financials([only_quarterly])
    assert result["revenue"]["value"] == "2,254,473,360.84"


def test_section_pages_skip_toc_and_non_headings():
    elements = [
        {"kind": "paragraph", "content": "第三节 可能面对的风险……（目录行）", "page_number": 2},
        {"kind": "heading", "content": "第三节 可能面对的风险", "page_number": 2},
        {"kind": "heading", "content": "三、可能面对的风险", "page_number": 148},
    ]
    assert find_section_pages(elements, "可能面对的风险") == [148]
