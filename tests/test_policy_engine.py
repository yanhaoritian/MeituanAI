from app.schemas import ParsedQuery, ParsedSlots
from app.services.policy_engine import apply_defaults_and_policy


def _merchant(avg_price: float) -> dict:
    return {"avg_price": avg_price}


def test_policy_applies_defaults() -> None:
    parsed = ParsedQuery(
        intent="order_food",
        slots=ParsedSlots(),
        confidence=0.8,
        conflict_flags=[],
    )

    updated, debug = apply_defaults_and_policy(parsed, [_merchant(20), _merchant(30), _merchant(40)])

    assert updated.slots.budget_max == 35.0
    assert updated.slots.distance_max_km == 3.0
    assert "budget_default_35" in debug["policy_notes"]
    assert "distance_default_3km" in debug["policy_notes"]


def test_policy_tightens_budget_for_cheap_intent() -> None:
    parsed = ParsedQuery(
        intent="order_food",
        slots=ParsedSlots(taste=["便宜"], budget_max=40.0),
        confidence=0.8,
        conflict_flags=["cheap_vs_premium"],
    )

    updated, debug = apply_defaults_and_policy(parsed, [_merchant(18), _merchant(22), _merchant(28), _merchant(36)])

    assert updated.slots.budget_max <= 25.0
    assert "cheap_dynamic_threshold" in debug["policy_notes"]
    assert "conflict_budget_first" in debug["policy_notes"]
