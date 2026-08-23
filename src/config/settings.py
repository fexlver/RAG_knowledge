"""应用配置。

所有可调参数统一从环境变量读取，避免在业务代码中散落硬编码。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def _to_int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


def _to_float(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


@dataclass(frozen=True, slots=True)
class Settings:
    """系统运行参数。"""

    project_root: Path
    dashscope_api_key: str
    dashscope_base_url: str
    qwen_model: str
    embedding_model: str
    embedding_dimension: int
    rerank_model: str
    milvus_host: str
    milvus_port: int
    milvus_collection: str
    sqlite_path: Path
    upload_dir: Path
    dense_top_k: int
    lexical_top_k: int
    fusion_top_k: int
    rerank_top_k: int
    rrf_k: int
    rerank_min_score: float
    chunk_size: int
    chunk_overlap: int
    max_agent_steps: int
    embedding_batch_size: int
    citation_limit: int
    history_message_limit: int
    # 带默认值，方便测试直接构造 Settings 而不必关心解析器选择。
    pdf_parser: str = "pymupdf_text"
    # rerank 后精确事实行加分：候选池扩大量与每次命中的加权幅度
    fact_boost_pool: int = 10
    fact_boost_weight: float = 0.15

    @property
    def milvus_uri(self) -> str:
        return f"http://{self.milvus_host}:{self.milvus_port}"

    @classmethod
    def from_env(cls, project_root: Path | None = None) -> Settings:
        root = (project_root or Path(__file__).resolve().parents[2]).resolve()
        load_dotenv(root / ".env")

        sqlite_raw = Path(os.getenv("SQLITE_PATH", "data/food_safety_rag.db"))
        upload_raw = Path(os.getenv("UPLOAD_DIR", "data/uploads"))
        sqlite_path = sqlite_raw if sqlite_raw.is_absolute() else root / sqlite_raw
        upload_dir = upload_raw if upload_raw.is_absolute() else root / upload_raw
        sqlite_path.parent.mkdir(parents=True, exist_ok=True)
        upload_dir.mkdir(parents=True, exist_ok=True)

        return cls(
            project_root=root,
            dashscope_api_key=os.getenv("DASHSCOPE_API_KEY", "").strip(),
            dashscope_base_url=os.getenv(
                "DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/api/v1"
            ),
            qwen_model=os.getenv("QWEN_MODEL", "qwen-plus"),
            embedding_model=os.getenv("EMBEDDING_MODEL", "text-embedding-v4"),
            embedding_dimension=_to_int("EMBEDDING_DIMENSION", 2048),
            rerank_model=os.getenv("RERANK_MODEL", "qwen3-rerank"),
            milvus_host=os.getenv("MILVUS_HOST", "127.0.0.1"),
            milvus_port=_to_int("MILVUS_PORT", 19530),
            milvus_collection=os.getenv("MILVUS_COLLECTION", "food_safety_chunks_v3"),
            sqlite_path=sqlite_path,
            upload_dir=upload_dir,
            dense_top_k=_to_int("DENSE_TOP_K", 15),
            lexical_top_k=_to_int("LEXICAL_TOP_K", 60),
            fusion_top_k=_to_int("FUSION_TOP_K", 20),
            rerank_top_k=_to_int("RERANK_TOP_K", 5),
            rrf_k=_to_int("RRF_K", 60),
            rerank_min_score=_to_float("RERANK_MIN_SCORE", 0.15),
            chunk_size=_to_int("CHUNK_SIZE", 500),
            chunk_overlap=_to_int("CHUNK_OVERLAP", 100),
            pdf_parser=os.getenv("PDF_PARSER", "pymupdf_text").strip().lower(),
            max_agent_steps=_to_int("MAX_AGENT_STEPS", 4),
            embedding_batch_size=_to_int("EMBEDDING_BATCH_SIZE", 10),
            citation_limit=_to_int("CITATION_LIMIT", 5),
            history_message_limit=_to_int("HISTORY_MESSAGE_LIMIT", 20),
            fact_boost_pool=_to_int("FACT_BOOST_POOL", 10),
            fact_boost_weight=_to_float("FACT_BOOST_WEIGHT", 0.15),
        )

    def require_dashscope_key(self) -> None:
        """在真正调用模型前校验密钥，允许无密钥运行离线测试。"""

        if not self.dashscope_api_key:
            raise RuntimeError(
                "未配置DASHSCOPE_API_KEY，请复制.env.example为.env后填写。"
            )
