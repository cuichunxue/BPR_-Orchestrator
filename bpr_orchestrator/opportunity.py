"""Opportunity Score (design section 16).

Opportunity = Impact x Evidence x Feasibility x Urgency x StrategicFit

Any Gate failure forces the score to 0 outright — safety, legal, and
quality are never traded off against a high score on the other factors.
"""
from __future__ import annotations

from dataclasses import dataclass

from bpr_orchestrator.gates import GateReport


@dataclass
class OpportunityFactors:
    impact: float          # 0..1
    evidence: float        # 0..1
    feasibility: float     # 0..1
    urgency: float         # 0..1
    strategic_fit: float   # 0..1


def compute_opportunity_score(
    factors: OpportunityFactors, gate_report: GateReport
) -> float:
    if not gate_report.all_passed:
        return 0.0
    return (
        factors.impact
        * factors.evidence
        * factors.feasibility
        * factors.urgency
        * factors.strategic_fit
    )
