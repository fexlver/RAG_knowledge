from src.domain.models import DocumentChunk, DocumentMetadata, RetrievedChunk
from src.generation.composer import AnswerComposer


class FakeAnswerModel:
    def generate_answer(self, question: str, contexts: list[str]) -> str:
        assert "[1]" in contexts[0]
        return "应按标准限定范围使用。[1]"


def _evidence(score: float) -> RetrievedChunk:
    metadata = DocumentMetadata(source="GB2760.pdf", standard_code="GB2760-2024")
    chunk = DocumentChunk(
        "c1", "d1", "添加剂应按允许范围使用。", 0, 8, "3.1 使用原则", metadata
    )
    return RetrievedChunk(chunk=chunk, rerank_score=score)


def test_composer_refuses_when_evidence_score_is_too_low():
    result = AnswerComposer(FakeAnswerModel(), minimum_score=0.2).compose(
        "问题", [_evidence(0.1)], []
    )

    assert result.refused is True
    assert result.citations == []


def test_composer_adds_traceable_citation_when_confident():
    result = AnswerComposer(FakeAnswerModel(), minimum_score=0.2).compose(
        "问题", [_evidence(0.9)], []
    )

    assert result.refused is False
    assert "GB2760.pdf" in result.answer
    assert result.citations[0].page_number == 8
