"""One-shot analyst agents for studio features (Ask AI, analysis cells).

Runs prompts through GenXAI's AgentRuntime rather than raw LLM calls, so
these paths share the framework's provider handling, token accounting, and
observability — and can later gain memory or tools by flipping runtime
flags instead of rewriting call sites.
"""

from __future__ import annotations

import uuid
from typing import Any

DEFAULT_TEMPERATURE = 0.2  # analytical tasks want determinism over flair


async def run_analyst(
    task: str,
    *,
    role: str = "Data Analyst",
    goal: str = "Answer questions strictly from the data provided",
    backstory: str = "You are a careful, concrete data analyst.",
    model: str | None = None,
    temperature: float = DEFAULT_TEMPERATURE,
) -> dict[str, Any]:
    """Execute a one-shot analyst agent; returns {"output", "model"}.

    Raises RuntimeError when no LLM API key is configured (callers map
    this to HTTP 409).
    """
    from app.generation import DEFAULT_GENERATION_MODEL, _resolve_model_and_key
    from genxai.core.agent.base import AgentFactory
    from genxai.core.agent.runtime import AgentRuntime

    resolved_model, api_key = _resolve_model_and_key(
        model or DEFAULT_GENERATION_MODEL
    )
    if api_key is None:
        raise RuntimeError(
            "No LLM API key configured — set OPENAI_API_KEY or ANTHROPIC_API_KEY"
        )

    agent = AgentFactory.create_agent(
        id=f"studio-analyst-{uuid.uuid4().hex[:8]}",
        role=role,
        goal=goal,
        backstory=backstory,
        llm_model=resolved_model,
        llm_temperature=temperature,
    )
    is_anthropic = "claude" in resolved_model.lower()
    runtime = AgentRuntime(
        agent=agent,
        anthropic_api_key=api_key if is_anthropic else None,
        openai_api_key=None if is_anthropic else api_key,
        # One-shot analytical calls: stateless by design (no cross-question
        # contamination). Flip when analyses want longitudinal memory.
        enable_memory=False,
    )
    result = await runtime.execute(task)
    return {"output": str(result.get("output") or ""), "model": resolved_model}
