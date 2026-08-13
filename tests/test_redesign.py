from bpr_orchestrator.models import Initiative, InterventionType
from bpr_orchestrator.redesign import (
    missing_prerequisite_considerations,
    next_recommended_intervention,
    validate_intervention_order,
)


def test_automate_requires_all_prior_steps_considered():
    initiative = Initiative(
        target_problem="p1",
        target_root_cause="h1",
        intervention_type=InterventionType.AUTOMATE,
        considered_alternatives=[InterventionType.ELIMINATE, InterventionType.SIMPLIFY],
    )
    ok, reasons = validate_intervention_order(initiative)
    assert not ok
    missing = missing_prerequisite_considerations(initiative)
    assert InterventionType.INTEGRATE in missing
    assert InterventionType.ELIMINATE not in missing


def test_eliminate_needs_no_prior_consideration():
    initiative = Initiative(
        target_problem="p1",
        target_root_cause="h1",
        intervention_type=InterventionType.ELIMINATE,
    )
    ok, reasons = validate_intervention_order(initiative)
    assert ok
    assert reasons == []


def test_next_recommended_intervention_follows_order():
    rejected = {InterventionType.ELIMINATE, InterventionType.INTEGRATE}
    assert next_recommended_intervention(rejected) == InterventionType.SIMPLIFY
    all_rejected = set(InterventionType)
    assert next_recommended_intervention(all_rejected) is None
