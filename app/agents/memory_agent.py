from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple


class MemoryAgent:
    def _extract_hard_constraints(self, text: str) -> List[str]:
        out: List[str] = []
        if any(k in text for k in ["不吃肉", "不要肉", "无肉", "不要荤", "不吃荤"]):
            out.append("no_meat")
        if any(k in text for k in ["素食", "素菜", "吃素", "全素"]):
            out.append("vegetarian")
        if any(k in text for k in ["不要生食", "不吃生食"]):
            out.append("no_raw")
        return sorted(set(out))

    def _extract_soft_preferences(self, text: str) -> List[str]:
        out: List[str] = []
        if any(k in text for k in ["更近", "近一点", "离我近"]):
            out.append("prefer_closer")
        if any(k in text for k in ["更快", "快一点", "送达更快"]):
            out.append("prefer_faster")
        if any(k in text for k in ["更便宜", "便宜点", "太贵"]):
            out.append("prefer_cheaper")
        return sorted(set(out))

    def _extract_transient_preferences(self, text: str) -> List[str]:
        out: List[str] = []
        if any(k in text for k in ["今天", "这顿"]) and any(k in text for k in ["热乎", "暖胃"]):
            out.append("heat_now")
        return out

    def constraint_layers(self, *, message: str, memory: Dict[str, Any] | None = None) -> Dict[str, List[str]]:
        text = (message or "").strip()
        prev_layers = ((memory or {}).get("constraint_layers") or {}) if isinstance(memory, dict) else {}
        prev_hard = list(prev_layers.get("hard", []) or [])
        prev_soft = list(prev_layers.get("soft", []) or [])

        new_hard = self._extract_hard_constraints(text)
        new_soft = self._extract_soft_preferences(text)
        new_transient = self._extract_transient_preferences(text)

        hard = sorted(set(prev_hard + new_hard))
        soft = new_soft if new_soft else prev_soft
        transient = new_transient
        return {"hard": hard, "soft": soft, "transient": transient}

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
            "relax_distance": any(k in text for k in ["远距离也可以", "远一点也行", "远点也行", "远一些也可以", "远点没事"]),
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
        out["constraint_layers"] = self.constraint_layers(message=q, memory=out)
        return out

