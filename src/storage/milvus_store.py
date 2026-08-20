"""Milvus 稠密向量索引适配器。"""

from __future__ import annotations

from pymilvus import DataType, MilvusClient

from src.domain.models import DocumentChunk, DocumentMetadata, RetrievedChunk


class MilvusDenseStore:
    """封装 Milvus 建表、写入、检索与文档级删除。"""

    def __init__(self, uri: str, collection_name: str, dimension: int):
        self.client = MilvusClient(uri=uri)
        self.collection_name = collection_name
        self.dimension = dimension
        self._ensure_collection()

    def _ensure_collection(self) -> None:
        if self.client.has_collection(collection_name=self.collection_name):
            return
        schema = self.client.create_schema(auto_id=False, enable_dynamic_field=False)
        schema.add_field("chunk_id", DataType.VARCHAR, is_primary=True, max_length=128)
        schema.add_field("doc_id", DataType.VARCHAR, max_length=128)
        schema.add_field("embedding", DataType.FLOAT_VECTOR, dim=self.dimension)
        schema.add_field("content", DataType.VARCHAR, max_length=65535)
        schema.add_field("metadata", DataType.JSON)
        index_params = self.client.prepare_index_params()
        index_params.add_index(
            field_name="embedding", index_type="AUTOINDEX", metric_type="COSINE"
        )
        self.client.create_collection(
            collection_name=self.collection_name,
            schema=schema,
            index_params=index_params,
        )

    def add(self, chunks: list[DocumentChunk], embeddings: list[list[float]]) -> None:
        if len(chunks) != len(embeddings):
            raise ValueError("文本块数量与向量数量不一致。")
        rows = [
            {
                "chunk_id": chunk.chunk_id,
                "doc_id": chunk.doc_id,
                "embedding": embedding,
                "content": chunk.content,
                "metadata": chunk.vector_metadata(),
            }
            for chunk, embedding in zip(chunks, embeddings, strict=True)
        ]
        if rows:
            self.client.insert(collection_name=self.collection_name, data=rows)

    def search(self, embedding: list[float], limit: int) -> list[RetrievedChunk]:
        response = self.client.search(
            collection_name=self.collection_name,
            data=[embedding],
            anns_field="embedding",
            limit=limit,
            output_fields=["doc_id", "content", "metadata"],
            search_params={"metric_type": "COSINE"},
        )
        results: list[RetrievedChunk] = []
        for hit in response[0] if response else []:
            entity = hit.get("entity", {})
            values = entity.get("metadata", {})
            metadata_keys = set(DocumentMetadata.__dataclass_fields__)
            metadata = DocumentMetadata(
                **{key: value for key, value in values.items() if key in metadata_keys}
            )
            chunk = DocumentChunk(
                chunk_id=str(hit.get("id") or values.get("chunk_id")),
                doc_id=entity.get("doc_id") or values.get("doc_id", ""),
                content=entity.get("content", ""),
                chunk_index=int(values.get("chunk_index", 0)),
                page_number=int(values.get("page_number", 0)) or None,
                section=values.get("section", ""),
                metadata=metadata,
                locator=values.get("locator") or {},
            )
            results.append(
                RetrievedChunk(
                    chunk=chunk,
                    dense_score=float(hit.get("distance", 0.0)),
                    routes={"dense"},
                )
            )
        return results

    def delete_document(self, doc_id: str) -> None:
        safe_doc_id = doc_id.replace('"', "")
        self.client.delete(
            collection_name=self.collection_name,
            filter=f'doc_id == "{safe_doc_id}"',
        )
