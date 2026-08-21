"""默认混合检索器的兼容入口。"""

from __future__ import annotations

from collections.abc import Callable

from src.config.settings import Settings
from src.retrieval.pipeline import (
    ComposableRetrievalPipeline,
    DenseStore,
    LexicalStore,
    RetrievalModel,
    build_retrieval_registry,
)


class HybridRetriever(ComposableRetrievalPipeline):
    """使用默认插件构建流水线，并保持原有构造方式兼容。"""

    def __init__(
        self,
        dense_store: DenseStore,
        lexical_store: LexicalStore,
        model: RetrievalModel,
        settings: Settings,
        config_loader: Callable[[], dict | None] | None = None,
        config_saver: Callable[[dict], None] | None = None,
    ):
        super().__init__(
            build_retrieval_registry(dense_store, lexical_store, model, settings),
            settings,
            config_loader,
            config_saver,
        )
