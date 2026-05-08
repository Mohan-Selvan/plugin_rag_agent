"""HTTP routes: SSE chat, chat history, widget bundle, demo, health (basic + ready)."""
from __future__ import annotations

import asyncio
import json
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, HumanMessage
from pydantic import BaseModel, Field
from qdrant_client import QdrantClient
from slowapi import Limiter
from sse_starlette.sse import EventSourceResponse

from ..config import AppConfig, RuntimeEnv, WidgetConfig
from ..database.db import SessionStore
from ..utils import get_logger

log = get_logger(__name__)
WIDGET_DIR = Path(__file__).resolve().parents[2] / "frontend" / "widget"

# Hidden tail format the agent emits for follow-up chips.
_SUGG_RE = re.compile(r"<suggestions>\s*(\[.*?\])\s*</suggestions>", re.DOTALL)
_SUGG_OPEN = "<suggestions>"
_SUGG_CLOSE = "</suggestions>"


class ChatRequest(BaseModel):
    """Body of POST /chat."""
    message: str = Field(..., min_length=1, max_length=4000)
    session_id: str = Field(..., min_length=8, max_length=128)


@dataclass
class StreamEvent:
    """One user-facing SSE event yielded by the agent stream wrapper."""
    type: str
    data: dict[str, Any]


def _chunk_text(chunk: Any) -> str:
    """Extract text content from a LangChain message chunk (handles list-of-parts shape)."""
    c = getattr(chunk, "content", None)
    if c is None: return ""
    if isinstance(c, str): return c
    if isinstance(c, list):
        return "".join(p.get("text", "") if isinstance(p, dict) else (p if isinstance(p, str) else "") for p in c)
    return str(c)


def _last_assistant_text(state: Any) -> str:
    """Return the most recent AIMessage text from a graph state, or empty string."""
    msgs = state.get("messages") if isinstance(state, dict) else None
    for m in reversed(msgs or []):
        if isinstance(m, AIMessage):
            t = _chunk_text(m)
            if t.strip(): return t
    return ""


def _parse_suggestions(raw: str, max_items: int) -> list[str]:
    """Parse the JSON tail emitted by the agent into a list of clean question strings."""
    try: data = json.loads(raw.strip())
    except Exception: return []
    if not isinstance(data, list): return []
    return [str(x).strip() for x in data if isinstance(x, str) and str(x).strip()][:max_items]


async def _stream_agent(agent: Any, msg: str, history: list[BaseMessage],
                        suggestions_max: int) -> AsyncIterator[StreamEvent]:
    """Yield user-facing events: token / suggestions / done / error.

    The agent is instructed to append `<suggestions>[...]</suggestions>` after its
    answer. We detect that tail in the streaming buffer, hold back its bytes from
    the client, and emit a single `suggestions` event with the parsed list.
    """
    inputs = {"messages": list(history) + [HumanMessage(content=msg)]}
    n = 0
    buf = ""
    in_sugg = False
    sugg_buf = ""
    sugg_done = False

    try:
        async for cm in agent.astream(inputs, stream_mode="messages"):
            chunk = cm[0] if isinstance(cm, tuple) else cm
            # Only stream tokens from the model itself; never forward
            # ToolMessage content (raw retrieval output) to the user.
            if not isinstance(chunk, (AIMessage, AIMessageChunk)):
                continue
            text = _chunk_text(chunk)
            if not text: continue
            buf += text
            # Walk the buffer, peeling off either visible text or suggestion content.
            while True:
                if not in_sugg:
                    i = buf.find(_SUGG_OPEN)
                    # Hold the tail bytes that might be the start of the marker.
                    safe = (len(buf) - len(_SUGG_OPEN) + 1) if i == -1 else i
                    if i == -1:
                        if safe > 0:
                            yield StreamEvent("token", {"text": buf[:safe]}); n += 1
                            buf = buf[safe:]
                        break
                    if safe > 0:
                        yield StreamEvent("token", {"text": buf[:safe]}); n += 1
                    buf = buf[safe + len(_SUGG_OPEN):]
                    in_sugg = True; sugg_buf = ""
                else:
                    j = buf.find(_SUGG_CLOSE)
                    if j == -1:
                        # Hold back the tail bytes that might be the start of the closing marker.
                        safe_close = max(0, len(buf) - len(_SUGG_CLOSE) + 1)
                        sugg_buf += buf[:safe_close]
                        buf = buf[safe_close:]
                        break
                    sugg_buf += buf[:j]
                    buf = buf[j + len(_SUGG_CLOSE):]
                    in_sugg = False
                    yield StreamEvent("suggestions",
                                      {"items": _parse_suggestions(sugg_buf, suggestions_max)})
                    sugg_done = True
                    sugg_buf = ""
    except Exception:
        log.exception("agent stream failed")
        yield StreamEvent("error", {"message": "Sorry, something went wrong."})
        yield StreamEvent("done", {}); return

    # Flush any trailing tail still in the buffer.
    if buf:
        m = _SUGG_RE.search(buf)
        if m and not sugg_done:
            pre = buf[: m.start()]
            if pre: yield StreamEvent("token", {"text": pre}); n += 1
            yield StreamEvent("suggestions",
                              {"items": _parse_suggestions(m.group(1), suggestions_max)})
            sugg_done = True
        else:
            yield StreamEvent("token", {"text": buf}); n += 1

    # Safety net: streaming returned nothing - one ainvoke retry.
    if n == 0:
        try:
            full = _last_assistant_text(await agent.ainvoke(inputs))
            m = _SUGG_RE.search(full or "")
            body = (full[: m.start()].rstrip() if m else (full or "")).strip()
            yield StreamEvent("token", {"text": body or "Sorry, I couldn't produce a response. Please try again."})
            if m and not sugg_done:
                yield StreamEvent("suggestions",
                                  {"items": _parse_suggestions(m.group(1), suggestions_max)})
        except Exception:
            yield StreamEvent("error", {"message": "Sorry, something went wrong."})

    yield StreamEvent("done", {})


