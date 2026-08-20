from src.domain.models import DocumentChunk, DocumentMetadata, RetrievedChunk
from src.retrieval.fusion import reciprocal_rank_fusion


def _result(chunk_id: str, route: str) -> RetrievedChunk:
    metadata = DocumentMetadata(source="标准.txt")
    chunk = DocumentChunk(chunk_id, "doc", chunk_id, 0, None, "", metadata)
    return RetrievedChunk(chunk=chunk, routes={route})


def test_rrf_rewards_results_found_by_both_routes_without_mutating_inputs():
    shared_dense = _result("shared", "dense")
    dense_only = _result("dense", "dense")
    shared_lexical = _result("shared", "lexical")

    fused = reciprocal_rank_fusion(
        [[shared_dense, dense_only], [shared_lexical]], rrf_k=60, limit=3
    )

    assert fused[0].chunk.chunk_id == "shared"
    assert fused[0].routes == {"dense", "lexical"}
    assert shared_dense.fusion_score == 0.0
