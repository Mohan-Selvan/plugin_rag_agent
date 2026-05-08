"""Markdown -> chunks -> Qdrant ingestion CLI."""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from langchain.chat_models import init_chat_model
from langchain.embeddings import init_embeddings
from langchain_core.documents import Document
from langchain_qdrant import QdrantVectorStore
from qdrant_client import QdrantClient

from ..config import get_config, get_env
from ..utils import get_logger, init_default

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

log = get_logger(__name__)

_HEADER_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_SENT_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_STRUCTURE_PROMPT = """You restructure markdown documents to make them easier
for downstream chunking. Rewrite the input as well-formed markdown with a clear
H1 title, H2 section headings, and H3 subsection headings where appropriate.

Rules:
- Preserve every fact, code block, link, and list from the original.
- Do not add commentary, summaries, or new information.
- Do not wrap output in code fences.
- Output only the restructured markdown.
"""


@dataclass(frozen=True)
class MarkdownDoc:
    """A loaded markdown file with its title and raw content."""
    path: Path
    title: str
    content: str


@dataclass
class Section:
    """A heading-bounded slice of a markdown document with its breadcrumb."""
    title: str
    level: int
    breadcrumb: str
    text: str


def discover_markdowns(folder: Path) -> list[Path]:
    """Return all .md paths under `folder`, sorted."""
    if not folder.exists(): raise FileNotFoundError(f"Knowledge base folder not found: {folder}")
    paths = sorted(p for p in folder.rglob("*.md") if p.is_file())
    log.info("Discovered %d markdown files under %s", len(paths), folder)
    return paths


def load_markdown(path: Path) -> MarkdownDoc:
    """Load a markdown file; title is the first H1 line or the filename stem."""
    text = path.read_text(encoding="utf-8")
    title = next((l.strip()[2:].strip() for l in text.splitlines() if l.strip().startswith("# ")), path.stem)
    return MarkdownDoc(path=path, title=title, content=text)


def restructure_markdown(llm: "BaseChatModel", doc: MarkdownDoc) -> str:
    """Have the ingestion LLM rewrite the doc with clean H2/H3 sections."""
    from langchain_core.messages import HumanMessage, SystemMessage
    log.info("Restructuring %s (%d chars)", doc.path.name, len(doc.content))
    msg = llm.invoke([SystemMessage(content=_STRUCTURE_PROMPT), HumanMessage(content=doc.content)])
    out = (msg.content if isinstance(msg.content, str) else str(msg.content)).strip()
    if not out:
        log.warning("LLM returned empty restructured doc for %s; using original", doc.path); return doc.content
    return out


def split_sections(md: str) -> list[Section]:
    """Split markdown on heading lines, tracking the breadcrumb of ancestor headings."""
    lines = md.splitlines(keepends=True)
    sections: list[Section] = []
    stack: list[tuple[int, str]] = []
    buf: list[str] = []
    cur_title = ""; cur_level = 0
    def _flush() -> None:
        body = "".join(buf).strip()
        if not body and not cur_title: return
        sections.append(Section(title=cur_title, level=cur_level,
                                 breadcrumb=" > ".join(t for _, t in stack if t), text=body))
    for line in lines:
        m = _HEADER_RE.match(line.rstrip("\n"))
        if m:
            _flush(); buf = [line]
            cur_level = len(m.group(1)); cur_title = m.group(2).strip()
            while stack and stack[-1][0] >= cur_level: stack.pop()
            stack.append((cur_level, cur_title))
        else:
            buf.append(line)
    _flush()
    return sections


def _split_long_paragraph(para: str, max_chars: int) -> list[str]:
    """Fallback splitter for paragraphs longer than max_chars (sentence then char boundaries)."""
    sents = _SENT_SPLIT_RE.split(para)
    chunks: list[str] = []; cur = ""
    for s in sents:
        if not s: continue
        if len(s) > max_chars:
            if cur: chunks.append(cur.strip()); cur = ""
            chunks.extend(s[i:i+max_chars] for i in range(0, len(s), max_chars))
            continue
        if len(cur) + len(s) + 1 > max_chars and cur:
            chunks.append(cur.strip()); cur = s
        else:
            cur = (cur + " " + s).strip() if cur else s
    if cur: chunks.append(cur.strip())
    return chunks


