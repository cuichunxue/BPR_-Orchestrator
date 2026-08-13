"""Deterministic stand-ins for the specialized agents.

These do not call any LLM. They exist so the orchestration engine (state
machine, gates, contradiction detection, learning loop, ...) can be
exercised end-to-end in tests and in examples/run_demo.py without needing
an ANTHROPIC_API_KEY. A real deployment would replace these with
AnthropicAgent subclasses (see agents/base.py) that actually call the
domain-specific analysis.
"""
from __future__ import annotations

from bpr_orchestrator.contracts import AgentResponse, AgentTask, BaseAgent
from bpr_orchestrator.models import (
    CauseLevel,
    ConfidenceState,
    Evidence,
    EvidenceQuality,
    EvidenceSourceType,
    Hypothesis,
    TransformationCase,
)


class MockProcessAgent(BaseAgent):
    name = "process_agent"
    description = "Describes the current (As-Is) process."

    def run(self, task: AgentTask, case: TransformationCase) -> AgentResponse:
        return AgentResponse(
            agent=self.name,
            task=task.instruction,
            finding=(
                "As-Is process mapped: 6 steps, 3 handoffs, 2 approval gates; "
                "no single system of record identified"
            ),
            assumptions=["process walkthrough covered the last 90 days only"],
            recommended_next_action="quantify cycle time per step with Data Agent",
        )


class MockDataAgent(BaseAgent):
    name = "data_agent"
    description = (
        "Returns fully-qualified quantitative findings — never a bare number."
    )

    def run(self, task: AgentTask, case: TransformationCase) -> AgentResponse:
        evidence = Evidence(
            source="ticketing_system_export_2026Q2",
            source_type=EvidenceSourceType.DATA,
            sample_size=482,
            freshness="last 90 days",
            bias_risk="low (system-generated timestamps)",
            reproducibility="high (re-runnable query)",
            quality=EvidenceQuality.HIGH,
            limitations=[
                "excludes tickets reopened more than once",
                "12 records had null approval_at and were dropped",
            ],
            raw_finding=(
                "mean processing time 4.2 days (n=482, 2026-04-01..2026-06-30); "
                "median 3.1 days; p95 11.4 days; 38% of cases pass through 2+ "
                "approval steps"
            ),
        )
        if task.hypothesis_id:
            evidence.supports = [task.hypothesis_id]
            hyp = case.hypotheses.get(task.hypothesis_id)
            if hyp is not None:
                eid = case.add(evidence)
                hyp.evidence_for.append(eid)
                hyp.evidence_quality = EvidenceQuality.HIGH
                hyp.confidence_state = ConfidenceState.SUPPORTED
        else:
            case.add(evidence)

        return AgentResponse(
            agent=self.name,
            task=task.instruction,
            finding=evidence.raw_finding,
            evidence=[evidence.evidence_id],
            confidence="high (n=482, system log, low bias risk)",
            recommended_next_action="cross-check against interview evidence for convergence",
        )


class MockRootCauseAgent(BaseAgent):
    name = "root_cause_agent"
    description = "Proposes structured cause hypotheses for a problem."

    def run(self, task: AgentTask, case: TransformationCase) -> AgentResponse:
        if not task.problem_id:
            raise ValueError("root_cause_agent requires task.problem_id")
        hypothesis = Hypothesis(
            problem_id=task.problem_id,
            cause_candidate=task.instruction,
            cause_level=CauseLevel.STRUCTURAL,
            next_verification="pull 90 days of approval-step timestamps",
        )
        hid = case.add(hypothesis)
        return AgentResponse(
            agent=self.name,
            task=task.instruction,
            finding=f"candidate structural cause proposed: {task.instruction}",
            assumptions=["based on process walkthrough interviews only, not yet data-verified"],
            recommended_next_action=f"verify hypothesis {hid} with Data Agent before treating as confirmed",
        )


class MockAutomationAgent(BaseAgent):
    name = "automation_agent"
    description = (
        "Compares technology-neutral implementation options for an already "
        "redesigned To-Be process. Only ever invoked after redesign, never before."
    )

    def run(self, task: AgentTask, case: TransformationCase) -> AgentResponse:
        return AgentResponse(
            agent=self.name,
            task=task.instruction,
            finding=(
                "for the redesigned To-Be process, a rules engine + existing "
                "SaaS approval workflow covers 90% of volume; RPA/LLM not required"
            ),
            assumptions=["To-Be process was already simplified before this comparison"],
            recommended_next_action="pilot with the rules engine option on lowest-risk segment",
        )
