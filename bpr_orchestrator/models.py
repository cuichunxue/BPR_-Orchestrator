"""Core data model for the BPR Orchestrator.

These objects mirror the structured "Transformation Case" state described in
the design: Problem / Hypothesis / Evidence / Initiative / Decision are never
free text — they are structured records the Orchestrator reasons over.
"""
from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Optional
from uuid import uuid4

from pydantic import BaseModel, Field


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:10]}"


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class Phase(str, Enum):
    """The Orchestrator's state machine. Linear, except MONITOR loops back
    to DIAGNOSE (continuous transformation)."""

    DISCOVER = "DISCOVER"
    UNDERSTAND = "UNDERSTAND"
    DIAGNOSE = "DIAGNOSE"
    VERIFY = "VERIFY"
    REDESIGN = "REDESIGN"
    EVALUATE = "EVALUATE"
    PILOT = "PILOT"
    MEASURE = "MEASURE"
    SCALE = "SCALE"
    MONITOR = "MONITOR"


class ProblemStatus(str, Enum):
    OPEN = "open"
    INVESTIGATING = "investigating"
    DIAGNOSED = "diagnosed"
    RESOLVED = "resolved"
    MONITORING = "monitoring"


class CauseLevel(str, Enum):
    SURFACE = "surface"
    STRUCTURAL = "structural"
    CONTROL_DESIGN = "control_design"


class ConfidenceState(str, Enum):
    UNCONFIRMED = "unconfirmed"
    SUPPORTED = "supported"
    CONFIRMED_WITHIN_EVIDENCE = "confirmed_within_evidence"


class EvidenceSourceType(str, Enum):
    DATA = "data"
    INTERVIEW = "interview"
    OBSERVATION = "observation"
    DOCUMENT = "document"
    EXPERIMENT = "experiment"
    SYSTEM_LOG = "system_log"


