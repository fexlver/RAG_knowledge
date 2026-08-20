"""通义千问 Embedding、重排序与生成的统一网关。"""

from __future__ import annotations

from collections.abc import Sequence

import dashscope

from src.config.settings import Settings


class QwenGateway:
    """集中处理模型鉴权、错误检查及响应解析。"""

    def __init__(self, settings: Settings):
        settings.require_dashscope_key()
        self.settings = settings
        dashscope.api_key = settings.dashscope_api_key
        dashscope.base_http_api_url = settings.dashscope_base_url

    @staticmethod
    def _ensure_success(response: object, operation: str) -> None:
        status_code = getattr(response, "status_code", 200)
        if status_code != 200:
            message = getattr(response, "message", "未知错误")
            raise RuntimeError(f"{operation}失败：{message}")

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """分批生成文档向量，避免超过服务端单次输入限制。"""

        vectors: list[list[float]] = []
        batch_size = self.settings.embedding_batch_size
        for offset in range(0, len(texts), batch_size):
            response = dashscope.TextEmbedding.call(
                model=self.settings.embedding_model,
                input=list(texts[offset : offset + batch_size]),
                dimension=self.settings.embedding_dimension,
            )
            self._ensure_success(response, "向量化")
            embeddings = sorted(
                response.output["embeddings"], key=lambda item: item["text_index"]
            )
            vectors.extend(item["embedding"] for item in embeddings)
        return vectors

    def embed_query(self, query: str) -> list[float]:
        return self.embed_documents([query])[0]

    def rerank(
        self, query: str, documents: Sequence[str], top_n: int
    ) -> list[tuple[int, float]]:
        response = dashscope.TextReRank.call(
            model=self.settings.rerank_model,
            query=query,
            documents=list(documents),
            top_n=min(top_n, len(documents)),
            return_documents=False,
        )
        self._ensure_success(response, "重排序")
        return [
            (int(item["index"]), float(item["relevance_score"]))
            for item in response.output["results"]
        ]

    def rewrite_query(self, question: str, history: Sequence[dict]) -> str:
        if not history:
            return question
        dialogue = "\n".join(
            f"{item.get('role', '')}: {item.get('content', '')}"
            for item in history[-6:]
        )
        prompt = (
            "结合对话历史，把最后一个问题改写为独立、可检索的问题。"
            "只输出改写后的问题，不回答。\n"
            f"历史：\n{dialogue}\n最后问题：{question}"
        )
        return (
            self._generate_text([{"role": "user", "content": prompt}]).strip()
            or question
        )

    def generate_answer(self, question: str, contexts: Sequence[str]) -> str:
        context_text = "\n\n".join(contexts)
        system = (
            "你是食品安全知识库助手。只能依据给定资料回答；每项关键结论必须使用[1]、[2]形式引用。"
            "资料不足时明确说无法从当前知识库确认，不得编造标准条款、数值或日期。"
            "涉及处罚、健康或合规决策时提醒用户核对现行原文及主管部门要求。"
        )
        user = f"资料：\n{context_text}\n\n问题：{question}"
        return self._generate_text(
            [{"role": "system", "content": system}, {"role": "user", "content": user}]
        )

    def _generate_text(self, messages: Sequence[dict]) -> str:
        response = dashscope.Generation.call(
            model=self.settings.qwen_model,
            messages=list(messages),
            result_format="message",
        )
        self._ensure_success(response, "大模型调用")
        return response.output.choices[0].message.content
