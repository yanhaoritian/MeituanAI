from app.services.query_parser import parse_query


def test_parse_query_extracts_budget_distance_eta_and_preferences() -> None:
    parsed = parse_query("预算30以内，2公里内送达，减脂，高蛋白，低碳，清淡")

    assert parsed.intent == "order_food"
    assert parsed.slots.budget_max == 30.0
    assert parsed.slots.distance_max_km == 2.0
    assert parsed.slots.delivery_eta_max_min is None
    assert "减脂" in parsed.slots.taste
    assert "高蛋白" in parsed.slots.taste
    assert "低碳" in parsed.slots.taste
    assert "清淡" in parsed.slots.taste
    assert "high_protein" in parsed.slots.dietary_restrictions
    assert "low_carb" in parsed.slots.dietary_restrictions


def test_parse_query_uses_reasonable_defaults_when_missing() -> None:
    parsed = parse_query("随便吃点热乎的")

    assert parsed.slots.budget_max == 35.0
    assert parsed.slots.distance_max_km == 3.0
    assert "热乎" in parsed.slots.taste


def test_parse_query_marks_conflicts() -> None:
    parsed = parse_query("便宜一点，但也想要高端精致点，不要生食但又想吃生食")

    assert "cheap_vs_premium" in parsed.conflict_flags
    assert "raw_food_conflict" in parsed.conflict_flags


def test_parse_query_extracts_directional_vegetarian_constraints() -> None:
    parsed = parse_query("今天想吃素菜，不吃肉，最好清淡一点")

    assert "vegetarian" in parsed.slots.dietary_restrictions
    assert "no_meat" in parsed.slots.dietary_restrictions
    assert "清淡" in parsed.slots.taste


def test_parse_query_extracts_meat_and_fast_intent() -> None:
    parsed = parse_query("想吃肉，送得快")

    assert "prefer_meat" in parsed.slots.dietary_restrictions
    assert parsed.slots.delivery_eta_max_min == 40


def test_parse_query_extracts_relaxed_distance_intent() -> None:
    parsed = parse_query("远距离也可以，再推荐一家")
    assert parsed.slots.distance_max_km == 8.0
