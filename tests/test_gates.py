from bpr_orchestrator.gates import evaluate_all_gates
from bpr_orchestrator.models import (
    ConfidenceState,
    Evidence,
    EvidenceQuality,
    EvidenceSourceType,
    Hypothesis,
    Initiative,
    InterventionType,
    KPIRecord,
    Problem,
    TransformationCase,
)


def _base_case() -> TransformationCase:
    case = TransformationCase(objective="cut cycle time", scope="EMEA order approvals")
    problem = Problem(symptom="slow approvals")
    case.add(problem)
    hyp = Hypothesis(
        problem_id=problem.problem_id,
        cause_candidate="double approval",
        confidence_state=ConfidenceState.SUPPORTED,
    )
    case.add(hyp)
    return case, problem, hyp


def test_gate1_fails_without_evidence():
    case, problem, hyp = _base_case()
    initiative = Initiative(
        target_problem=problem.problem_id,
        target_root_cause=hyp.hypothesis_id,
        intervention_type=InterventionType.ELIMINATE,
    )
    report = evaluate_all_gates(case, initiative)
    gate1 = next(r for r in report.results if r.name == "Gate 1: Evidence")
    assert not gate1.passed


def test_gate1_passes_with_high_quality_evidence():
    case, problem, hyp = _base_case()
    ev = Evidence(
        source="ticket_log",
        source_type=EvidenceSourceType.DATA,
        quality=EvidenceQuality.HIGH,
    )
    case.add(ev)
    hyp.evidence_for.append(ev.evidence_id)
    initiative = Initiative(
        target_problem=problem.problem_id,
        target_root_cause=hyp.hypothesis_id,
        intervention_type=InterventionType.ELIMINATE,
    )
    report = evaluate_all_gates(case, initiative)
    gate1 = next(r for r in report.results if r.name == "Gate 1: Evidence")
    assert gate1.passed


def test_gate2_bpr_blocks_automate_without_considering_alternatives():
    case, problem, hyp = _base_case()
    initiative = Initiative(
        target_problem=problem.problem_id,
        target_root_cause=hyp.hypothesis_id,
        intervention_type=InterventionType.AUTOMATE,
        considered_alternatives=[],
    )
    report = evaluate_all_gates(case, initiative)
    gate2 = next(r for r in report.results if r.name == "Gate 2: BPR")
    assert not gate2.passed
    assert "eliminate" in gate2.reasons[0]


def test_gate2_bpr_passes_when_all_prior_steps_considered():
    case, problem, hyp = _base_case()
    prior = [t for t in InterventionType if t != InterventionType.AUTOMATE]
    initiative = Initiative(
        target_problem=problem.problem_id,
        target_root_cause=hyp.hypothesis_id,
        intervention_type=InterventionType.AUTOMATE,
        considered_alternatives=prior,
    )
    report = evaluate_all_gates(case, initiative)
    gate2 = next(r for r in report.results if r.name == "Gate 2: BPR")
    assert gate2.passed


def test_gate7_requires_meaningful_realized_improvement():
    case, problem, hyp = _base_case()
    initiative = Initiative(
        target_problem=problem.problem_id,
        target_root_cause=hyp.hypothesis_id,
        intervention_type=InterventionType.ELIMINATE,
    )
    kpi = KPIRecord(name="cycle_time", baseline=4.2, expected=2.9, actual=4.0, unit="d")
    case.add(kpi)
    report = evaluate_all_gates(case, initiative)
    gate7 = next(r for r in report.results if r.name == "Gate 7: Scale")
    assert not gate7.passed

    kpi2 = KPIRecord(name="cycle_time_2", baseline=4.2, expected=2.9, actual=3.0, unit="d")
    case.add(kpi2)
    case.kpis.pop(kpi.kpi_id)
    report2 = evaluate_all_gates(case, initiative)
    gate7b = next(r for r in report2.results if r.name == "Gate 7: Scale")
    assert gate7b.passed
