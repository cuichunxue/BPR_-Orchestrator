"""Enforces the BPR redesign sequence (design section 13):

  eliminate -> integrate -> simplify -> parallelize -> instant ->
  exception_management -> empower -> automate

An Initiative is never allowed to jump straight to AUTOMATE (or any later
step) without the earlier, cheaper interventions having been explicitly
considered and documented as rejected for that root cause. This is what
keeps "just add an LLM agent" from being step one.
"""
from __future__ import annotations

from bpr_orchestrator.models import Initiative, INTERVENTION_ORDER, InterventionType


def missing_prerequisite_considerations(initiative: Initiative) -> list[InterventionType]:
    """Return the intervention types that must have been considered
    (and are not) before `initiative.intervention_type` may be proposed."""
    target_idx = INTERVENTION_ORDER.index(initiative.intervention_type)
    required = set(INTERVENTION_ORDER[:target_idx])
    considered = set(initiative.considered_alternatives)
    return [t for t in INTERVENTION_ORDER if t in required and t not in considered]


def validate_intervention_order(initiative: Initiative) -> tuple[bool, list[str]]:
    missing = missing_prerequisite_considerations(initiative)
    if not missing:
        return True, []
    reasons = [
        f"'{m.value}' was not considered/documented as rejected before "
        f"proposing '{initiative.intervention_type.value}'"
        for m in missing
    ]
    return False, reasons


def next_recommended_intervention(already_rejected: set[InterventionType]) -> InterventionType | None:
    """Given the set of intervention types already considered and rejected
    for a root cause, return the next one to evaluate in mandated order."""
    for t in INTERVENTION_ORDER:
        if t not in already_rejected:
            return t
    return None
