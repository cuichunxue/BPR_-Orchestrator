"""A specialized-agent base class backed by the Claude API.

Concrete agents (Process Agent, Evidence Agent, Root Cause Agent, ...)
subclass this and only need to supply their own `system_prompt` describing
their specialty — the AGENT_CONTRACT_PROMPT enforcing the standardized
response envelope is appended automatically.
"""
from __future__ import annotations

import json
import os

from bpr_orchestrator.contracts import AgentResponse, AgentTask, BaseAgent
from bpr_orchestrator.models import TransformationCase
from bpr_orchestrator.prompts import AGENT_CONTRACT_PROMPT


class AnthropicAgent(BaseAgent):
    """Wraps the Anthropic Messages API. Requires the `anthropic` package
    and an ANTHROPIC_API_KEY in the environment (or an explicit api_key)."""

    system_prompt: str = "You are a specialized business-process analysis agent."
    model: str = "claude-sonnet-5"

    def __init__(self, api_key: str | None = None, model: str | None = None):
        try:
            import anthropic  # noqa: F401
        except ImportError as exc:
            raise ImportError(
                "AnthropicAgent requires the 'anthropic' package. "
                "Install it with: pip install anthropic"
            ) from exc
        self._anthropic = anthropic
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not self._api_key:
            raise ValueError(
                "No API key provided and ANTHROPIC_API_KEY is not set"
            )
        if model:
            self.model = model
        self._client = anthropic.Anthropic(api_key=self._api_key)

    def build_prompt(self, task: AgentTask, case: TransformationCase) -> str:
        return (
            f"Task: {task.instruction}\n\n"
            f"Case objective: {case.objective}\n"
            f"Case scope: {case.scope}\n"
            f"Relevant context: {task.context}\n"
        )

    def run(self, task: AgentTask, case: TransformationCase) -> AgentResponse:
        full_system = f"{self.system_prompt}\n\n{AGENT_CONTRACT_PROMPT}"
        message = self._client.messages.create(
            model=self.model,
            max_tokens=1500,
            system=full_system,
            messages=[{"role": "user", "content": self.build_prompt(task, case)}],
        )
        text = "".join(
            block.text for block in message.content if getattr(block, "type", "") == "text"
        )
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"agent '{self.name}' did not return valid JSON: {text[:500]}"
            ) from exc
        return AgentResponse(**payload)
