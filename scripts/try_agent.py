"""Manual end-to-end check.

Sends a small-talk message and a knowledge-base question to the agent and
prints the final response + the sequence of message types so you can see
whether the retriever tool was called.

Usage:
    python -m scripts.try_agent
    python -m scripts.try_agent "your custom question"
"""
from __future__ import annotations

import sys

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from backend.config import get_config, get_env
from backend.rag.agent import build_agent
from backend.utils import init_default

DEFAULT_PROMPTS = ["Hi there!", "Tell me what you can help with."]


def _summarise(state):
    msgs = state.get("messages", [])
    types = [type(m).__name__ for m in msgs]
    tool_names: list[str] = []
    final = ""
    for m in msgs:
        if isinstance(m, AIMessage) and getattr(m, "tool_calls", None):
            for tc in m.tool_calls:
                name = tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", "")
                if name: tool_names.append(name)
        if isinstance(m, AIMessage):
            content = m.content if isinstance(m.content, str) else str(m.content)
            if content.strip(): final = content
        if isinstance(m, ToolMessage):
            pass
    return types, tool_names, final


def main() -> int:
    init_default()
    agent = build_agent(get_config(), get_env())
    prompts = sys.argv[1:] or DEFAULT_PROMPTS
    for q in prompts:
        print("=" * 72)
        print(f"USER: {q}")
        state = agent.invoke({"messages": [HumanMessage(content=q)]})
        types, tools, final = _summarise(state)
        print(f"messages: {types}")
        print(f"tools called: {tools or '(none)'}")
        print(f"ASSISTANT: {final}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
