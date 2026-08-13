"""Three memory tiers (design section 21).

CaseMemory        - the current engagement's TransformationCase (owned by orchestrator.py)
EnterpriseMemory  - one client's durable facts: processes, systems, roles, KPIs,
                     risks, past initiatives. Never shared across clients.
GeneralPatternLibrary - cross-client, generalized patterns only
                     ("approval overload -> risk-based approval"). Entries are
                     stripped of any client-identifying detail before being
                     added here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class EnterpriseFact:
    category: str   # process / system / role / kpi / risk / past_initiative
    key: str
    value: Any


class EnterpriseMemory:
    """Durable, client-scoped memory. One instance per client."""

    def __init__(self, enterprise_id: str):
        self.enterprise_id = enterprise_id
        self._facts: dict[str, EnterpriseFact] = {}

    def remember(self, category: str, key: str, value: Any) -> None:
        self._facts[f"{category}:{key}"] = EnterpriseFact(category, key, value)

    def recall(self, category: str, key: str) -> Any | None:
        fact = self._facts.get(f"{category}:{key}")
        return fact.value if fact else None

    def all_in_category(self, category: str) -> list[EnterpriseFact]:
        return [f for f in self._facts.values() if f.category == category]


_IDENTIFYING_KEYS = {
    "enterprise_id", "company", "client", "customer_name", "person",
    "employee", "contact", "email", "domain", "url", "case_id",
}


def _strip_identifying_info(payload: dict) -> dict:
    return {k: v for k, v in payload.items() if k.lower() not in _IDENTIFYING_KEYS}


@dataclass
class Pattern:
    pattern_id: str
    trigger: str          # generalized symptom, e.g. "approval overload"
    resolution: str        # generalized fix, e.g. "risk-based approval"
    supporting_case_count: int = 1
    metadata: dict = field(default_factory=dict)


class GeneralPatternLibrary:
    """Cross-enterprise, anonymized pattern store. Only generalized
    trigger/resolution pairs are stored here — never raw case data."""

    def __init__(self):
        self._patterns: dict[str, Pattern] = {}

    def add_pattern(
        self, pattern_id: str, trigger: str, resolution: str, **metadata: Any
    ) -> Pattern:
        clean_metadata = _strip_identifying_info(metadata)
        if pattern_id in self._patterns:
            existing = self._patterns[pattern_id]
            existing.supporting_case_count += 1
            return existing
        pattern = Pattern(pattern_id, trigger, resolution, metadata=clean_metadata)
        self._patterns[pattern_id] = pattern
        return pattern

    def find_by_trigger(self, trigger_substring: str) -> list[Pattern]:
        needle = trigger_substring.lower()
        return [p for p in self._patterns.values() if needle in p.trigger.lower()]

    def all_patterns(self) -> list[Pattern]:
        return list(self._patterns.values())
