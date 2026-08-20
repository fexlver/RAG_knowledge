"""可观测的多步检索编排器。"""

from __future__ import annotations

from src.agent.planner import QueryPlan, QueryPlanner
from src.domain.models import RetrievedChunk
from src.retrieval.fusion import reciprocal_rank_fusion
from src.retrieval.hybrid import HybridRetriever


class RetrievalOrchestrator:
    """按计划调用检索工具，并返回执行轨迹而非隐藏推理过程。"""

    def __init__(
        self, planner: QueryPlanner, retriever: HybridRetriever, rrf_k: int = 60
    ):
        self.planner = planner
        self.retriever = retriever
        self.rrf_k = rrf_k

    def execute(self, query: str) -> tuple[list[RetrievedChunk], QueryPlan, list[str]]:
        plan = self.planner.plan(query)
        trace = [f"查询路由：{plan.mode}", f"路由依据：{plan.reason}"]
        rankings: list[list[RetrievedChunk]] = []
        for index, subquery in enumerate(plan.subqueries, start=1):
            result = self.retriever.retrieve(subquery)
            rankings.append(result)
            trace.append(
                f"步骤{index}：检索“{subquery}”，获得{len(result)}条重排证据。"
            )
        if len(rankings) == 1:
            return rankings[0], plan, trace
        fused = reciprocal_rank_fusion(rankings, self.rrf_k, limit=8)
        trace.append(f"证据融合：跨步骤去重后保留{len(fused)}条证据。")
        return fused, plan, trace
