"""基于证据生成带引用和拒答控制的回答。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from src.domain.models import AnswerResult, Citation, RetrievedChunk


class AnswerModel(Protocol):
    def generate_answer(self, question: str, contexts: list[str]) -> str: ...


@dataclass(slots=True)
class PreparedAnswer:
    """已经完成置信判断和引用组装、等待模型生成的回答草稿。"""

    question: str
    citations: list[Citation]
    contexts: list[str]
    trace: list[str]
    refused_answer: str | None = None


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
        draft = self.prepare(question, evidence, trace)
        if draft.refused_answer:
            return self.finalize(draft.refused_answer, draft, refused=True)
        answer = self.model.generate_answer(question, draft.contexts).strip()
        return self.finalize(answer, draft)

    def prepare(
        self, question: str, evidence: list[RetrievedChunk], trace: list[str]
    ) -> PreparedAnswer:
        if not evidence or evidence[0].final_score < self.minimum_score:
            top_score = evidence[0].final_score if evidence else 0.0
            trace.append(
                f"置信控制：最高相关分{top_score:.3f}，低于阈值{self.minimum_score:.3f}。"
            )
            return PreparedAnswer(
                question=question,
                citations=[],
                contexts=[],
                trace=trace,
                refused_answer=(
                    "当前知识库中没有找到足够可靠的依据，无法确认该问题。"
                    "建议补充相关标准/法规原文，或核对主管部门发布的现行版本。"
                ),
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
                doc_id=item.chunk.doc_id,
                chunk_id=item.chunk.chunk_id,
                source=item.chunk.metadata.source,
                standard_code=item.chunk.metadata.standard_code,
                page_number=item.chunk.page_number,
                section=item.chunk.section,
                excerpt=item.chunk.content[:180],
                locator=item.chunk.locator,
            )
            citations.append(citation)
            contexts.append(
                f"[证据{label}] {citation.reference_name}\n{item.chunk.content}"
            )
            if len(citations) >= self.citation_limit:
                break

        return PreparedAnswer(
            question=question,
            citations=citations,
            contexts=contexts,
            trace=trace,
        )

    def finalize(
        self,
        generated_answer: str,
        draft: PreparedAnswer,
        *,
        refused: bool = False,
    ) -> AnswerResult:
        normalized_answer = re.sub(
            r"\[证据\s*(\d+)]", r"[\1]", generated_answer.strip()
        )
        answer = self._sanitize_citation_markers(
            normalized_answer, len(draft.citations)
        )
        if draft.citations:
            draft.trace.append(
                f"答案生成：使用{len(draft.citations)}条去重证据，并以内联编号提供原文追溯。"
            )
        return AnswerResult(
            answer=answer,
            citations=draft.citations,
            trace=draft.trace,
            refused=refused,
        )

    @staticmethod
    def _sanitize_citation_markers(answer: str, citation_count: int) -> str:
        """移除模型生成的越界引用，避免把条款编号误当成证据标签。"""

        def replace(match: re.Match[str]) -> str:
            label = int(match.group(1))
            return match.group(0) if 1 <= label <= citation_count else ""

        return re.sub(r"\[(\d+)]", replace, answer)
