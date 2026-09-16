"""Deterministic deal valuation.

Pure function, no database and no provider calls — same rationale as
agents/lead_scoring/scoring.py: a number that decides whether a proposal needs approval
(policies.yaml's ``proposal_value_requires_approval_usd``) must be reproducible and
inspectable, not a matter of what a model estimated.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

#: Deterministic default: a freshly-qualified deal with a proposal not yet sent.
DEFAULT_PROBABILITY = 40

#: Fallback contract value bands when the opportunity has no estimate on file, keyed by
#: the same company-size signal Lead Scoring uses for fit.
_SIZE_BAND_VALUE_USD: dict[str, Decimal] = {
    "1-10": Decimal("1500"),
    "11-50": Decimal("5000"),
    "51-200": Decimal("15000"),
    "201-500": Decimal("30000"),
}
_UNKNOWN_SIZE_BAND_VALUE_USD = Decimal("2500")


@dataclass(frozen=True)
class ValuationInputs:
    estimated_opportunity_value_usd: Decimal | None
    size_band: str | None


@dataclass(frozen=True)
class ValuationResult:
    value_usd: Decimal
    probability: int
    rationale: dict[str, Any]


def value_deal(inputs: ValuationInputs) -> ValuationResult:
    if inputs.estimated_opportunity_value_usd is not None:
        value = inputs.estimated_opportunity_value_usd
        basis = f"opportunity estimated value ${value}"
    else:
        value = _SIZE_BAND_VALUE_USD.get(inputs.size_band or "", _UNKNOWN_SIZE_BAND_VALUE_USD)
        basis = f"no opportunity estimate on file; company size band {inputs.size_band!r} default"

    return ValuationResult(
        value_usd=value,
        probability=DEFAULT_PROBABILITY,
        rationale={
            "value_usd": str(value),
            "probability": DEFAULT_PROBABILITY,
            "basis": basis,
        },
    )
