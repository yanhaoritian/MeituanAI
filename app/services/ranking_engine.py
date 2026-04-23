from __future__ import annotations

import random
from typing import Dict, List, Tuple

from app.schemas import ParsedQuery


def _meat_keyword_hits(merchant: Dict) -> int:
    text = (
        f"{merchant.get('name', '')} "
        f"{' '.join(merchant.get('tags', []))} "
        f"{merchant.get('description', '')} "
        f"{' '.join(merchant.get('recommended_dishes', []))}"
    ).lower()
    meat_keywords = [
        "牛肉",
        "鸡肉",
        "猪肉",
        "羊肉",
        "排骨",
        "烤肉",
        "肥牛",
        "鸡腿",
        "beef",
        "chicken",
        "pork",
        "lamb",
    ]
    return sum(1 for k in meat_keywords if k in text)


def _is_vegetarian_friendly(merchant: Dict) -> bool:
    text = (
        f"{merchant.get('name', '')} "
        f"{' '.join(merchant.get('tags', []))} "
        f"{merchant.get('description', '')} "
        f"{' '.join(merchant.get('recommended_dishes', []))}"
    ).lower()
    diet_flags = set(str(x) for x in merchant.get("diet_flags", []))
    veg_keywords = ["素", "素食", "蔬菜", "豆腐", "菌菇", "vegetarian", "vegan"]
    return "vegetarian_friendly" in diet_flags or any(k in text for k in veg_keywords)


def is_beverage_merchant(merchant: Dict) -> bool:
    text = (
        f"{merchant.get('name', '')} "
        f"{' '.join(merchant.get('tags', []))} "
        f"{merchant.get('description', '')}"
    ).lower()
    beverage_keywords = [
        "奶茶",
        "咖啡",
        "茶饮",
        "饮品",
        "果茶",
        "milk tea",
        "coffee",
        "drink",
        "星巴克",
        "瑞幸",
        "costa",
        "manner",
    ]
    meal_keywords = ["盖饭", "炒饭", "面", "粉", "粥", "汤", "汉堡", "披萨", "饺子", "米饭", "主食", "便当"]
    has_beverage = any(k in text for k in beverage_keywords)
    has_meal = any(k in text for k in meal_keywords)
    return has_beverage and not has_meal


def _should_exclude_beverage(parsed: ParsedQuery) -> bool:
    flags = set(parsed.conflict_flags or [])
    if "explicit_drink_intent" in flags:
        return False
    return "implicit_meal_intent" in flags


def _text_overlap_score(parsed: ParsedQuery, merchant: Dict) -> float:
    terms = parsed.slots.taste + parsed.slots.category
    if not terms:
        return 0.5
    haystack = " ".join(merchant.get("tags", [])) + " " + merchant.get("description", "")
    hit = sum(1 for t in terms if t in haystack)
    return min(1.0, hit / max(len(terms), 1))


def _merchant_health_score(parsed: ParsedQuery, merchant: Dict) -> float:
    text = (
        f"{merchant.get('name', '')} "
        f"{' '.join(merchant.get('tags', []))} "
        f"{merchant.get('description', '')} "
        f"{' '.join(merchant.get('recommended_dishes', []))}"
    ).lower()
    diet_flags = set(str(x) for x in merchant.get("diet_flags", []))
    tastes = set(parsed.slots.taste or [])
    restrictions = set(parsed.slots.dietary_restrictions or [])

    score = 0.5

    if any(k in tastes for k in ["减脂", "高蛋白"]):
        if "high_protein" in diet_flags:
            score += 0.22
        if "low_carb" in diet_flags:
            score += 0.18
        if any(k in text for k in ["轻食", "沙拉", "鸡胸", "藜麦", "低脂", "蛋白"]):
            score += 0.14

    if any(k in tastes for k in ["清淡", "不油腻", "暖胃"]):
        if "low_oil" in diet_flags:
            score += 0.2
        if "hot_food" in diet_flags and "暖胃" in tastes:
            score += 0.08
        if any(k in text for k in ["清淡", "少油", "低油", "汤", "粥", "暖胃", "清汤"]):
            score += 0.12

    if "low_carb" in restrictions and "low_carb" in diet_flags:
        score += 0.15
    if "high_protein" in restrictions and "high_protein" in diet_flags:
        score += 0.15
    if "prefer_meat" in restrictions:
        if _meat_keyword_hits(merchant) > 0:
            score += 0.18
        if _is_vegetarian_friendly(merchant):
            score -= 0.08
    if "vegetarian" in restrictions or "no_meat" in restrictions:
        if _is_vegetarian_friendly(merchant):
            score += 0.2
        if _meat_keyword_hits(merchant) > 0:
            score -= 0.25

    if any(k in tastes for k in ["减脂", "清淡", "不油腻", "高蛋白"]):
        if any(k in text for k in ["炸鸡", "奶茶", "烧烤", "肥牛拌饭", "重口", "油炸"]):
            score -= 0.18

    return max(0.0, min(1.0, score))


