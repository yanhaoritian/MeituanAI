from __future__ import annotations

import re
from typing import Any, Dict, Tuple


class MemoryAgent:
    def build_query(self, *, message: str, last_query: str) -> Tuple[str, str]:
        text = (message or "").strip()
        if not last_query:
            return text, "standalone"
        reset_markers = ["新需求", "重新开始", "换个话题", "不聊点餐了", "清空会话", "从头开始"]
        if any(k in text for k in reset_markers):
            return text, "standalone_reset"

        followup_markers = ["换", "再来", "刚才", "上一个", "不要这个", "太贵", "近一点", "快一点", "改成", "不要", "别要"]
        if any(k in text for k in followup_markers) or len(text) <= 60:
            if any(k in text for k in ["更近", "近一点"]):
                text = f"{text}；优先距离更近"
            if any(k in text for k in ["更快", "快一点"]):
                text = f"{text}；优先送达更快"
            if any(k in text for k in ["更便宜", "太贵", "便宜点"]):
                text = f"{text}；优先价格更低"
            return f"{last_query}；{text}", "followup_merged"
        return text, "standalone"

    def hard_constraints(self, *, message: str) -> Dict[str, Any]:
        text = (message or "").strip()
        return {
            "require_closer": any(k in text for k in ["更近", "近一点", "离我近"]),
            "require_faster": any(k in text for k in ["更快", "快一点", "送达更快"]),
            "require_cheaper": any(k in text for k in ["更便宜", "便宜点", "太贵"]),
        }

    def update_memory(self, *, memory: Dict[str, Any], merged_query: str) -> Dict[str, Any]:
        out = dict(memory or {})
        q = merged_query or ""
        m_budget = re.search(r"预算\s*(\d+)", q)
        m_dist = re.search(r"(\d+(?:\.\d+)?)\s*(?:km|公里)", q)
        m_eta = re.search(r"(\d+)\s*分钟", q)
        if m_budget:
            out["budget_max"] = float(m_budget.group(1))
        if m_dist:
            out["distance_max_km"] = float(m_dist.group(1))
        if m_eta:
            out["delivery_eta_max_min"] = int(m_eta.group(1))
        tags = []
        for k in ["清淡", "不油腻", "酸辣", "麻辣", "高蛋白", "减脂", "暖胃"]:
            if k in q:
                tags.append(k)
        if tags:
            out["taste_tags"] = sorted(set((out.get("taste_tags") or []) + tags))
        return out

