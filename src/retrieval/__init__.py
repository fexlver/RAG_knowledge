"""检索与融合。"""
from .pipeline import (
    ComposableRetrievalPipeline,
    RetrievalPipelineConfig,
    RetrievalPluginDescriptor,
    RetrievalPluginRegistry,
)

__all__ = [
    "ComposableRetrievalPipeline",
    "RetrievalPipelineConfig",
    "RetrievalPluginDescriptor",
    "RetrievalPluginRegistry",
]
