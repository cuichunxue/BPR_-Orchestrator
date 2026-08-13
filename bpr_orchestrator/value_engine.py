"""Value Engine (design section 15).

Raw resource freed by an initiative (e.g. "1000 hours saved") must never be
monetized directly. It has to pass through a realization chain — can the
freed capacity actually be redeployed, does it reduce overtime, does it
avoid outsourcing, does it avoid a hire — before any of it becomes a cash
figure. Only the realized portion is counted; the rest stays as
`capacity_creation`, an unrealized, honestly-labeled number.
"""
from __future__ import annotations

from dataclasses import dataclass

from bpr_orchestrator.models import ValueBreakdown


@dataclass
class RealizationAssumptions:
    redeployable_fraction: float = 0.0     # freed hours actually reassigned to other value work
    overtime_reduction_fraction: float = 0.0
    outsourcing_reduction_fraction: float = 0.0
    hiring_avoidance_fraction: float = 0.0
    hourly_cost: float = 0.0                # currency per hour, for monetizing realized hours


def categorize_time_saved(
    hours_saved: float, assumptions: RealizationAssumptions
) -> ValueBreakdown:
    """Walk raw hours saved through the realization chain from design
    section 15 and split the result into value categories. Only the
    fraction that clears a realization pathway gets monetized; everything
    else is reported as unrealized capacity_creation."""

    fractions = {
        "redeployable": assumptions.redeployable_fraction,
        "overtime": assumptions.overtime_reduction_fraction,
        "outsourcing": assumptions.outsourcing_reduction_fraction,
        "hiring_avoidance": assumptions.hiring_avoidance_fraction,
    }
    total_claimed = sum(fractions.values())
    notes: list[str] = []
    if total_claimed > 1.0:
        notes.append(
            "realization fractions summed to "
            f"{total_claimed:.2f} > 1.0; capped at 1.0 to avoid double-counting"
        )
        scale = 1.0 / total_claimed
        fractions = {k: v * scale for k, v in fractions.items()}

    realized_hours = hours_saved * sum(fractions.values())
    unrealized_hours = hours_saved - realized_hours

    cash = realized_hours * assumptions.hourly_cost
    notes.append(
        f"{realized_hours:.1f}h of {hours_saved:.1f}h saved have a documented "
        "realization pathway (redeploy/overtime/outsourcing/hiring-avoidance); "
        f"{unrealized_hours:.1f}h remain unrealized capacity"
    )

    return ValueBreakdown(
        cash_saving=cash,
        capacity_creation=unrealized_hours,
        realization_notes=notes,
    )
