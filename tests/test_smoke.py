"""Smoke tests that don't require Qdrant, Ollama, or any external service."""
from __future__ import annotations

from pathlib import Path

from backend.config import AppConfig, load_config
from backend.rag.ingest import chunk_section, split_sections

REPO = Path(__file__).resolve().parents[1]


def test_load_config():
    cfg = load_config(REPO / "config" / "config.yaml")
    assert isinstance(cfg, AppConfig)
    assert ":" in cfg.agent.model
    assert ":" in cfg.embeddings.model
    assert cfg.vector_store.tool_name
    assert cfg.vector_store.tool_description.strip()


def test_split_sections_and_chunk():
    long_b = "Body of B with more text. " * 20
    md = ("# Top\nIntro paragraph.\n\n## Section A\nBody of A.\n\n## Section B\n") + long_b
    sections = split_sections(md)
    titles = [s.title for s in sections]
    assert "Top" in titles and "Section A" in titles and "Section B" in titles
    section_b = next(s for s in sections if s.title == "Section B")
    chunks = chunk_section(section_b, max_chars=200, overlap=20)
    assert len(chunks) > 1
    for c in chunks: assert len(c) <= 400
