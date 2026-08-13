"""End-to-end walkthrough of the BPR Orchestrator using mock agents.

Run with: python -m examples.run_demo
"""
from __future__ import annotations

from bpr_orchestrator.agents.mock_agents import (
    MockAutomationAgent,
    MockDataAgent,
    MockProcessAgent,
    MockRootCauseAgent,
)
from bpr_orchestrator.models import (
    Decision,
    Initiative,
    InterventionType,
    KPIRecord,
    Problem,
    Reversibility,
    RiskItem,
    TransformationCase,
)
from bpr_orchestrator.opportunity import OpportunityFactors
from bpr_orchestrator.orchestrator import BPROrchestrator, InvestigationCandidate


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def main() -> None:
    case = TransformationCase()
    agents = {
        "process_agent": MockProcessAgent(),
        "data_agent": MockDataAgent(),
        "root_cause_agent": MockRootCauseAgent(),
        "automation_agent": MockAutomationAgent(),
    }
    orch = BPROrchestrator(case, agents)

    section("Gate 0 / DISCOVER: scope")
    orch.set_objective(
        objective="Reduce order-approval cycle time to improve customer NPS",
        scope="Order-to-cash approval workflow, EMEA region",
        stakeholders=["Sales Ops Director", "Finance Controller"],
        constraints=["no changes to SOX-controlled approval thresholds"],
    )
    print(orch.advance_phase())  # DISCOVER -> UNDERSTAND

    section("UNDERSTAND: register the problem")
    problem_id = orch.add_problem(
        Problem(
            symptom="Order approvals take 4+ days on average",
            business_impact="delayed revenue recognition",
            customer_impact="customers cancel orders during the wait",
            severity="high",
            frequency="daily",
        )
    )
    print(orch.advance_phase())  # UNDERSTAND -> DIAGNOSE

    section("DIAGNOSE: dispatch Process + Root Cause agents")
    print(orch.dispatch("process_agent", "map the As-Is approval process"))
    rc_response = orch.dispatch(
        "root_cause_agent",
        "approval routing requires 2 sequential sign-offs regardless of order size",
        problem_id=problem_id,
    )
    print(rc_response)
    hypothesis_id = next(iter(case.hypotheses))
    print(orch.advance_phase())  # DIAGNOSE -> VERIFY

    section("Next Best Investigation")
    candidates = [
        InvestigationCandidate("interview 3 approvers", cost=2, time=3, discrimination_power=0.4),
        InvestigationCandidate("analyze 90 days of ticket logs", cost=1, time=1, discrimination_power=0.8),
        InvestigationCandidate("measure system latency", cost=3, time=2, discrimination_power=0.3),
    ]
    best = orch.next_best_investigation(candidates)
    print(f"chosen investigation: {best.name} (score={best.information_value_score:.3f})")

    section("VERIFY: dispatch Data Agent against the hypothesis")
    print(orch.dispatch("data_agent", "quantify approval cycle time", hypothesis_id=hypothesis_id))
    print(orch.advance_phase())  # VERIFY -> REDESIGN

    section("REDESIGN: propose an initiative respecting eliminate->...->automate order")
    initiative = Initiative(
        target_problem=problem_id,
        target_root_cause=hypothesis_id,
        intervention_type=InterventionType.SIMPLIFY,
        considered_alternatives=[InterventionType.ELIMINATE, InterventionType.INTEGRATE],
        expected_effect="single risk-based approval step for orders under threshold",
        cost=15000,
        reversibility=Reversibility.EASILY_REVERSIBLE,
        risks=[RiskItem(description="threshold miscalibration", domain="operational", severity="medium")],
        evidence_basis=list(case.evidence.keys()),
    )
    initiative_id = orch.propose_initiative(initiative).initiative_id
    print(orch.advance_phase())  # REDESIGN -> EVALUATE

    section("EVALUATE: gates + opportunity score")
    initiative.value.cash_saving = 40000
    report = orch.evaluate_gates(initiative_id)
    for r in report.results:
        print(f"  {r.name}: {'PASS' if r.passed else 'FAIL'} {r.reasons}")
    score = orch.score_initiative(
        initiative_id,
        OpportunityFactors(impact=0.8, evidence=0.7, feasibility=0.9, urgency=0.6, strategic_fit=0.7),
    )
    print(f"opportunity score: {score:.3f}")
    ready, reasons = orch.ready_to_scale(initiative_id)
    print(f"ready to scale before pilot: {ready} {reasons}")
    print(orch.advance_phase())  # EVALUATE -> PILOT

    section("Human-in-the-loop: record the go/no-go for pilot")
    orch.record_decision(
        Decision(
            decision="approve limited pilot in one sales region",
            decision_owner="Sales Ops Director",
            evidence_used=list(case.evidence.keys()),
            alternatives=["do nothing", "eliminate approval entirely"],
            risk_accepted=["threshold miscalibration during pilot window"],
            initiative_id=initiative_id,
        )
    )
    print(orch.advance_phase())  # PILOT -> MEASURE

    section("MEASURE: compare expected vs actual KPI")
    kpi = KPIRecord(
        name="approval_cycle_time_days", baseline=4.2, expected=2.9, actual=3.7, unit="d"
    )
    learning = orch.run_learning_loop(initiative_id, kpi)
    print(learning)
    print(orch.advance_phase())  # MEASURE -> SCALE

    section("SCALE readiness")
    ready, reasons = orch.ready_to_scale(initiative_id)
    print(f"ready to scale: {ready} {reasons}")

    section("Phase history")
    print([p.value for p in case.phase_history])


if __name__ == "__main__":
    main()
