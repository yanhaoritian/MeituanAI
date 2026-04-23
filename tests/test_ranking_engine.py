from app.schemas import ParsedQuery, ParsedSlots
from app.services.ranking_engine import filter_merchants, is_beverage_merchant, pick_recommended_dishes, rank_merchants


def _merchant(
    merchant_id: str,
    *,
    name: str,
    tags: list[str],
    diet_flags: list[str],
    description: str,
    recommended_dishes: list[str],
    rating: float = 4.5,
    distance_km: float = 1.0,
    avg_price: float = 28.0,
    delivery_eta_min: int = 30,
    is_open: bool = True,
) -> dict:
    return {
        "id": merchant_id,
        "name": name,
        "tags": tags,
        "diet_flags": diet_flags,
        "description": description,
        "recommended_dishes": recommended_dishes,
        "rating": rating,
        "distance_km": distance_km,
        "avg_price": avg_price,
        "delivery_eta_min": delivery_eta_min,
        "is_open": is_open,
    }


def test_is_beverage_merchant_distinguishes_drink_only_merchants() -> None:
    assert is_beverage_merchant(
        _merchant(
            "drink_1",
            name="奶茶铺",
            tags=["奶茶", "饮品"],
            diet_flags=[],
            description="主打果茶和奶茶",
            recommended_dishes=["招牌奶茶"],
        )
    )
    assert not is_beverage_merchant(
        _merchant(
            "meal_1",
            name="咖啡简餐",
            tags=["咖啡", "三明治", "主食"],
            diet_flags=[],
            description="咖啡和简餐都有",
            recommended_dishes=["鸡胸三明治"],
        )
    )


def test_filter_merchants_excludes_raw_food_when_requested() -> None:
    parsed = ParsedQuery(
        intent="order_food",
        slots=ParsedSlots(
            budget_max=40.0,
            distance_max_km=3.0,
            dietary_restrictions=["no_raw"],
        ),
        confidence=0.8,
        conflict_flags=[],
    )
    merchants = [
        _merchant(
            "safe_1",
            name="热汤面",
            tags=["汤面", "暖胃"],
            diet_flags=["hot_food"],
            description="热乎汤面",
            recommended_dishes=["鸡汤面"],
        ),
        _merchant(
            "raw_1",
            name="刺身饭",
            tags=["日料", "生食"],
            diet_flags=["raw_food"],
            description="主打刺身",
            recommended_dishes=["三文鱼饭"],
        ),
    ]

    filtered, debug = filter_merchants(parsed, merchants)

    assert [m["id"] for m in filtered] == ["safe_1"]
    assert debug["filtered_out"]["restriction"] == 1


def test_rank_merchants_prioritizes_health_friendly_merchant_for_diet_query() -> None:
    parsed = ParsedQuery(
        intent="order_food",
        slots=ParsedSlots(
            taste=["减脂", "高蛋白", "清淡"],
            budget_max=30.0,
            distance_max_km=3.0,
            dietary_restrictions=["high_protein", "low_carb"],
        ),
        confidence=0.8,
        conflict_flags=[],
    )
    merchants = [
        _merchant(
            "healthy_1",
            name="轻食蛋白碗",
            tags=["轻食", "高蛋白", "减脂", "清淡"],
            diet_flags=["high_protein", "low_carb", "low_oil"],
            description="高蛋白低脂餐，适合减脂",
            recommended_dishes=["鸡胸藜麦碗"],
            rating=4.5,
            distance_km=1.0,
            avg_price=28.0,
        ),
        _merchant(
            "heavy_1",
            name="炸鸡奶茶双拼",
            tags=["炸鸡", "奶茶", "重口"],
            diet_flags=[],
            description="高油高热量快乐餐",
            recommended_dishes=["炸鸡套餐"],
            rating=4.7,
            distance_km=0.8,
            avg_price=25.0,
        ),
    ]

    ranked = rank_merchants(parsed, merchants, semantic_scores={})

    assert ranked[0]["id"] == "healthy_1"
    assert ranked[0]["score_breakdown"]["preference"] > ranked[1]["score_breakdown"]["preference"]


def test_filter_merchants_excludes_meat_heavy_when_no_meat_requested() -> None:
    parsed = ParsedQuery(
        intent="order_food",
        slots=ParsedSlots(
            budget_max=40.0,
            distance_max_km=3.0,
            dietary_restrictions=["no_meat", "vegetarian"],
        ),
        confidence=0.8,
        conflict_flags=[],
    )
    merchants = [
        _merchant(
            "veg_1",
            name="素食小馆",
            tags=["素食", "清淡"],
            diet_flags=["vegetarian_friendly", "low_oil"],
            description="主打素菜和豆制品",
            recommended_dishes=["香菇青菜面", "番茄豆腐煲"],
        ),
        _merchant(
            "meat_1",
            name="牛肉饭",
            tags=["牛肉", "盖饭"],
            diet_flags=[],
            description="招牌牛肉饭和鸡腿饭",
            recommended_dishes=["黑椒牛肉饭", "鸡腿饭"],
        ),
    ]

    filtered, debug = filter_merchants(parsed, merchants)

    assert [m["id"] for m in filtered] == ["veg_1"]
    assert debug["filtered_out"]["restriction"] == 1


def test_pick_recommended_dishes_prefers_meat_dishes_when_user_requests_meat() -> None:
    parsed = ParsedQuery(
        intent="order_food",
        slots=ParsedSlots(
            taste=[],
            category=[],
            budget_max=40.0,
            distance_max_km=3.0,
            dietary_restrictions=["prefer_meat"],
        ),
        confidence=0.8,
        conflict_flags=[],
    )
    merchant = _merchant(
        "mix_1",
        name="家常馆",
        tags=["家常菜"],
        diet_flags=[],
        description="荤素都有",
        recommended_dishes=["香菇青菜饭", "红烧肉盖饭", "土豆牛肉饭"],
    )

    dishes = pick_recommended_dishes(parsed, merchant, top_k=2)
    assert "红烧肉盖饭" in dishes or "土豆牛肉饭" in dishes
