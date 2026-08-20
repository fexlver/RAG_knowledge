"""基于证据生成带引用和拒答控制的回答。"""

from __future__ import annotations

from typing import Protocol

from src.domain.models import AnswerResult, Citation, RetrievedChunk


class AnswerModel(Protocol):
    def generate_answer(self, question: str, contexts: list[str]) -> str: ...


class AnswerComposer:
    def __init__(
        self, model: AnswerModel, minimum_score: float, citation_limit: int = 5
    ):
        self.model = model
        self.minimum_score = minimum_score
        self.citation_limit = citation_limit

    def compose(
        self, question: str, evidence: list[RetrievedChunk], trace: list[str]
    ) -> AnswerResult:
        if not evidence or evidence[0].final_score < self.minimum_score:
            top_score = evidence[0].final_score if evidence else 0.0
            trace.append(
                f"置信控制：最高相关分{top_score:.3f}，低于阈值{self.minimum_score:.3f}。"
            )
            return AnswerResult(
                answer=(
                    "当前知识库中没有找到足够可靠的依据，无法确认该问题。"
                    "建议补充相关标准/法规原文，或核对主管部门发布的现行版本。"
                ),
                citations=[],
                trace=trace,
                refused=True,
            )

        citations: list[Citation] = []
        contexts: list[str] = []
        seen: set[tuple[str, int | None, str]] = set()
        for item in evidence:
            key = (
                item.chunk.metadata.source,
                item.chunk.page_number,
                item.chunk.section,
            )
            if key in seen:
                continue
            seen.add(key)
            label = len(citations) + 1
            citation = Citation(
                label=label,
                source=item.chunk.metadata.source,
                standard_code=item.chunk.metadata.standard_code,
                page_number=item.chunk.page_number,
                section=item.chunk.section,
                excerpt=item.chunk.content[:180],
            )
            citations.append(citation)
            contexts.append(f"[{label}] {citation.display_name}\n{item.chunk.content}")
            if len(citations) >= self.citation_limit:
                break

        answer = self.model.generate_answer(question, contexts).strip()
        references = "\n".join(citation.display_name for citation in citations)
        answer = f"{answer}\n\n参考依据：\n{references}"
        trace.append(
            f"答案生成：使用{len(citations)}条去重证据，并附文件、页码和章节引用。"
        )
        return AnswerResult(answer=answer, citations=citations, trace=trace)