def _normalize_rating(rating: float) -> float:
    return max(0.0, min(1.0, rating / 5.0))


def _normalize_distance(distance_km: float, max_km: float) -> float:
    if max_km <= 0:
        return 0.0
    return max(0.0, min(1.0, 1 - (distance_km / max_km)))


def _normalize_price(price: float, budget: float) -> float:
    if budget <= 0:
        return 0.0
    ratio = price / budget
    if ratio <= 1:
        return 1 - abs(1 - ratio) * 0.4
    return max(0.0, 1 - (ratio - 1))


def filter_merchants(parsed: ParsedQuery, merchants: List[Dict]) -> Tuple[List[Dict], Dict]:
    filtered = []
    reasons = {"filtered_out": {"price": 0, "distance": 0, "eta": 0, "restriction": 0, "closed": 0, "beverage": 0}}
    exclude_beverage = _should_exclude_beverage(parsed)

    for m in merchants:
        if not m.get("is_open", False):
            reasons["filtered_out"]["closed"] += 1
            continue
        if exclude_beverage and is_beverage_merchant(m):
            reasons["filtered_out"]["beverage"] += 1
            continue
        if float(m["avg_price"]) > float(parsed.slots.budget_max):
            reasons["filtered_out"]["price"] += 1
            continue
        if float(m["distance_km"]) > float(parsed.slots.distance_max_km):
            reasons["filtered_out"]["distance"] += 1
            continue
        if parsed.slots.delivery_eta_max_min and int(m["delivery_eta_min"]) > int(parsed.slots.delivery_eta_max_min):
            reasons["filtered_out"]["eta"] += 1
            continue
        if "no_raw" in parsed.slots.dietary_restrictions and "raw_food" in m.get("diet_flags", []):
            reasons["filtered_out"]["restriction"] += 1
            continue
        if "no_meat" in parsed.slots.dietary_restrictions:
            if _meat_keyword_hits(m) > 0 and not _is_vegetarian_friendly(m):
                reasons["filtered_out"]["restriction"] += 1
                continue

        filtered.append(m)

    return filtered, reasons


def rank_merchants(parsed: ParsedQuery, merchants: List[Dict], semantic_scores: Dict[str, float] | None = None) -> List[Dict]:
    ranked = []
    budget = float(parsed.slots.budget_max)
    max_km = float(parsed.slots.distance_max_km)

    for m in merchants:
        mid = str(m.get("id", ""))
        if semantic_scores and mid in semantic_scores:
            s_semantic = float(semantic_scores[mid])
            semantic_source = "vector"
        else:
            s_semantic = _text_overlap_score(parsed, m)
            semantic_source = "keyword_overlap"
        s_rating = _normalize_rating(float(m["rating"]))
        s_distance = _normalize_distance(float(m["distance_km"]), max_km)
        s_price = _normalize_price(float(m["avg_price"]), budget)
        s_pref = _merchant_health_score(parsed, m)

        total = 0.30 * s_semantic + 0.22 * s_rating + 0.18 * s_distance + 0.14 * s_price + 0.16 * s_pref
        ranked.append(
            {
                **m,
                "score": round(total, 4),
                "score_breakdown": {
                    "semantic": round(s_semantic, 4),
                    "semantic_source": semantic_source,
                    "rating": round(s_rating, 4),
                    "distance": round(s_distance, 4),
                    "price": round(s_price, 4),
                    "preference": round(s_pref, 4),
                },
            }
        )

    ranked.sort(key=lambda x: x["score"], reverse=True)
    return ranked