class EvidenceQuality(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNKNOWN = "unknown"


class InterventionType(str, Enum):
    """Ordered by mandated consideration priority (ESIA+ sequence).
    Index in this enum == priority order; AUTOMATE is deliberately last."""

    ELIMINATE = "eliminate"
    INTEGRATE = "integrate"
    SIMPLIFY = "simplify"
    PARALLELIZE = "parallelize"
    INSTANT = "instant"
    EXCEPTION_MANAGEMENT = "exception_management"
    EMPOWER = "empower"
    AUTOMATE = "automate"


INTERVENTION_ORDER: list[InterventionType] = list(InterventionType)


class InitiativeStatus(str, Enum):
    PROPOSED = "proposed"
    CONDITIONAL = "conditional"          # passed evidence gate but not all gates
    GATE_BLOCKED = "gate_blocked"
    APPROVED = "approved"
    PILOTING = "piloting"
    SCALING = "scaling"
    STOPPED = "stopped"
    MONITORING = "monitoring"


class Reversibility(str, Enum):
    EASILY_REVERSIBLE = "easily_reversible"
    COSTLY_REVERSIBLE = "costly_reversible"
    IRREVERSIBLE = "irreversible"


# Domains where the Orchestrator must never render the final decision itself.
# See design section 17 ("Human-in-the-loop").
HUMAN_ONLY_DOMAINS: tuple[str, ...] = (
    "legal_compliance",
    "safety",
    "quality_final",
    "shipping",
    "hr_evaluation",
    "hiring_firing",
    "major_investment",
    "corporate_strategy",
)


# ---------------------------------------------------------------------------
# Core objects
# ---------------------------------------------------------------------------

class Problem(BaseModel):
    problem_id: str = Field(default_factory=lambda: _id("prob"))
    symptom: str
    business_impact: str = ""
    customer_impact: str = ""
    scope: str = ""
    severity: str = "unknown"          # low/medium/high/critical
    frequency: str = "unknown"
    status: ProblemStatus = ProblemStatus.OPEN


class Evidence(BaseModel):
    """Never collapse evidence to a bare number or a bare quote — every
    field here exists because the design explicitly forbids e.g. a Data
    Agent returning just '平均処理時間4.2日' without denominator, period,
    distribution, missing data, outliers, sample size, and collection
    conditions."""

    evidence_id: str = Field(default_factory=lambda: _id("ev"))
    source: str
    source_type: EvidenceSourceType
    independence: bool = True
    sample_size: Optional[int] = None
    freshness: Optional[str] = None
    bias_risk: str = "unknown"
    conflict_of_interest: bool = False
    reproducibility: str = "unknown"
    supports: list[str] = Field(default_factory=list)     # hypothesis_ids
    contradicts: list[str] = Field(default_factory=list)  # hypothesis_ids
    quality: EvidenceQuality = EvidenceQuality.UNKNOWN
    limitations: list[str] = Field(default_factory=list)
    raw_finding: str = ""


class Hypothesis(BaseModel):
    hypothesis_id: str = Field(default_factory=lambda: _id("hyp"))
    problem_id: str
    cause_candidate: str
    cause_level: CauseLevel = CauseLevel.SURFACE
    evidence_for: list[str] = Field(default_factory=list)      # evidence_ids
    evidence_against: list[str] = Field(default_factory=list)  # evidence_ids
    evidence_quality: EvidenceQuality = EvidenceQuality.UNKNOWN
    confidence_state: ConfidenceState = ConfidenceState.UNCONFIRMED
    next_verification: str = ""


class RiskItem(BaseModel):
    risk_id: str = Field(default_factory=lambda: _id("risk"))
    description: str
    domain: str = "operational"   # e.g. legal_compliance / safety / quality_final / operational / financial
    severity: str = "unknown"
    likelihood: str = "unknown"
    mitigation: str = ""


class ValueBreakdown(BaseModel):
    """Output of the Value Engine (design section 15). Raw resource
    freed (e.g. hours saved) must be walked through a realization chain
    before it is allowed to become a monetary figure."""

    cash_saving: float = 0.0
    capacity_creation: float = 0.0     # hours/FTE freed, not yet monetized
    loss_avoidance: float = 0.0
    revenue: float = 0.0
    profit: float = 0.0
    risk_reduction: float = 0.0
    realization_notes: list[str] = Field(default_factory=list)


class Initiative(BaseModel):
    initiative_id: str = Field(default_factory=lambda: _id("init"))
    target_problem: str                 # problem_id
    target_root_cause: str               # hypothesis_id — mandatory, enforced by orchestrator
    intervention_type: InterventionType
    considered_alternatives: list[InterventionType] = Field(default_factory=list)
    expected_effect: str = ""
    value: ValueBreakdown = Field(default_factory=ValueBreakdown)
    cost: float = 0.0
    risks: list[RiskItem] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    evidence_basis: list[str] = Field(default_factory=list)   # evidence_ids
    reversibility: Reversibility = Reversibility.COSTLY_REVERSIBLE
    pilot_possible: bool = True
    owner: str = ""
    status: InitiativeStatus = InitiativeStatus.PROPOSED
    opportunity_score: Optional[float] = None
    gate_blocked_reasons: list[str] = Field(default_factory=list)


class Decision(BaseModel):
    """A Human Decision Object — the audit trail of what a human actually
    decided, and on what evidence. The Orchestrator produces evidence and
    options; it records, but never authors, this object's `decision`."""

    decision_id: str = Field(default_factory=lambda: _id("dec"))
    decision: str
    decision_owner: str                  # must be a human identity, never "orchestrator"/"agent"
    domain: Optional[str] = None         # one of HUMAN_ONLY_DOMAINS if applicable
    decision_date: date = Field(default_factory=date.today)
    evidence_used: list[str] = Field(default_factory=list)
    alternatives: list[str] = Field(default_factory=list)
    risk_accepted: list[str] = Field(default_factory=list)
    conditions: list[str] = Field(default_factory=list)
    review_date: Optional[date] = None
    initiative_id: Optional[str] = None


class KPIRecord(BaseModel):
    kpi_id: str = Field(default_factory=lambda: _id("kpi"))
    name: str
    baseline: Optional[float] = None
    expected: Optional[float] = None
    actual: Optional[float] = None
    unit: str = ""
    measured_at: Optional[datetime] = None
    causal_method: Optional[str] = None   # ab_test / phased_rollout / diff_in_diff / interrupted_time_series / control_process / none


class Learning(BaseModel):
    learning_id: str = Field(default_factory=lambda: _id("learn"))
    initiative_id: str
    expected_vs_actual_gap: str
    candidate_explanations: list[str] = Field(default_factory=list)
    follow_up_action: str = ""


class DesignArtifact(BaseModel):
    """A non-JSON artifact an agent produced (e.g. generated BPMN XML).
    Kept in the Case alongside Evidence/Decisions so a diagram is
    traceable to the problem/initiative and phase it was produced for,
    instead of only existing in the AgentResponse that returned it."""

    artifact_id: str = Field(default_factory=lambda: _id("art"))
    artifact_type: str
    content: str
    phase: Phase
    source_agent: str = ""
    problem_id: Optional[str] = None
    initiative_id: Optional[str] = None


# ---------------------------------------------------------------------------
# Aggregate: the Transformation Case
# ---------------------------------------------------------------------------

class TransformationCase(BaseModel):
    """One BPR engagement's full working state. The Orchestrator reads and
    mutates this object across the whole DISCOVER..MONITOR loop instead of
    relying on conversational memory."""

    case_id: str = Field(default_factory=lambda: _id("case"))
    objective: str = ""
    scope: str = ""
    stakeholders: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    current_process: str = ""

    phase: Phase = Phase.DISCOVER

    problems: dict[str, Problem] = Field(default_factory=dict)
    hypotheses: dict[str, Hypothesis] = Field(default_factory=dict)
    evidence: dict[str, Evidence] = Field(default_factory=dict)
    initiatives: dict[str, Initiative] = Field(default_factory=dict)
    risks: dict[str, RiskItem] = Field(default_factory=dict)
    decisions: dict[str, Decision] = Field(default_factory=dict)
    kpis: dict[str, KPIRecord] = Field(default_factory=dict)
    learnings: dict[str, Learning] = Field(default_factory=dict)
    design_artifacts: dict[str, DesignArtifact] = Field(default_factory=dict)

    contradictions: list[str] = Field(default_factory=list)
    phase_history: list[Phase] = Field(default_factory=lambda: [Phase.DISCOVER])

    def add(self, obj: BaseModel) -> str:
        """Insert any of the case's structured objects into the right
        collection and return its id."""
        if isinstance(obj, Problem):
            self.problems[obj.problem_id] = obj
            return obj.problem_id
        if isinstance(obj, Hypothesis):
            self.hypotheses[obj.hypothesis_id] = obj
            return obj.hypothesis_id
        if isinstance(obj, Evidence):
            self.evidence[obj.evidence_id] = obj
            return obj.evidence_id
        if isinstance(obj, Initiative):
            self.initiatives[obj.initiative_id] = obj
            return obj.initiative_id
        if isinstance(obj, RiskItem):
            self.risks[obj.risk_id] = obj
            return obj.risk_id
        if isinstance(obj, Decision):
            self.decisions[obj.decision_id] = obj
            return obj.decision_id
        if isinstance(obj, KPIRecord):
            self.kpis[obj.kpi_id] = obj
            return obj.kpi_id
        if isinstance(obj, Learning):
            self.learnings[obj.learning_id] = obj
            return obj.learning_id
        if isinstance(obj, DesignArtifact):
            self.design_artifacts[obj.artifact_id] = obj
            return obj.artifact_id
        raise TypeError(f"Unsupported object type: {type(obj)!r}")
