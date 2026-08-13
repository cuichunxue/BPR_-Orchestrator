"""The Orchestrator's phase state machine (design section 8).

DISCOVER -> UNDERSTAND -> DIAGNOSE -> VERIFY -> REDESIGN -> EVALUATE ->
PILOT -> MEASURE -> SCALE -> MONITOR -> (loops back to) DIAGNOSE

Phases never advance freely — each transition has a guard. The canonical
example from the design: Evidence Quality = Low keeps the case stuck
between DIAGNOSE and VERIFY; it cannot reach REDESIGN.
"""
from __future__ import annotations

from dataclasses import dataclass

from bpr_orchestrator.models import ConfidenceState, Phase, TransformationCase

# Linear forward order. MONITOR is handled separately since it loops.
_FORWARD_ORDER: list[Phase] = [
    Phase.DISCOVER,
    Phase.UNDERSTAND,
    Phase.DIAGNOSE,
    Phase.VERIFY,
    Phase.REDESIGN,
    Phase.EVALUATE,
    Phase.PILOT,
    Phase.MEASURE,
    Phase.SCALE,
    Phase.MONITOR,
]


@dataclass
class TransitionResult:
    allowed: bool
    reasons: list[str]


def _has_scope(case: TransformationCase) -> bool:
    return bool(case.objective.strip()) and bool(case.scope.strip())


def _has_sufficient_evidence(case: TransformationCase) -> bool:
    """At least one hypothesis must be backed by evidence that is not
    LOW/UNKNOWN quality before the case can leave VERIFY."""
    from bpr_orchestrator.models import EvidenceQuality

    for hyp in case.hypotheses.values():
        if hyp.confidence_state == ConfidenceState.UNCONFIRMED:
            continue
        qualities = [
            case.evidence[eid].quality
            for eid in hyp.evidence_for
            if eid in case.evidence
        ]
        if qualities and all(
            q in (EvidenceQuality.HIGH, EvidenceQuality.MEDIUM) for q in qualities
        ):
            return True
    return False


def _has_root_caused_initiatives(case: TransformationCase) -> bool:
    return any(
        init.target_root_cause and init.target_root_cause in case.hypotheses
        for init in case.initiatives.values()
    )


def _gates_clear_for_pilot(case: TransformationCase) -> bool:
    from bpr_orchestrator.gates import evaluate_all_gates

    return any(
        evaluate_all_gates(case, init, up_to="Gate 6: Pilot").all_passed
        for init in case.initiatives.values()
    )


def _has_measured_outcome(case: TransformationCase) -> bool:
    return any(kpi.actual is not None for kpi in case.kpis.values())


# Guard: given (from_phase), can we move to the next phase in _FORWARD_ORDER?
_GUARDS = {
    Phase.DISCOVER: _has_scope,
    Phase.UNDERSTAND: lambda case: len(case.problems) > 0,
    Phase.DIAGNOSE: lambda case: len(case.hypotheses) > 0,
    Phase.VERIFY: _has_sufficient_evidence,
    Phase.REDESIGN: _has_root_caused_initiatives,
    Phase.EVALUATE: _gates_clear_for_pilot,
    Phase.PILOT: lambda case: True,   # pilot itself is the check; entering is allowed once gated
    Phase.MEASURE: _has_measured_outcome,
    Phase.SCALE: lambda case: True,
    Phase.MONITOR: lambda case: True,
}


def next_phase(case: TransformationCase) -> Phase:
    if case.phase == Phase.MONITOR:
        return Phase.DIAGNOSE
    idx = _FORWARD_ORDER.index(case.phase)
    return _FORWARD_ORDER[idx + 1]


def can_advance(case: TransformationCase) -> TransitionResult:
    guard = _GUARDS.get(case.phase)
    if guard is None:
        return TransitionResult(True, [])
    ok = guard(case)
    if ok:
        return TransitionResult(True, [])
    return TransitionResult(
        False,
        [f"Guard for leaving phase {case.phase.value} was not satisfied"],
    )


def advance(case: TransformationCase) -> TransitionResult:
    """Attempt to move the case to its next phase in place. Returns the
    TransitionResult describing whether it happened."""
    result = can_advance(case)
    if result.allowed:
        case.phase = next_phase(case)
        case.phase_history.append(case.phase)
    return result
