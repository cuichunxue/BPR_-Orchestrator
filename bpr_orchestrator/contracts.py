"""The contract every specialized agent must honor.

Design section 10: an agent is never allowed to return a bare finding
("平均処理時間が4.2日"). It must return the full envelope below, and the
Orchestrator treats a response missing required fields as invalid rather
than silently accepting it.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from pydantic import BaseModel, Field

from bpr_orchestrator.models import TransformationCase


class AgentTask(BaseModel):
    """A unit of work the Orchestrator hands to a specialized agent."""

    task_id: str
    agent_name: str
    instruction: str
    problem_id: Optional[str] = None
    hypothesis_id: Optional[str] = None
    context: dict = Field(default_factory=dict)


class AgentResponse(BaseModel):
    agent: str
    task: str
    finding: str
    evidence: list[str] = Field(default_factory=list)   # evidence_ids referenced or newly created
    confidence: str = "unknown"
    assumptions: list[str] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    recommended_next_action: str = ""
    human_decision_required: bool = False
    artifact: Optional[str] = None        # non-JSON payload, e.g. generated BPMN XML
    artifact_type: Optional[str] = None   # e.g. "bpmn_xml"

    def is_well_formed(self) -> tuple[bool, list[str]]:
        """Reject vague, under-specified findings before they ever reach
        the case state. Mirrors the 'Data Agent must not just say 4.2 days'
        rule — a finding must be accompanied by assumptions or evidence,
        not asserted bare."""
        problems: list[str] = []
        if not self.finding.strip():
            problems.append("finding is empty")
        if not self.evidence and not self.assumptions:
            problems.append(
                "finding has neither evidence nor stated assumptions "
                "(bare assertions are not accepted)"
            )
        if not self.recommended_next_action.strip():
            problems.append("recommended_next_action is required")
        return (len(problems) == 0, problems)


class BaseAgent(ABC):
    """Specialized agents (Process, Evidence, Root Cause, BPR Design,
    Value, Data, Automation, Risk, Implementation, Change, ...) implement
    this. The Orchestrator never performs their analysis itself — it only
    dispatches AgentTask and integrates the AgentResponse."""

    name: str = "base_agent"
    description: str = ""

    @abstractmethod
    def run(self, task: AgentTask, case: TransformationCase) -> AgentResponse:
        ...
