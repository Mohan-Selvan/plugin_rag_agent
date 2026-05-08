"""Deep-agent factory: chat model + retriever tool + logging middleware."""
from __future__ import annotations

from typing import Any

from deepagents import create_deep_agent
from langchain.chat_models import init_chat_model

from ..config import AppConfig, RuntimeEnv
from ..utils import get_logger
from .middlewares import AgentLoggingMiddleware
from .retriever import build_retriever_tool

log = get_logger(__name__)


_SUGGESTIONS_INSTRUCTION = """

OUTPUT FORMAT (mandatory, applies to EVERY single reply with no exceptions):

End every response with a follow-up suggestions block. The format is:

<suggestions>[...]</suggestions>

The block contains a JSON array of up to {n} short follow-up question
strings (each under 8 words). The questions must be specific and
answerable from the knowledge base. If no good follow-ups exist, output
exactly: <suggestions>[]</suggestions>

The block must come AFTER your final answer, on its own line. Never skip
it, never reference it in your answer, and never mention its existence to
the user. The system strips it before the user sees the message.

Examples (illustrate the format only; pick follow-ups that fit the
actual answer and the knowledge base):

User: How do I get started?
Assistant: Here is a quick overview... (your real answer goes here).
<suggestions>["What is included?", "How do I sign up?"]</suggestions>

User: Hi there!
Assistant: Hello! How can I help today?
<suggestions>["What can you help with?", "Tell me about your services"]</suggestions>
"""


def _system_prompt(cfg: AppConfig) -> str:
    """Compose the final system prompt: user prompt + (optional) suggestions tail rule."""
    base = cfg.agent.system_prompt.rstrip()
    if cfg.suggestions.enabled and cfg.suggestions.max_items > 0:
        return base + _SUGGESTIONS_INSTRUCTION.format(n=cfg.suggestions.max_items)
    return base


def build_agent(cfg: AppConfig, env: RuntimeEnv) -> Any:
    """Build the deep agent from config and return the compiled LangGraph runnable."""
    model = init_chat_model(cfg.agent.model, temperature=cfg.agent.temperature,
                            **cfg.agent.model_kwargs)
    tool = build_retriever_tool(cfg, env)
    log.info("Building deep agent: model=%s tool=%s suggestions=%s",
             cfg.agent.model, tool.name, cfg.suggestions.enabled)
    return create_deep_agent(model=model, tools=[tool], system_prompt=_system_prompt(cfg),
                             middleware=[AgentLoggingMiddleware()])