def chunk_section(section: Section, max_chars: int, overlap: int) -> list[str]:
    """Split a section into <= max_chars chunks on paragraph (then sentence) boundaries."""
    if len(section.text) <= max_chars: return [section.text]
    units: list[str] = []
    for para in re.split(r"\n\s*\n", section.text):
        para = para.strip()
        if not para: continue
        units.extend([para] if len(para) <= max_chars else _split_long_paragraph(para, max_chars))
    chunks: list[str] = []; cur: list[str] = []; cur_len = 0
    for u in units:
        if cur_len + len(u) + 2 > max_chars and cur:
            chunks.append("\n\n".join(cur))
            if overlap > 0 and cur:
                tail = cur[-1][-overlap:]; cur = [tail]; cur_len = len(tail)
            else:
                cur = []; cur_len = 0
        cur.append(u); cur_len += len(u) + 2
    if cur: chunks.append("\n\n".join(cur))
    return chunks


def run(restructure: bool | None = None, recreate: bool = False) -> int:
    """Discover markdowns, optionally restructure, chunk, embed, upsert into Qdrant."""
    cfg = get_config(); env = get_env(); init_default()
    do_restructure = cfg.ingestion.restructure if restructure is None else restructure
    paths = discover_markdowns(cfg.knowledge_base.data_folder)
    if not paths: log.error("No markdown files found"); return 1

    embeddings = init_embeddings(cfg.embeddings.model)
    llm = (init_chat_model(cfg.ingestion.model, temperature=cfg.ingestion.temperature,
                           **cfg.ingestion.model_kwargs) if do_restructure else None)

    collection = cfg.vector_store.collection
    client = QdrantClient(url=env.qdrant_url)
    if recreate and client.collection_exists(collection):
        log.warning("Recreating collection %s", collection); client.delete_collection(collection)

    documents: list[Document] = []
    for p in paths:
        try: doc = load_markdown(p)
        except Exception: log.exception("Failed to load %s", p); continue
        try: structured = restructure_markdown(llm, doc) if llm else doc.content
        except Exception: log.exception("Restructure failed; using original"); structured = doc.content
        for s in split_sections(structured) or split_sections(doc.content):
            for chunk in chunk_section(s, cfg.ingestion.max_chunk_chars, cfg.ingestion.chunk_overlap_chars):
                documents.append(Document(page_content=chunk, metadata={
                    "source": str(p.relative_to(cfg.knowledge_base.data_folder)),
                    "title": doc.title, "section": s.title, "breadcrumb": s.breadcrumb}))

    if not documents: log.error("No chunks produced; aborting."); return 1
    log.info("Prepared %d chunks; adding to %s ...", len(documents), collection)

    if client.collection_exists(collection):
        QdrantVectorStore.from_existing_collection(
            embedding=embeddings, collection_name=collection, url=env.qdrant_url
        ).add_documents(documents)
    else:
        QdrantVectorStore.from_documents(
            documents=documents, embedding=embeddings, url=env.qdrant_url, collection_name=collection)

    log.info("Ingestion complete: %d chunks in %s", len(documents), collection)
    return 0


def main() -> None:
    """CLI entry: python -m backend.rag.ingest [--no-restructure] [--recreate]"""
    p = argparse.ArgumentParser(description="Vector-RAG ingestion")
    p.add_argument("--no-restructure", action="store_true", help="Skip LLM-based markdown restructuring")
    p.add_argument("--recreate", action="store_true", help="Drop and recreate the Qdrant collection before ingesting")
    a = p.parse_args()
    sys.exit(run(restructure=False if a.no_restructure else None, recreate=a.recreate))


if __name__ == "__main__":
    main()