def build_reason(parsed: ParsedQuery, merchant: Dict) -> str:
    reasons = []
    hits = _matched_terms(parsed, merchant)
    if hits:
        reasons.append(f"命中你的偏好关键词：{'、'.join(hits[:3])}")

    if merchant["avg_price"] <= parsed.slots.budget_max:
        reasons.append(f"人均{merchant['avg_price']}元，符合你的预算")
    if merchant["distance_km"] <= parsed.slots.distance_max_km:
        reasons.append(f"距离约{merchant['distance_km']}km，取餐范围内")
    if parsed.slots.delivery_eta_max_min and merchant["delivery_eta_min"] <= parsed.slots.delivery_eta_max_min:
        reasons.append(f"预计{merchant['delivery_eta_min']}分钟送达，时效友好")

    rating = float(merchant.get("rating", 0))
    if rating >= 4.7:
        reasons.append(f"评分{rating:.1f}，口碑稳定")

    dishes = merchant.get("recommended_dishes", []) or []
    if dishes:
        reasons.append(f"这家主推“{dishes[0]}”")

    if not reasons:
        reasons.append("综合评分、距离和价格平衡后表现更优")

    # Keep short and readable with mild phrase variety.
    openers = ["推荐这家是因为", "更偏向这家的原因是", "优先推荐它，主要因为"]
    opener = openers[abs(hash(merchant.get("id", ""))) % len(openers)]
    return f"{opener}，" + "；".join(reasons[:3]) + "。"


def _matched_terms(parsed: ParsedQuery, merchant: Dict) -> List[str]:
    terms = parsed.slots.taste + parsed.slots.category
    haystack = " ".join(merchant.get("tags", [])) + " " + merchant.get("description", "")
    return [t for t in terms if t in haystack]


def reason_evidence(parsed: ParsedQuery, merchant: Dict, recommended_dishes: List[str]) -> Dict:
    return {
        "matched_terms": _matched_terms(parsed, merchant),
        "budget_ok": merchant.get("avg_price", 0) <= (parsed.slots.budget_max or 0),
        "distance_ok": merchant.get("distance_km", 0) <= (parsed.slots.distance_max_km or 0),
        "eta_ok": (
            True
            if not parsed.slots.delivery_eta_max_min
            else merchant.get("delivery_eta_min", 999) <= parsed.slots.delivery_eta_max_min
        ),
        "top_dish": recommended_dishes[0] if recommended_dishes else "",
        "rating": merchant.get("rating"),
    }


def pick_recommended_dishes(parsed: ParsedQuery, merchant: Dict, top_k: int = 3) -> List[str]:
    dishes = merchant.get("recommended_dishes", []) or []
    if not dishes:
        return []

    terms = parsed.slots.taste + parsed.slots.category
    no_meat_mode = "no_meat" in set(parsed.slots.dietary_restrictions or [])
    prefer_meat_mode = "prefer_meat" in set(parsed.slots.dietary_restrictions or [])
    meat_dish_keywords = ["牛", "鸡", "猪", "羊", "排骨", "肥牛", "beef", "chicken", "pork", "lamb"]
    if not terms and not no_meat_mode and not prefer_meat_mode:
        return dishes[:top_k]

    scored = []
    for d in dishes:
        score = sum(1 for t in terms if t in d)
        if no_meat_mode and any(k in str(d).lower() for k in meat_dish_keywords):
            score -= 2
        if prefer_meat_mode and any(k in str(d).lower() for k in meat_dish_keywords):
            score += 2
        score += random.uniform(0, 0.01)
        scored.append((score, d))
    scored.sort(reverse=True, key=lambda x: x[0])
    return [d for _, d in scored[:top_k]]
