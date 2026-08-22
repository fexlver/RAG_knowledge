"""年报问题查询规划器。"""

from __future__ import annotations

import re
from dataclasses import dataclass

COMPLEX_INTENT = re.compile(r"对比|比较|区别|变化|新旧|修订|废止|现行|版本|分别")
STANDARD_CODE = re.compile(
    r"\b(?:GB/T|GB/Z|GB|DB\d{2}/T?|SC|SN/T|NY/T|QB/T)\s*\d+(?:\.\d+)?(?:[-—]\d{4})?\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class QueryPlan:
    mode: str
    subqueries: tuple[str, ...]
    reason: str


class QueryPlanner:
    """将版本比较等复杂问题路由到多步检索。"""

    def __init__(self, max_steps: int = 4):
        self.max_steps = max_steps

    def plan(self, query: str) -> QueryPlan:
        codes = list(
            dict.fromkeys(
                match.group(0).replace(" ", "")
                for match in STANDARD_CODE.finditer(query)
            )
        )
        is_complex = bool(COMPLEX_INTENT.search(query)) or len(codes) >= 2
        if not is_complex:
            return QueryPlan("direct", (query,), "普通事实查询，执行一次混合检索。")

        candidates = [query]
        candidates.extend(f"{code} 适用范围 关键要求 实施日期 有效性" for code in codes)
        if not codes:
            candidates.extend(
                part.strip()
                for part in re.split(r"[，,；;和与及]", query)
                if len(part.strip()) >= 4
            )
        subqueries = tuple(dict.fromkeys(candidates))[: self.max_steps]
        return QueryPlan(
            "multi_step",
            subqueries,
            "检测到标准版本、有效性或比较意图，拆分检索后合并证据。",
        )
