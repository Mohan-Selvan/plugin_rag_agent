"""Qdrant-backed retriever wrapped as the agent's single search tool."""
from __future__ import annotations

from typing import Any

from langchain.embeddings import init_embeddings
from langchain_core.callbacks import CallbackManagerForRetrieverRun
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from langchain_core.tools import StructuredTool
from langchain_qdrant import QdrantVectorStore
from pydantic import BaseModel, ConfigDict, Field

from ..config import AppConfig, EmbeddingsConfig, RuntimeEnv, VectorStoreConfig
from ..utils import get_logger

log = get_logger(__name__)


class _SearchInput(BaseModel):
    """Schema for the retriever tool's single argument."""
    query: str = Field(..., description="The user's question, rephrased as a focused search query.")


class QdrantVectorRetriever(BaseRetriever):
    """LangChain retriever returning top-k chunks from the configured Qdrant collection."""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    top_k: int = 5
    score_threshold: float | None = None
    store: Any = Field(...)

    @classmethod
    def from_config(cls, vs: VectorStoreConfig, emb: EmbeddingsConfig, env: RuntimeEnv) -> "QdrantVectorRetriever":
        """Build retriever from config: connect to Qdrant + init embeddings."""
        store = QdrantVectorStore.from_existing_collection(
            embedding=init_embeddings(emb.model), collection_name=vs.collection, url=env.qdrant_url)
        return cls(top_k=vs.top_k, score_threshold=vs.score_threshold, store=store)

    def _get_relevant_documents(self, query: str, *, run_manager: CallbackManagerForRetrieverRun) -> list[Document]:
        try:
            hits = self.store.similarity_search_with_score(
                query=query, k=self.top_k, score_threshold=self.score_threshold)
        except Exception:
            log.exception("Qdrant search failed"); return []
        docs = [Document(page_content=d.page_content,
                          metadata={**(d.metadata or {}), "score": float(s) if s is not None else None})
                for d, s in hits]
        log.info("Retriever returned %d hits for query=%r", len(docs), query[:80])
        return docs


def _format_docs(docs: list[Document]) -> str:
    """Render retrieved chunks as plain text the agent can quote."""
    if not docs: return "No relevant information was found in the knowledge base."
    out: list[str] = []
    for i, d in enumerate(docs, 1):
        m = d.metadata or {}
        crumb = m.get("breadcrumb") or m.get("title") or ""
        out.append(f"[{i}] source={m.get('source','unknown')}" + (f" | section={crumb}" if crumb else ""))
        out.append(d.page_content.strip()); out.append("")
    return "\n".join(out).strip()


def build_retriever_tool(cfg: AppConfig, env: RuntimeEnv) -> StructuredTool:
    """Wrap the configured retriever as a StructuredTool with name + description from config."""
    retriever = QdrantVectorRetriever.from_config(cfg.vector_store, cfg.embeddings, env)
    name = cfg.vector_store.tool_name
    def _search(query: str) -> str:
        try: return _format_docs(retriever.invoke(query))
        except Exception:
            log.exception("Retriever %s failed", name)
            return "The knowledge base lookup failed."
    return StructuredTool.from_function(
        name=name, description=cfg.vector_store.tool_description.strip(),
        args_schema=_SearchInput, func=_search)
