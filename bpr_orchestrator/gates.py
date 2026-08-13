"""Quality Gates (design section 9).

Gate 0: Scope        - objective / scope / success criteria are explicit
Gate 1: Evidence      - root cause has evidence sufficient to act on
Gate 2: BPR           - eliminate/integrate/simplify were considered before automating
Gate 3: Safety/Legal/Quality - high-severity risks in those domains have a recorded human Decision
Gate 4: Feasibility    - technically/organizationally/operationally viable
Gate 5: Economics      - value breakdown shows positive realized value
Gate 6: Pilot          - a limited pilot is possible
Gate 7: Scale          - pilot outcome was actually measured and positive

An Initiative that fails any gate is CONDITIONAL, never APPROVED. Gate
failures are never averaged away by a high Opportunity Score — see
opportunity.py, which zeroes the score outright on any gate failure.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from bpr_orchestrator.models import (
    ConfidenceState,
    EvidenceQuality,
    HUMAN_ONLY_DOMAINS,
    Initiative,
    InterventionType,
    Reversibility,
    TransformationCase,
)
from bpr_orchestrator.redesign import missing_prerequisite_considerations

GATE_NAMES: list[str] = [
    "Gate 0: Scope",
    "Gate 1: Evidence",
    "Gate 2: BPR",
    "Gate 3: Safety/Legal/Quality",
    "Gate 4: Feasibility",
    "Gate 5: Economics",
    "Gate 6: Pilot",
    "Gate 7: Scale",
]


@dataclass
class GateResult:
    name: str
    passed: bool
    reasons: list[str] = field(default_factory=list)


@dataclass
class GateReport:
    results: list[GateResult]

    @property
    def all_passed(self) -> bool:
        return all(r.passed for r in self.results)

    @property
    def failed(self) -> list[GateResult]:
        return [r for r in self.results if not r.passed]


def gate_0_scope(case: TransformationCase, initiative: Initiative) -> GateResult:
    reasons = []
    if not case.objective.strip():
        reasons.append("objective is not defined")
    if not case.scope.strip():
        reasons.append("scope is not defined")
    return GateResult(GATE_NAMES[0], not reasons, reasons)


def gate_1_evidence(case: TransformationCase, initiative: Initiative) -> GateResult:
    reasons = []
    hyp = case.hypotheses.get(initiative.target_root_cause)
    if hyp is None:
        reasons.append("initiative has no linked root-cause hypothesis")
        return GateResult(GATE_NAMES[1], False, reasons)
    if hyp.confidence_state == ConfidenceState.UNCONFIRMED:
        reasons.append(
            f"root cause '{hyp.hypothesis_id}' is still unconfirmed"
        )
    qualities = [
        case.evidence[eid].quality
        for eid in hyp.evidence_for
        if eid in case.evidence
    ]
    if not qualities:
        reasons.append("root cause has no attached evidence")
    elif not any(q in (EvidenceQuality.HIGH, EvidenceQuality.MEDIUM) for q in qualities):
        reasons.append("all evidence for the root cause is low/unknown quality")
    return GateResult(GATE_NAMES[1], not reasons, reasons)


def gate_2_bpr(case: TransformationCase, initiative: Initiative) -> GateResult:
    missing = missing_prerequisite_considerations(initiative)
    reasons = []
    if missing:
        reasons.append(
            "intervention type "
            f"'{initiative.intervention_type.value}' proposed without "
            "documenting rejection of prerequisite alternatives: "
            + ", ".join(m.value for m in missing)
        )
    return GateResult(GATE_NAMES[2], not reasons, reasons)


def gate_3_safety_legal_quality(
    case: TransformationCase, initiative: Initiative
) -> GateResult:
    reasons = []
    high_severity_domain_risks = [
        r
        for r in initiative.risks
        if r.domain in HUMAN_ONLY_DOMAINS and r.severity in ("high", "critical")
    ]
    if high_severity_domain_risks:
        recorded_domains = {
            d.domain for d in case.decisions.values()
            if d.initiative_id == initiative.initiative_id
        }
        for risk in high_severity_domain_risks:
            if risk.domain not in recorded_domains:
                reasons.append(
                    f"risk in human-only domain '{risk.domain}' has no recorded "
                    "human Decision for this initiative"
                )
    return GateResult(GATE_NAMES[3], not reasons, reasons)


def gate_4_feasibility(case: TransformationCase, initiative: Initiative) -> GateResult:
    reasons = []
    if initiative.reversibility == Reversibility.IRREVERSIBLE and not any(
        d.initiative_id == initiative.initiative_id for d in case.decisions.values()
    ):
        reasons.append(
            "initiative is irreversible and has no recorded human Decision"
        )
    return GateResult(GATE_NAMES[4], not reasons, reasons)


def gate_5_economics(case: TransformationCase, initiative: Initiative) -> GateResult:
    reasons = []
    v = initiative.value
    realized = v.cash_saving + v.revenue + v.profit + v.loss_avoidance + v.risk_reduction
    if realized <= 0 and v.capacity_creation <= 0:
        reasons.append("value breakdown shows no realized or realizable value")
    if realized > 0 and realized < initiative.cost:
        reasons.append("realized value does not exceed initiative cost")
    return GateResult(GATE_NAMES[5], not reasons, reasons)


def gate_6_pilot(case: TransformationCase, initiative: Initiative) -> GateResult:
    reasons = []
    if not initiative.pilot_possible:
        reasons.append("initiative is marked as not pilotable")
    return GateResult(GATE_NAMES[6], not reasons, reasons)


def gate_7_scale(case: TransformationCase, initiative: Initiative) -> GateResult:
    reasons = []
    relevant_kpis = [
        k for k in case.kpis.values() if k.actual is not None
    ]
    if not relevant_kpis:
        reasons.append("no pilot outcome has been measured yet")
        return GateResult(GATE_NAMES[7], False, reasons)

    # Merely having *a* measurement isn't "effect demonstrated" — if we
    # know the baseline and expected value we require the pilot to have
    # realized a meaningful share of the promised improvement, otherwise
    # a barely-moved (or worse) KPI would pass Scale just because someone
    # took a measurement.
    for k in relevant_kpis:
        if k.baseline is None or k.expected is None:
            continue
        expected_delta = k.expected - k.baseline
        if expected_delta == 0:
            continue
        actual_delta = k.actual - k.baseline
        realized_fraction = actual_delta / expected_delta
        if realized_fraction < 0.5:
            reasons.append(
                f"KPI '{k.name}' realized only {realized_fraction:.0%} of the "
                "expected improvement over baseline; effect not sufficiently "
                "demonstrated to scale"
            )
    return GateResult(GATE_NAMES[7], not reasons, reasons)


_GATE_FUNCS = [
    gate_0_scope,
    gate_1_evidence,
    gate_2_bpr,
    gate_3_safety_legal_quality,
    gate_4_feasibility,
    gate_5_economics,
    gate_6_pilot,
    gate_7_scale,
]


def evaluate_all_gates(
    case: TransformationCase, initiative: Initiative, up_to: str | None = None
) -> GateReport:
    """Evaluate gates in order. `up_to` (a GATE_NAMES entry) limits how far
    we check — e.g. before a pilot only Gates 0-6 are relevant."""
    results = []
    for fn, name in zip(_GATE_FUNCS, GATE_NAMES):
        results.append(fn(case, initiative))
        if up_to is not None and name == up_to:
            break
    return GateReport(results)
