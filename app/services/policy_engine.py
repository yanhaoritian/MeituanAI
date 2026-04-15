from __future__ import annotations

from typing import Dict, List, Tuple

from app.schemas import ParsedQuery


def _percentile(values: List[float], q: float) -> float:
    if not values:
        return 25.0
    ordered = sorted(values)
    idx = int((len(ordered) - 1) * q)
    return ordered[idx]


def apply_defaults_and_policy(parsed: ParsedQuery, merchants: List[Dict]) -> Tuple[ParsedQuery, Dict]:
    debug = {"policy_notes": []}
    prices = [float(m["avg_price"]) for m in merchants]

    if parsed.slots.budget_max is None:
        parsed.slots.budget_max = 35.0
        debug["policy_notes"].append("budget_default_35")

    if "便宜" in "".join(parsed.slots.taste):
        cheap_threshold = min(_percentile(prices, 0.25), 25.0)
        parsed.slots.budget_max = min(parsed.slots.budget_max, cheap_threshold)
        debug["policy_notes"].append("cheap_dynamic_threshold")

    if parsed.slots.distance_max_km is None:
        parsed.slots.distance_max_km = 3.0
        debug["policy_notes"].append("distance_default_3km")

    if "cheap_vs_premium" in parsed.conflict_flags:
        debug["policy_notes"].append("conflict_budget_first")

    return parsed, debug
