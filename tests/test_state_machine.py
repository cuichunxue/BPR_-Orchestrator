import pytest

from bpr_orchestrator.models import Phase, Problem, TransformationCase
from bpr_orchestrator.state_machine import advance, can_advance


def test_cannot_leave_discover_without_scope():
    case = TransformationCase()
    result = can_advance(case)
    assert not result.allowed
    assert case.phase == Phase.DISCOVER


def test_discover_to_understand_once_scope_set():
    case = TransformationCase(objective="reduce cost", scope="finance dept")
    result = advance(case)
    assert result.allowed
    assert case.phase == Phase.UNDERSTAND


def test_cannot_skip_phases():
    case = TransformationCase(objective="x", scope="y")
    advance(case)  # DISCOVER -> UNDERSTAND
    assert case.phase == Phase.UNDERSTAND
    # UNDERSTAND -> DIAGNOSE requires at least one Problem
    result = advance(case)
    assert not result.allowed
    assert case.phase == Phase.UNDERSTAND
    case.add(Problem(symptom="slow process"))
    result = advance(case)
    assert result.allowed
    assert case.phase == Phase.DIAGNOSE


def test_monitor_loops_back_to_diagnose():
    case = TransformationCase(objective="x", scope="y", phase=Phase.MONITOR)
    result = advance(case)
    assert result.allowed
    assert case.phase == Phase.DIAGNOSE
