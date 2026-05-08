"""Test-wide patches applied before any test imports server modules.

Stubs out ``build_agent`` so importing ``backend.server.server`` (which
calls ``create_app()`` at module load) doesn't try to construct a real
DeepAgents runnable - that path needs Qdrant + an LLM backend.
Redirects the SQLite session store to a tmp file so tests don't touch
``storage/sessions.sqlite``.
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch


class _StubAgent:
    """Drop-in async stand-in for the compiled deep-agent runnable."""

    async def astream(self, payload, stream_mode=None, config=None):
        if False:
            yield  # async generator that yields nothing

    async def ainvoke(self, payload, config=None):
        return {"messages": []}


_patch = patch("backend.rag.agent.build_agent", return_value=_StubAgent())
_patch.start()

from backend.config import get_config  # noqa: E402

_tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
_tmp.close()
get_config().sessions.sqlite_path = Path(_tmp.name)
