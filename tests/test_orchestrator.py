import pytest

from bpr_orchestrator.contracts import AgentResponse, AgentTask, BaseAgent
from bpr_orchestrator.models import (
    ConfidenceState,
    Decision,
    Evidence,
    EvidenceQuality,
    EvidenceSourceType,
    Hypothesis,
    Initiative,
    InitiativeStatus,
    InterventionType,
    Problem,
    Reversibility,
    RiskItem,
    TransformationCase,
)
from bpr_orchestrator.opportunity import OpportunityFactors
from bpr_orchestrator.orchestrator import BPROrchestrator, OrchestratorError


class BareFindingAgent(BaseAgent):
    name = "bare_agent"

    def run(self, task: AgentTask, case: TransformationCase) -> AgentResponse:
        return AgentResponse(
            agent=self.name,
            task=task.instruction,
            finding="approvals are slow",  # no evidence, no assumptions
            recommended_next_action="",     # also missing
        )


def _case_with_confirmed_hypothesis():
    case = TransformationCase(objective="reduce cost", scope="finance")
    problem = Problem(symptom="slow approvals")
    case.add(problem)
    hyp = Hypothesis(
        problem_id=problem.problem_id,
        cause_candidate="double approval",
        confidence_state=ConfidenceState.SUPPORTED,
    )
    ev = Evidence(
        source="log", source_type=EvidenceSourceType.DATA, quality=EvidenceQuality.HIGH
    )
    case.add(ev)
    hyp.evidence_for.append(ev.evidence_id)
    case.add(hyp)
    return case, problem, hyp


def test_dispatch_rejects_malformed_response():
    case = TransformationCase(objective="x", scope="y")
    orch = BPROrchestrator(case, {"bare_agent": BareFindingAgent()})
    with pytest.raises(OrchestratorError):
        orch.dispatch("bare_agent", "look into approvals")


def test_propose_initiative_marks_conditional_on_order_violation():
    case, problem, hyp = _case_with_confirmed_hypothesis()
    orch = BPROrchestrator(case, {})
    initiative = Initiative(
        target_problem=problem.problem_id,
        target_root_cause=hyp.hypothesis_id,
        intervention_type=InterventionType.AUTOMATE,
        considered_alternatives=[],
    )
    result = orch.propose_initiative(initiative)
    assert result.status == InitiativeStatus.CONDITIONAL
    assert result.gate_blocked_reasons


def test_propose_initiative_ok_when_order_respected():
    case, problem, hyp = _case_with_confirmed_hypothesis()
    orch = BPROrchestrator(case, {})
    initiative = Initiative(
        target_problem=problem.problem_id,
        target_root_cause=hyp.hypothesis_id,
        intervention_type=InterventionType.ELIMINATE,
    )
    result = orch.propose_initiative(initiative)
    assert result.status == InitiativeStatus.PROPOSED
    assert result.gate_blocked_reasons == []


def test_record_decision_rejects_non_human_owner():
    case, problem, hyp = _case_with_confirmed_hypothesis()
    orch = BPROrchestrator(case, {})
    with pytest.raises(OrchestratorError):
        orch.record_decision(
            Decision(decision="approve", decision_owner="Orchestrator")
        )


def test_record_decision_accepts_named_human():
    case, problem, hyp = _case_with_confirmed_hypothesis()
    orch = BPROrchestrator(case, {})
    did = orch.record_decision(
        Decision(decision="approve pilot", decision_owner="Jane Doe, VP Ops")
    )
    assert did in case.decisions


def test_ready_to_scale_blocked_without_human_decision_on_high_risk_domain():
    case, problem, hyp = _case_with_confirmed_hypothesis()
    orch = BPROrchestrator(case, {})
    initiative = Initiative(
        target_problem=problem.problem_id,
        target_root_cause=hyp.hypothesis_id,
        intervention_type=InterventionType.ELIMINATE,
        reversibility=Reversibility.EASILY_REVERSIBLE,
        risks=[RiskItem(description="approval below threshold", domain="safety", severity="high")],
    )
    orch.propose_initiative(initiative)
    ready, reasons = orch.ready_to_scale(initiative.initiative_id)
    assert not ready
    assert any("human" in r for r in reasons)

    orch.record_decision(
        Decision(
            decision="accept safety risk and proceed",
            decision_owner="Head of Safety",
            domain="safety",
            initiative_id=initiative.initiative_id,
        )
    )
    # Gate 7 still needs a measured, meaningfully-realized KPI, so overall
    # scale readiness stays false, but the *human-decision* reason must be gone.
    ready2, reasons2 = orch.ready_to_scale(initiative.initiative_id)
    assert not any("human" in r for r in reasons2)


def test_opportunity_score_zero_when_gate_fails():
    case, problem, hyp = _case_with_confirmed_hypothesis()
    orch = BPROrchestrator(case, {})
    initiative = Initiative(
        target_problem=problem.problem_id,
        target_root_cause=hyp.hypothesis_id,
        intervention_type=InterventionType.AUTOMATE,
        considered_alternatives=[],  # violates Gate 2
    )
    orch.propose_initiative(initiative)
    score = orch.score_initiative(
        initiative.initiative_id,
        OpportunityFactors(impact=1, evidence=1, feasibility=1, urgency=1, strategic_fit=1),
    )
    assert score == 0.0


def test_opportunity_score_nonzero_when_gates_pass():
    case, problem, hyp = _case_with_confirmed_hypothesis()
    orch = BPROrchestrator(case, {})
    initiative = Initiative(
        target_problem=problem.problem_id,
        target_root_cause=hyp.hypothesis_id,
        intervention_type=InterventionType.ELIMINATE,
    )
    initiative.value.cash_saving = 10000
    initiative.cost = 1000
    orch.propose_initiative(initiative)
    score = orch.score_initiative(
        initiative.initiative_id,
        OpportunityFactors(impact=0.8, evidence=0.8, feasibility=0.8, urgency=0.8, strategic_fit=0.8),
    )
    assert score > 0.0