## Color theme presets - host sites embedding the widget can pick one via
## ?theme=<name> on /widget.js, or override individual slots with
## ?primary=<hex>&secondary=<hex>&tertiary=<hex>.
THEMES: dict[str, dict[str, str]] = {
    "indigo":  {"primary": "#6366f1", "secondary": "#4338ca", "tertiary": "#a78bfa"},
    "emerald": {"primary": "#10b981", "secondary": "#047857", "tertiary": "#6ee7b7"},
    "rose":    {"primary": "#f43f5e", "secondary": "#be123c", "tertiary": "#fb7185"},
    "amber":   {"primary": "#f59e0b", "secondary": "#b45309", "tertiary": "#fcd34d"},
    "ocean":   {"primary": "#0ea5e9", "secondary": "#0369a1", "tertiary": "#7dd3fc"},
    "slate":   {"primary": "#475569", "secondary": "#1e293b", "tertiary": "#94a3b8"},
}
_HEX_COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def _safe_hex(value: str | None) -> str | None:
    """Return value if it's a valid #RGB or #RRGGBB hex color, else None."""
    return value if value and _HEX_COLOR_RE.match(value) else None


def _resolve_colors(w: WidgetConfig, *, theme: str | None, primary: str | None,
                    secondary: str | None, tertiary: str | None) -> tuple[str, str | None, str | None]:
    """Resolve final (primary, secondary, tertiary). Theme replaces all three;
    explicit hex overrides individual slots. Bad values are ignored so a
    typo in the embed code can't break the widget."""
    p, s, t = w.primary_color, w.secondary_color, w.tertiary_color
    if theme and theme in THEMES:
        scheme = THEMES[theme]
        p, s, t = scheme["primary"], scheme["secondary"], scheme["tertiary"]
    p, s, t = (_safe_hex(primary) or p, _safe_hex(secondary) or s, _safe_hex(tertiary) or t)
    return p, s, t


def _settings_payload(w: WidgetConfig, *, theme: str | None = None,
                      primary: str | None = None, secondary: str | None = None,
                      tertiary: str | None = None) -> str:
    """Serialise widget settings as JS-safe JSON (ASCII-escaped + </ neutralised)."""
    p, s, t = _resolve_colors(w, theme=theme, primary=primary, secondary=secondary, tertiary=tertiary)
    raw = json.dumps({"title": w.title, "subtitle": w.subtitle, "greeting": w.greeting,
                      "primaryColor": p, "secondaryColor": s, "tertiaryColor": t,
                      "position": w.position, "starterQuestions": w.starter_questions})
    return raw.replace("</", "<\\/")


