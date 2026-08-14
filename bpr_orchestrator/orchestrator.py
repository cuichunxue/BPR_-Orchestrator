"""The BPR Orchestrator itself (design sections 3, 11, 12, 17, 19).

It does not perform process mining, statistics, or automation selection —
it dispatches to specialized agents (BaseAgent implementations) and owns:

  Scope management, Goal management, Task decomposition, Evidence
  integration, Contradiction detection, Gate judgment, Decision recording,
  Execution monitoring, Learning.

It never itself renders a decision in a human-only domain (design section
17) — it only ever produces Evidence + Options + Risks for those, and
records the Decision a human actually makes.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from bpr_orchestrator.contracts import AgentResponse, AgentTask, BaseAgent
from bpr_orchestrator.gates import GateReport, evaluate_all_gates
from bpr_orchestrator.memory import EnterpriseMemory, GeneralPatternLibrary
from bpr_orchestrator.models import (
    ConfidenceState,
    Decision,
    DesignArtifact,
    HUMAN_ONLY_DOMAINS,
    Hypothesis,
    Initiative,
    KPIRecord,
    Learning,
    Problem,
    Reversibility,
    TransformationCase,
)
from bpr_orchestrator.opportunity import OpportunityFactors, compute_opportunity_score
from bpr_orchestrator.redesign import validate_intervention_order
from bpr_orchestrator.state_machine import TransitionResult, advance as sm_advance

_NON_HUMAN_OWNERS = {"orchestrator", "agent", "ai", "system", "bpr orchestrator", "llm"}

# Default candidate explanations for a KPI shortfall — design section 19.
_DEFAULT_GAP_EXPLANATIONS = [
    "adoption not as expected",
    "scope narrower than planned",
    "root-cause hypothesis was wrong",
    "exception rate increased",
    "data misinterpretation",
]


class OrchestratorError(Exception):
    pass


@dataclass
class InvestigationCandidate:
    """A candidate 'Next Best Investigation' (design section 12)."""

    name: str
    cost: float
    time: float
    discrimination_power: float   # how well it can distinguish between hypotheses, 0..1
    resolves_hypotheses: list[str] = field(default_factory=list)

    @property
    def information_value_score(self) -> float:
        denom = max(self.cost * self.time, 1e-9)
        return self.discrimination_power / denom


class BPROrchestrator:
    def __init__(
        self,
        case: TransformationCase,
        agents: dict[str, BaseAgent],
        enterprise_memory: EnterpriseMemory | None = None,
        pattern_library: GeneralPatternLibrary | None = None,
    ):
        self.case = case
        self.agents = agents
        self.enterprise_memory = enterprise_memory
        self.pattern_library = pattern_library
        self._task_counter = 0

    # -- Scope / Goal management --------------------------------------

    def set_objective(
        self, objective: str, scope: str, stakeholders: list[str] | None = None,
        constraints: list[str] | None = None,
    ) -> None:
        self.case.objective = objective
        self.case.scope = scope
        self.case.stakeholders = stakeholders or []
        self.case.constraints = constraints or []

    def add_problem(self, problem: Problem) -> str:
        return self.case.add(problem)

    def add_hypothesis(self, hypothesis: Hypothesis) -> str:
        return self.case.add(hypothesis)

    # -- Task decomposition / dispatch ----------------------------------

    def dispatch(self, agent_name: str, instruction: str, **task_kwargs) -> AgentResponse:
        agent = self.agents.get(agent_name)
        if agent is None:
            raise OrchestratorError(f"no agent registered under '{agent_name}'")
        self._task_counter += 1
        task = AgentTask(
            task_id=f"task_{self._task_counter}",
            agent_name=agent_name,
            instruction=instruction,
            **task_kwargs,
        )
        response = agent.run(task, self.case)
        self._integrate_response(response, task)
        return response

    def _integrate_response(self, response: AgentResponse, task: AgentTask) -> None:
        well_formed, problems = response.is_well_formed()
        if not well_formed:
            raise OrchestratorError(
                f"agent '{response.agent}' returned a malformed response: {problems}"
            )
        if response.contradictions:
            self.case.contradictions.extend(response.contradictions)
        if response.artifact is not None:
            self.case.add(
                DesignArtifact(
                    artifact_type=response.artifact_type or "unknown",
                    content=response.artifact,
                    phase=self.case.phase,
                    source_agent=response.agent,
                    problem_id=task.problem_id,
                    initiative_id=task.initiative_id,
                )
            )

    # -- Next Best Investigation (design section 12) ---------------------

    def rank_investigations(
        self, candidates: list[InvestigationCandidate]
    ) -> list[InvestigationCandidate]:
        return sorted(candidates, key=lambda c: c.information_value_score, reverse=True)

    def next_best_investigation(
        self, candidates: list[InvestigationCandidate]
    ) -> InvestigationCandidate | None:
        ranked = self.rank_investigations(candidates)
        return ranked[0] if ranked else None

    # -- Contradiction detection ------------------------------------------

    def detect_contradictions(self) -> list[str]:
        found: list[str] = []
        for hyp in self.case.hypotheses.values():
            strong_for = any(
                self.case.evidence[eid].quality.value in ("high", "medium")
                for eid in hyp.evidence_for
                if eid in self.case.evidence
            )
            strong_against = any(
                self.case.evidence[eid].quality.value in ("high", "medium")
                for eid in hyp.evidence_against
                if eid in self.case.evidence
            )
            if strong_for and strong_against:
                msg = (
                    f"hypothesis '{hyp.hypothesis_id}' has credible evidence "
                    "both for and against it"
                )
                found.append(msg)
        for msg in found:
            if msg not in self.case.contradictions:
                self.case.contradictions.append(msg)
        return found

    # -- Initiative proposal (must follow ESIA+ order) --------------------

    def propose_initiative(self, initiative: Initiative) -> Initiative:
        hyp = self.case.hypotheses.get(initiative.target_root_cause)
        if hyp is None:
            raise OrchestratorError(
                "initiative must reference an existing target_root_cause hypothesis"
            )
        if hyp.confidence_state == ConfidenceState.UNCONFIRMED:
            initiative.gate_blocked_reasons.append(
                "root cause is unconfirmed; initiative cannot be treated as final"
            )

        order_ok, order_reasons = validate_intervention_order(initiative)
        if not order_ok:
            initiative.gate_blocked_reasons.extend(order_reasons)

        from bpr_orchestrator.models import InitiativeStatus
        initiative.status = (
            InitiativeStatus.PROPOSED
            if not initiative.gate_blocked_reasons
            else InitiativeStatus.CONDITIONAL
        )
        self.case.add(initiative)
        return initiative

    # -- Gate judgment -----------------------------------------------------

    def evaluate_gates(self, initiative_id: str, up_to: str | None = None) -> GateReport:
        initiative = self.case.initiatives[initiative_id]
        report = evaluate_all_gates(self.case, initiative, up_to=up_to)
        from bpr_orchestrator.models import InitiativeStatus
        if report.all_passed:
            if initiative.status == InitiativeStatus.CONDITIONAL:
                initiative.status = InitiativeStatus.PROPOSED
            initiative.gate_blocked_reasons = []
        else:
            initiative.status = InitiativeStatus.CONDITIONAL
            initiative.gate_blocked_reasons = [
                reason for r in report.failed for reason in r.reasons
            ]
        return report

    def score_initiative(
        self, initiative_id: str, factors: OpportunityFactors
    ) -> float:
        # Opportunity scoring ranks *candidates for piloting*, so it checks
        # everything through Gate 6 (Pilot) but not Gate 7 (Scale) — Scale
        # can only ever be judged after a pilot has produced a measured
        # outcome, which by definition doesn't exist yet at this point.
        report = self.evaluate_gates(initiative_id, up_to="Gate 6: Pilot")
        initiative = self.case.initiatives[initiative_id]
        score = compute_opportunity_score(factors, report)
        initiative.opportunity_score = score
        return score

    # -- Human-in-the-loop (design section 17) ------------------------------

    def requires_human_decision(self, initiative_id: str) -> bool:
        initiative = self.case.initiatives[initiative_id]
        if initiative.reversibility == Reversibility.IRREVERSIBLE:
            return True
        return any(r.domain in HUMAN_ONLY_DOMAINS and r.severity in ("high", "critical")
                   for r in initiative.risks)

    def record_decision(self, decision: Decision) -> str:
        owner_norm = decision.decision_owner.strip().lower()
        if not owner_norm or owner_norm in _NON_HUMAN_OWNERS:
            raise OrchestratorError(
                "Decision.decision_owner must identify an accountable human; "
                "the Orchestrator may not record itself or an agent as the "
                "decision owner"
            )
        return self.case.add(decision)

    def ready_to_scale(self, initiative_id: str) -> tuple[bool, list[str]]:
        initiative = self.case.initiatives[initiative_id]
        reasons: list[str] = []
        report = self.evaluate_gates(initiative_id)
        if not report.all_passed:
            reasons.extend(r for res in report.failed for r in res.reasons)
        if self.requires_human_decision(initiative_id):
            has_decision = any(
                d.initiative_id == initiative_id for d in self.case.decisions.values()
            )
            if not has_decision:
                reasons.append(
                    "initiative touches a human-only domain or is irreversible "
                    "and has no recorded human Decision"
                )
        return (len(reasons) == 0, reasons)

    # -- Phase / execution monitoring --------------------------------------

    def advance_phase(self) -> TransitionResult:
        return sm_advance(self.case)

    # -- Learning loop (design section 19) ----------------------------------

    def run_learning_loop(self, initiative_id: str, kpi: KPIRecord) -> Learning:
        self.case.add(kpi)
        if kpi.expected is None or kpi.actual is None:
            raise OrchestratorError("KPIRecord needs both expected and actual to learn from")
        gap = kpi.actual - kpi.expected
        gap_desc = (
            f"{kpi.name}: expected {kpi.expected}{kpi.unit}, "
            f"actual {kpi.actual}{kpi.unit} (gap {gap:+.2f}{kpi.unit})"
        )
        learning = Learning(
            initiative_id=initiative_id,
            expected_vs_actual_gap=gap_desc,
            candidate_explanations=list(_DEFAULT_GAP_EXPLANATIONS) if gap != 0 else [],
            follow_up_action=(
                "reinvestigate root cause and pilot adoption/scope before scaling"
                if gap != 0 else "outcome matched expectation; proceed to Scale gate"
            ),
        )
        self.case.add(learning)
        return learning

    # -- Misc reporting --------------------------------------------------

    def pending_human_decisions(self) -> list[str]:
        pending = []
        for initiative_id in self.case.initiatives:
            if self.requires_human_decision(initiative_id):
                has_decision = any(
                    d.initiative_id == initiative_id for d in self.case.decisions.values()
                )
                if not has_decision:
                    pending.append(initiative_id)
        return pending
