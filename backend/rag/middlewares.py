"""Agent middlewares (logging only for now)."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from ..utils import get_logger

log = get_logger("agent")
_MAX = 200


def _preview(text: Any, n: int = _MAX) -> str:
    """Return text truncated and single-lined for log output."""
    s = (text if isinstance(text, str) else str(text)).replace("\n", " ").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def _log_before(state: dict) -> None:
    for m in reversed(state.get("messages", [])):
        if isinstance(m, HumanMessage):
            log.info("user -> agent: %s", _preview(m.content)); return


def _log_after(state: dict) -> None:
    msgs = state.get("messages", [])
    if not msgs: return
    last = msgs[-1]
    if not isinstance(last, AIMessage): return
    tool_calls = getattr(last, "tool_calls", None) or []
    if tool_calls:
        names = [tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", "?") for tc in tool_calls]
        log.info("agent -> tools: %s", names)
    text = last.content if isinstance(last.content, str) else str(last.content)
    if text.strip(): log.info("agent -> user: %s", _preview(text))


def _tool_meta(req: Any) -> tuple[str, Any]:
    tc = req.tool_call
    name = tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", "?")
    args = tc.get("args") if isinstance(tc, dict) else getattr(tc, "args", {})
    return name, args


def _log_tool_result(name: str, result: Any) -> None:
    if isinstance(result, ToolMessage):
        log.info("tool end:   %s -> %s", name, _preview(result.content))
    else:
        log.info("tool end:   %s (Command)", name)


class AgentLoggingMiddleware(AgentMiddleware):
    """Logs every model invocation, tool call, and tool result (sync + async)."""

    def before_model(self, state: dict, runtime: Any) -> dict | None:
        _log_before(state); return None

    def after_model(self, state: dict, runtime: Any) -> dict | None:
        _log_after(state); return None

    def wrap_tool_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        name, args = _tool_meta(request)
        log.info("tool start: %s args=%s", name, _preview(args))
        try: result = handler(request)
        except Exception as e: log.exception("tool error: %s -> %s", name, e); raise
        _log_tool_result(name, result)
        return result

    async def abefore_model(self, state: dict, runtime: Any) -> dict | None:
        _log_before(state); return None

    async def aafter_model(self, state: dict, runtime: Any) -> dict | None:
        _log_after(state); return None

    async def awrap_tool_call(self, request: Any, handler: Callable[[Any], Awaitable[Any]]) -> Any:
        name, args = _tool_meta(request)
        log.info("tool start: %s args=%s", name, _preview(args))
        try: result = await handler(request)
        except Exception as e: log.exception("tool error: %s -> %s", name, e); raise
        _log_tool_result(name, result)
        return result
