from __future__ import annotations

from typing import List, Optional

from app.agents.contracts import RetrievalResult
from app.schemas import Location, RecommendRequest
from app.services.recommender import RecommenderService


class RetrievalAgent:
    def __init__(self, recommender: RecommenderService) -> None:
        self._recommender = recommender

    def _apply_hard_closer_filter(
        self,
        *,
        recs: List[dict],
        selected_snapshot: List[dict],
        require_closer_than_km: float | None,
    ) -> tuple[List[dict], bool]:
        if require_closer_than_km is None:
            return recs, False
        distance_map = {str(x.get("merchant_id", "")): float(x.get("distance_km", 999.0)) for x in (selected_snapshot or [])}
        filtered = [r for r in recs if distance_map.get(str(r.get("merchant_id", "")), 999.0) < require_closer_than_km]
        return filtered, True

    def recommend_with_scope_fallback(
        self,
        *,
        user_id: str,
        merged_query: str,
        location: Optional[Location],
        scope_ids: List[str],
        exclude_ids: List[str],
        fast_mode: bool,
        require_closer_than_km: float | None = None,
    ) -> RetrievalResult:
        scoped_req = RecommendRequest(
            user_id=user_id,
            query=merged_query,
            location=location,
            merchant_scope_ids=scope_ids,
            exclude_merchant_ids=exclude_ids,
            fast_mode=fast_mode,
        )
        scoped_result = self._recommender.recommend(scoped_req)
        recs = [x.model_dump() for x in scoped_result.recommendations]
        scoped_snapshot = (scoped_result.debug or {}).get("selected_snapshot", [])
        recs, hard_applied = self._apply_hard_closer_filter(
            recs=recs,
            selected_snapshot=scoped_snapshot,
            require_closer_than_km=require_closer_than_km,
        )
        if recs:
            return RetrievalResult(
                recommendations=recs,
                trace_id=scoped_result.trace_id,
                scope_debug={
                    "scope_locked": bool(scope_ids),
                    "scope_fallback_unlocked": False,
                    "hard_closer_applied": hard_applied,
                    "require_closer_than_km": require_closer_than_km,
                    "recommend_debug": scoped_result.debug or {},
                },
            )

        if not scope_ids:
            return RetrievalResult(
                recommendations=[],
                trace_id=scoped_result.trace_id,
                scope_debug={
                    "scope_locked": False,
                    "scope_fallback_unlocked": False,
                    "recommend_debug": scoped_result.debug or {},
                },
            )

        global_req = RecommendRequest(
            user_id=user_id,
            query=merged_query,
            location=location,
            merchant_scope_ids=[],
            exclude_merchant_ids=exclude_ids,
            fast_mode=fast_mode,
        )
        global_result = self._recommender.recommend(global_req)
        global_recs = [x.model_dump() for x in global_result.recommendations]
        global_snapshot = (global_result.debug or {}).get("selected_snapshot", [])
        global_recs, hard_applied_global = self._apply_hard_closer_filter(
            recs=global_recs,
            selected_snapshot=global_snapshot,
            require_closer_than_km=require_closer_than_km,
        )
        return RetrievalResult(
            recommendations=global_recs,
            trace_id=global_result.trace_id,
            scope_debug={
                "scope_locked": True,
                "scope_fallback_unlocked": True,
                "hard_closer_applied": hard_applied_global,
                "require_closer_than_km": require_closer_than_km,
                "recommend_debug": global_result.debug or {},
            },
        )

