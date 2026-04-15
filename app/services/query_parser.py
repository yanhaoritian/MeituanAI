import re
from typing import List

from app.schemas import ParsedQuery, ParsedSlots


def _extract_budget(text: str) -> float | None:
    patterns = [
        r"预算\s*(\d+)\s*(?:元|块)?(?:以内|以下|之内)?",
        r"(?:不超过|别超过|最多)\s*(\d+)\s*(?:元|块)?",
        r"(\d+)\s*(?:元|块)\s*(?:以内|以下|之内)",
        r"人均\s*(\d+)\s*(?:元|块)?",
        r"(\d+)\s*(?:元|块)",
    ]
    for p in patterns:
        match = re.search(p, text)
        if match:
            return float(match.group(1))
    if "便宜" in text or "实惠" in text:
        return 25.0
    if "大众" in text or "随便吃点" in text:
        return 35.0
    return None


def _extract_distance(text: str) -> float | None:
    match_km = re.search(r"(\d+(?:\.\d+)?)\s*(?:km|KM|公里)", text)
    if match_km:
        return float(match_km.group(1))

    match_m = re.search(r"(\d+)\s*米", text)
    if match_m:
        return max(0.3, float(match_m.group(1)) / 1000.0)

    if "附近" in text or "就近" in text:
        return 3.0
    if "远点没事" in text or "远一点也行" in text:
        return 8.0
    return None


def _extract_tastes(text: str) -> List[str]:
    vocab = ["清淡", "酸辣", "暖胃", "不油腻", "热乎", "高蛋白", "减脂", "低碳", "便宜", "实惠", "高端"]
    return [term for term in vocab if term in text]


def _extract_categories(text: str) -> List[str]:
    vocab = ["汤面", "面", "米线", "轻食", "沙拉", "粥", "家常菜", "日料", "拌饭", "烤鸡"]
    return [term for term in vocab if term in text]


def _extract_delivery_eta(text: str) -> int | None:
    match = re.search(r"(\d+)\s*分钟(?:内|以内|送达)?", text)
    if match:
        return int(match.group(1))
    if "送达快" in text or "快一点" in text or "尽快" in text:
        return 40
    return None


def _extract_restrictions(text: str) -> List[str]:
    restrictions = []
    if "不要生食" in text or "不吃生食" in text:
        restrictions.append("no_raw")
    if "高蛋白" in text:
        restrictions.append("high_protein")
    if "低碳" in text:
        restrictions.append("low_carb")
    return restrictions


def _extract_intent(text: str) -> str:
    if any(k in text for k in ["改一下", "换一个", "换家", "不要这个"]):
        return "modify_preference"
    if any(k in text for k in ["你好", "在吗", "谢谢"]):
        return "chitchat"
    return "order_food"


def parse_query(query: str) -> ParsedQuery:
    budget = _extract_budget(query)
    distance_max_km = _extract_distance(query)
    tastes = _extract_tastes(query)
    categories = _extract_categories(query)
    restrictions = _extract_restrictions(query)
    delivery_eta_max_min = _extract_delivery_eta(query)
    conflict_flags = []

    if distance_max_km is None:
        distance_max_km = 3.0

    if "便宜" in query and ("高端" in query or "精致" in query):
        conflict_flags.append("cheap_vs_premium")
    if ("要生食" in query or "想吃生食" in query) and ("不要生食" in query or "不吃生食" in query):
        conflict_flags.append("raw_food_conflict")

    slots = ParsedSlots(
        taste=tastes,
        category=categories,
        budget_max=budget,
        distance_max_km=distance_max_km,
        delivery_eta_max_min=delivery_eta_max_min,
        dietary_restrictions=restrictions,
    )

    return ParsedQuery(
        intent=_extract_intent(query),
        slots=slots,
        confidence=0.8,
        conflict_flags=conflict_flags,
    )