def build_router(agent: Any, sessions: SessionStore, cfg: AppConfig, env: RuntimeEnv,
                 limiter: Limiter) -> APIRouter:
    """Construct the single APIRouter holding every HTTP route."""
    r = APIRouter()
    widget_js = WIDGET_DIR / "widget.js"
    chat_ui = WIDGET_DIR / "chat-ui.html"
    demo_html = WIDGET_DIR / "demo.html"
    history_window = cfg.sessions.history_window
    suggestions_max = cfg.suggestions.max_items if cfg.suggestions.enabled else 0

    @r.get("/health")
    async def health() -> JSONResponse:
        """Liveness check (cheap, no upstream probes)."""
        return JSONResponse({"status": "ok"})

    @r.get("/health/ready")
    async def ready() -> JSONResponse:
        """Readiness check - probes Qdrant and Ollama (when configured)."""
        checks: dict[str, str] = {}
        try:
            QdrantClient(url=env.qdrant_url, timeout=2).get_collections()
            checks["qdrant"] = "ok"
        except Exception as e:
            checks["qdrant"] = f"unreachable: {type(e).__name__}"
        if any(m.startswith("ollama:") for m in (cfg.agent.model, cfg.embeddings.model, cfg.ingestion.model)):
            try:
                async with httpx.AsyncClient(timeout=2.0) as client:
                    resp = await client.get(env.ollama_host.rstrip("/") + "/api/tags")
                checks["ollama"] = "ok" if resp.status_code == 200 else f"unhealthy: {resp.status_code}"
            except Exception as e:
                checks["ollama"] = f"unreachable: {type(e).__name__}"
        ok = all(v == "ok" for v in checks.values())
        return JSONResponse({"ready": ok, "checks": checks}, status_code=200 if ok else 503)

    @r.get("/widget.js")
    async def widget_bundle(
        theme: str | None = Query(None, max_length=32),
        primary: str | None = Query(None, max_length=9),
        secondary: str | None = Query(None, max_length=9),
        tertiary: str | None = Query(None, max_length=9),
    ) -> Response:
        if not widget_js.exists(): return PlainTextResponse("widget bundle missing", status_code=500)
        body = widget_js.read_text(encoding="utf-8")
        settings = _settings_payload(cfg.widget, theme=theme, primary=primary,
                                      secondary=secondary, tertiary=tertiary)
        prefix = f"window.__PLUGIN_RAG_SETTINGS__ = {settings};\n"
        return Response(content=prefix + body,
                        media_type="application/javascript; charset=utf-8",
                        headers={"Cache-Control": "no-cache"})

    @r.get("/widget/settings")
    async def widget_settings(
        theme: str | None = Query(None, max_length=32),
        primary: str | None = Query(None, max_length=9),
        secondary: str | None = Query(None, max_length=9),
        tertiary: str | None = Query(None, max_length=9),
    ) -> JSONResponse:
        return JSONResponse(json.loads(_settings_payload(cfg.widget, theme=theme,
                                                          primary=primary, secondary=secondary,
                                                          tertiary=tertiary)))

    @r.get("/chat-ui", response_class=HTMLResponse)
    async def chat_ui_page() -> HTMLResponse:
        return (HTMLResponse(chat_ui.read_text(encoding="utf-8")) if chat_ui.exists()
                else HTMLResponse("<h1>chat-ui.html missing</h1>", status_code=500))

    @r.get("/demo", response_class=HTMLResponse)
    async def demo_page() -> HTMLResponse:
        return (HTMLResponse(demo_html.read_text(encoding="utf-8")) if demo_html.exists()
                else HTMLResponse("<h1>demo.html missing</h1>", status_code=500))

    @r.get("/chat/history")
    async def history(session_id: str = Query(..., min_length=8, max_length=128),
                      limit: int = Query(50, ge=1, le=200)):
        msgs = await sessions.recent(session_id, limit)
        return {"session_id": session_id, "messages": [
            {"role": "user" if isinstance(m, HumanMessage) else "assistant",
             "content": m.content if isinstance(m.content, str) else str(m.content)}
            for m in msgs if isinstance(m, (HumanMessage, AIMessage))]}

    rate_str = f"{cfg.rate_limit.requests_per_minute}/minute"

    @r.post("/chat")
    @limiter.limit(rate_str)
    async def chat(request: Request, payload: ChatRequest):
        await sessions.touch(payload.session_id)
        history = await sessions.recent(payload.session_id, history_window)
        await sessions.append(payload.session_id, "user", payload.message)

        async def event_source() -> AsyncIterator[dict]:
            parts: list[str] = []
            try:
                async for ev in _stream_agent(agent, payload.message, history, suggestions_max):
                    if ev.type == "token": parts.append(ev.data.get("text", ""))
                    yield {"event": ev.type, "data": json.dumps(ev.data, ensure_ascii=False)}
                    if ev.type == "done": break
                    if await request.is_disconnected():
                        log.info("client disconnected session=%s", payload.session_id[:8]); break
            except asyncio.CancelledError: raise
            except Exception:
                log.exception("error during /chat stream")
                yield {"event": "error", "data": json.dumps({"message": "Unexpected error."})}
                yield {"event": "done", "data": "{}"}
            finally:
                full = "".join(parts).strip()
                if full:
                    try: await sessions.append(payload.session_id, "assistant", full)
                    except Exception: log.exception("failed to persist assistant message")

        return EventSourceResponse(event_source(), ping=15,
            headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache, no-transform"})

    return r
