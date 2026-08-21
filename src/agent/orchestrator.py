"""可观测的多步检索编排器。"""

from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter

from src.agent.planner import QueryPlan, QueryPlanner
from src.domain.models import RetrievedChunk
from src.retrieval.fusion import reciprocal_rank_fusion
from src.retrieval.hybrid import HybridRetriever
from src.retrieval.pipeline import RetrievalPipelineConfig, RetrievalProgress


@dataclass(slots=True)
class OrchestrationStreamItem:
    """编排器的流式输出，最后一项携带完整证据与计划。"""

    progress: RetrievalProgress | None = None
    evidence: list[RetrievedChunk] | None = None
    plan: QueryPlan | None = None


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

    def execute_stream(self, query: str):
        """逐阶段执行检索，使调用方可以在耗时操作开始前推送状态。"""

        plan_started = perf_counter()
        plan = self.planner.plan(query)
        yield OrchestrationStreamItem(
            progress=RetrievalProgress(
                "route",
                "route",
                "completed",
                "查询路由",
                f"已选择 {plan.mode} 模式；{plan.reason}",
                int((perf_counter() - plan_started) * 1000),
            )
        )

        rankings: list[list[RetrievedChunk]] = []
        for index, subquery in enumerate(plan.subqueries, start=1):
            result: list[RetrievedChunk] = []
            for item in self.retriever.retrieve_stream(
                subquery, event_prefix=f"query:{index}:"
            ):
                if item.progress:
                    yield OrchestrationStreamItem(progress=item.progress)
                if item.results is not None:
                    result = item.results
            rankings.append(result)

        if len(rankings) == 1:
            evidence = rankings[0]
        else:
            event_id = "agent:fusion"
            yield OrchestrationStreamItem(
                progress=RetrievalProgress(
                    event_id,
                    "fusion",
                    "running",
                    "跨查询证据融合",
                    f"正在合并 {len(rankings)} 个子查询的检索结果…",
                )
            )
            started = perf_counter()
            evidence = reciprocal_rank_fusion(rankings, self.rrf_k, limit=8)
            yield OrchestrationStreamItem(
                progress=RetrievalProgress(
                    event_id,
                    "fusion",
                    "completed",
                    "跨查询证据融合",
                    f"跨查询去重完成，保留 {len(evidence)} 条证据。",
                    int((perf_counter() - started) * 1000),
                )
            )
        yield OrchestrationStreamItem(evidence=evidence, plan=plan)

    def retrieval_settings(self) -> dict:
        """返回当前组合及注册表能力，供设置页动态渲染。"""

        return self.retriever.describe()

    def configure_retrieval(self, value: dict) -> dict:
        config = RetrievalPipelineConfig.from_dict(value)
        return self.retriever.configure(config)
