"""Pure strike-selection logic for the expiry-day short strangle.

Dependency-free (stdlib only) so it is unit-testable without broker SDKs.

Premiums fall as an option moves further out-of-the-money. Given a per-side
premium threshold, we sell the *furthest*-OTM strike whose premium is still
strictly above the threshold — i.e. the option whose premium sits just above
the threshold (closest to it from above). This maximises distance from spot
while still collecting a premium the trader considers worthwhile.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Leg:
    strike: float
    ltp: float
    side: str  # "CE" or "PE"


def _pick_side(candidates: list[tuple[float, float | None]],
               threshold: float, furthest: str) -> tuple[float, float] | None:
    """Pick one (strike, ltp) from OTM candidates.

    candidates : list of (strike, ltp); ltp may be None (no quote) and is skipped.
    furthest   : "max" for CE (further OTM = higher strike),
                 "min" for PE (further OTM = lower strike).

    Returns the furthest-OTM candidate whose ltp > threshold. If none clears the
    threshold, falls back to the nearest-OTM candidate with the richest premium
    (so the strategy still fires) — the caller is expected to log this.
    """
    priced = [(s, l) for s, l in candidates if l is not None]
    if not priced:
        return None

    above = [(s, l) for s, l in priced if l > threshold]
    if above:
        chosen = max(above, key=lambda x: x[0]) if furthest == "max" else min(above, key=lambda x: x[0])
        return chosen

    # Nothing above threshold: the whole side is cheaper than the threshold.
    # Fall back to the richest (highest-premium) OTM strike, which is the one
    # nearest to spot.
    return max(priced, key=lambda x: x[1])


def select_strangle_legs(
    rows: list[dict], spot: float, ce_threshold: float, pe_threshold: float | None = None,
) -> tuple[Leg | None, Leg | None]:
    """Choose the CE and PE legs for a short strangle.

    rows : list of {"strike", "ce_ltp", "pe_ltp"} (ltp may be None).
    spot : underlying spot price; strikes strictly above are OTM calls,
           strictly below are OTM puts.
    ce_threshold / pe_threshold : per-side premium thresholds. pe_threshold
           defaults to ce_threshold.

    Returns (ce_leg, pe_leg); either may be None if that side has no usable data.
    """
    if pe_threshold is None:
        pe_threshold = ce_threshold

    ce_candidates = [(r["strike"], r.get("ce_ltp")) for r in rows if r["strike"] > spot]
    pe_candidates = [(r["strike"], r.get("pe_ltp")) for r in rows if r["strike"] < spot]

    ce_pick = _pick_side(ce_candidates, ce_threshold, furthest="max")
    pe_pick = _pick_side(pe_candidates, pe_threshold, furthest="min")

    ce_leg = Leg(strike=ce_pick[0], ltp=ce_pick[1], side="CE") if ce_pick else None
    pe_leg = Leg(strike=pe_pick[0], ltp=pe_pick[1], side="PE") if pe_pick else None
    return ce_leg, pe_leg
