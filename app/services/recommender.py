from __future__ import annotations

import uuid
import os
import re
from datetime import datetime
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Tuple

from app.schemas import FeedbackResponse, RecommendationItem, RecommendRequest, RecommendResponse
from app.services.amap_service import AmapPoiService
from app.services.data_repository import MerchantRepository
from app.services.llm_parser import parse_query_by_llm
from app.services.llm_reasoner import generate_reason_by_llm
from app.services.menu_service import MenuService
from app.services.metrics_logger import ReasonMetricsLogger
from app.services.policy_engine import apply_defaults_and_policy
from app.services.profile_service import ProfileService
from app.services.query_parser import apply_directional_constraints, parse_query
from app.services.ranking_engine import (
    build_reason,
    filter_merchants,
    is_beverage_merchant,
    pick_recommended_dishes,
    rank_merchants,
    reason_evidence,
)
from app.services.semantic_service import SemanticService


class RecommenderService:
    def __init__(
        self,
        repository: MerchantRepository,
        profile_service: ProfileService,
        amap_service: AmapPoiService,
        menu_service: MenuService,
        metrics_logger: ReasonMetricsLogger,
    ) -> None:
        self._repository = repository
        self._profile_service = profile_service
        self._amap_service = amap_service
        self._menu_service = menu_service
        self._metrics_logger = metrics_logger
        self._semantic_service = SemanticService()

    def _multi_recall_enabled(self) -> bool:
        return os.getenv("ENABLE_MULTI_RECALL", "false").lower() == "true"

    def _term_hit_count(self, parsed, merchant: Dict) -> int:
        terms = list(parsed.slots.taste or []) + list(parsed.slots.category or [])
        if "no_meat" in (parsed.slots.dietary_restrictions or []):
            terms.extend(["素", "豆腐", "蔬菜", "菌菇"])
        haystack = (
            f"{merchant.get('name', '')} "
            f"{' '.join(merchant.get('tags', []))} "
            f"{merchant.get('description', '')}"
        )
        return sum(1 for t in terms if t and t in haystack)

    def _build_multi_recall_pool(
        self,
        *,
        user_id: str,
        parsed,
        candidates: List[Dict],
        semantic_scores: Dict[str, float],
    ) -> Tuple[List[Dict], Dict]:
        if not candidates:
            return candidates, {"enabled": False, "reason": "no_candidates"}
        profile = self._profile_service.get_profile(user_id)
        liked_ids = set(str(x) for x in (profile.get("liked_merchants") or []))

        rule_top_k = int(os.getenv("MULTI_RECALL_RULE_TOP_K", "80"))
        semantic_top_k = int(os.getenv("MULTI_RECALL_SEMANTIC_TOP_K", "50"))
        preference_top_k = int(os.getenv("MULTI_RECALL_PREF_TOP_K", "30"))

        by_id = {str(m.get("id", "")): m for m in candidates}
        rule_sorted = sorted(
            candidates,
            key=lambda m: (self._term_hit_count(parsed, m), float(m.get("rating", 0.0))),
            reverse=True,
        )
        semantic_sorted = sorted(
            candidates,
            key=lambda m: float(semantic_scores.get(str(m.get("id", "")), 0.0)),
            reverse=True,
        )
        pref_sorted = [m for m in candidates if str(m.get("id", "")) in liked_ids]

        route_rule = [str(m.get("id", "")) for m in rule_sorted[: max(0, rule_top_k)]]
        route_semantic = [str(m.get("id", "")) for m in semantic_sorted[: max(0, semantic_top_k)]]
        route_pref = [str(m.get("id", "")) for m in pref_sorted[: max(0, preference_top_k)]]

        merged_ids: List[str] = []
        for route in (route_pref, route_semantic, route_rule):
            for mid in route:
                if mid and mid not in merged_ids and mid in by_id:
                    merged_ids.append(mid)
        if not merged_ids:
            return candidates, {"enabled": True, "reason": "empty_merged_use_all"}

        min_pool = min(len(candidates), max(30, len(route_pref) + len(route_semantic)))
        if len(merged_ids) < min_pool:
            for m in rule_sorted:
                mid = str(m.get("id", ""))
                if mid not in merged_ids:
                    merged_ids.append(mid)
                if len(merged_ids) >= min_pool:
                    break

        pool = [by_id[mid] for mid in merged_ids if mid in by_id]
        debug = {
            "enabled": True,
            "input_count": len(candidates),
            "pool_count": len(pool),
            "route_counts": {
                "preference": len(route_pref),
                "semantic": len(route_semantic),
                "rule": len(route_rule),
            },
        }
        return pool, debug

    def _apply_implicit_meal_policy(self, query: str, parsed) -> List[str]:
        """
        Turn user subtext into explicit ranking/filter flags.
        meal_intent: user likely wants proper meal, avoid beverage-only stores.
        drink_intent: user explicitly asks for coffee/tea drinks.
        """
        q = (query or "").strip()
        notes: List[str] = []
        if not q:
            return notes

        meal_keywords = [
            "吃",
            "饭",
            "午饭",
            "午餐",
            "晚饭",
            "晚餐",
            "正餐",
            "主食",
            "饿",
            "垫肚子",
            "顶饱",
            "热乎",
            "暖胃",
        ]
        meal_flavor_keywords = [
            "清淡",
            "不油腻",
            "少油",
            "少盐",
            "酸辣",
            "麻辣",
            "鲜香",
            "暖胃",
            "高蛋白",
            "减脂",
        ]
        strong_meal_keywords = ["吃饭", "来点饭", "正餐", "主食", "垫肚子", "顶饱", "管饱", "先吃点东西"]
        drink_keywords = [
            "喝",
            "饮品",
            "奶茶",
            "咖啡",
            "果茶",
            "拿铁",
            "美式",
            "柠檬茶",
            "茶饮",
        ]
        explicit_no_drink = ["不要饮品", "不喝奶茶", "不要奶茶", "不要咖啡", "别推荐饮品"]

        meal_intent = any(k in q for k in meal_keywords)
        # "清淡/不油腻/减脂" etc are usually dish constraints instead of drink intent.
        if any(k in q for k in meal_flavor_keywords):
            meal_intent = True
        drink_intent = any(k in q for k in drink_keywords)
        no_drink_intent = any(k in q for k in explicit_no_drink)

        # If both appear, prefer explicit drink intent unless user says "不要饮品".
        if no_drink_intent:
            drink_intent = False
            meal_intent = True

        flags = set(parsed.conflict_flags or [])
        if meal_intent and not drink_intent:
            flags.add("implicit_meal_intent")
            notes.append("implicit_meal_intent")
        if any(k in q for k in strong_meal_keywords) and not drink_intent:
            flags.add("strong_meal_intent")
            notes.append("strong_meal_intent")
        if drink_intent:
            flags.add("explicit_drink_intent")
            notes.append("explicit_drink_intent")

        parsed.conflict_flags = list(flags)
        return notes

    def _fallback_rank_with_beverage_backup(
        self,
        *,
        parsed,
        merchants: List[Dict],
        strict_ranked: List[Dict],
        top_n: int,
    ) -> tuple[List[Dict], List[str], Dict]:
        notes: List[str] = []
        backup_debug: Dict = {"enabled": False, "backup_beverage_count": 0}
        flags = set(parsed.conflict_flags or [])
        if "implicit_meal_intent" not in flags or "explicit_drink_intent" in flags:
            return strict_ranked, notes, backup_debug

        beverage_cap = int(os.getenv("MEAL_INTENT_BEVERAGE_BACKUP_CAP", "1"))
        if beverage_cap <= 0:
            return strict_ranked, notes, backup_debug
        if len(strict_ranked) >= top_n:
            return strict_ranked, notes, backup_debug

        relaxed_parsed = deepcopy(parsed)
        relaxed_parsed.conflict_flags = [f for f in parsed.conflict_flags if f != "implicit_meal_intent"]
        relaxed_candidates, _ = filter_merchants(relaxed_parsed, merchants)
        relaxed_ranked = rank_merchants(relaxed_parsed, relaxed_candidates)

        strict_ids = {m["id"] for m in strict_ranked}
        beverage_backups = [m for m in relaxed_ranked if m["id"] not in strict_ids and is_beverage_merchant(m)]
        if not beverage_backups:
            return strict_ranked, notes, backup_debug

        merged = list(strict_ranked)
        added = 0
        for m in beverage_backups:
            if added >= beverage_cap:
                break
            merged.append(m)
            added += 1

        merged.sort(key=lambda x: x["score"], reverse=True)
        notes.append(f"meal_intent_beverage_backup_cap_{added}")
        backup_debug = {"enabled": True, "backup_beverage_count": added}
        return merged, notes, backup_debug

    def _apply_preference_boost(self, user_id: str, ranked: list[Dict]) -> list[Dict]:
        profile = self._profile_service.get_profile(user_id)
        liked = set(profile.get("liked_merchants", []))
        disliked = set(profile.get("disliked_merchants", []))
        tag_weights = profile.get("tag_weights", {})

        for item in ranked:
            adjust = 0.0
            if item["id"] in liked:
                adjust += 0.15
            if item["id"] in disliked:
                adjust -= 0.25
            for t in item.get("tags", []):
                adjust += float(tag_weights.get(t, 0.0)) * 0.01
            item["score"] = round(max(0.0, min(1.0, item["score"] + adjust)), 4)
            item["pref_adjust"] = round(adjust, 4)

        ranked.sort(key=lambda x: x["score"], reverse=True)
        return ranked

    def _reason_style_from_query(self, query: str) -> str:
        q = query or ""
        if any(k in q for k in ["累", "难受", "暖胃", "不舒服"]):
            return "安抚型"
        if any(k in q for k in ["提神", "刺激", "酸辣", "重口"]):
            return "提神型"
        if any(k in q for k in ["犒劳", "奖励", "治愈"]):
            return "奖励型"
        return "平衡型"

    def _reason_signature(self, text: str) -> str:
        # Remove numbers/punctuations to detect near-duplicate skeleton.
        norm = re.sub(r"\d+(?:\.\d+)?", "", text)
        norm = re.sub(r"[，。；、：:,.!?！？\s]", "", norm)
        return norm[:24]

    def _diversify_reasons(self, selected: list[Dict], dish_map: Dict, reason_map: Dict, style: str) -> Dict:
        prefixes = {
            "安抚型": ["今天更建议你选", "如果想吃得舒服一点", "这家更像稳妥解法"],
            "提神型": ["想快速找回食欲的话", "这一家会更带劲", "如果想口味更有冲击感"],
            "奖励型": ["想犒劳自己时", "这家更有满足感", "今天给自己加点仪式感"],
            "平衡型": ["综合看下来", "从性价比和体验看", "这家在当前条件下更稳"],
        }
        starters = prefixes.get(style, prefixes["平衡型"])

        seen = {}
        starter_idx = 0
        for m in selected:
            mid = m["id"]
            text = reason_map[mid]
            sig = self._reason_signature(text)
            if sig in seen:
                dish = (dish_map.get(mid) or ["这道招牌菜"])[0]
                tail = f"更推荐你点“{dish}”，这一口会更贴合你这次想吃的感觉。"
                reason_map[mid] = f"{starters[starter_idx % len(starters)]}，{tail}"
                starter_idx += 1
            else:
                seen[sig] = mid
        return reason_map

    def _build_comparison_clause(self, current: Dict, rival: Dict) -> str:
        price_gap = float(current.get("avg_price", 0)) - float(rival.get("avg_price", 0))
        dist_gap = float(current.get("distance_km", 0)) - float(rival.get("distance_km", 0))
        rating_gap = float(current.get("rating", 0)) - float(rival.get("rating", 0))
        eta_gap = float(current.get("delivery_eta_min", 0)) - float(rival.get("delivery_eta_min", 0))

        clauses = []
        if rating_gap >= 0.2:
            clauses.append("评分更高")
        if price_gap <= -3:
            clauses.append("价格更友好")
        if dist_gap <= -0.6:
            clauses.append("距离更近")
        if eta_gap <= -6:
            clauses.append("预计送达更快")

        if not clauses:
            if rating_gap >= 0:
                return "相比下一家，整体稳定性更好。"
            return "相比下一家，当前这家在预算与时效上更均衡。"

        return f"相比下一家，它的{('、'.join(clauses[:2]))}。"

    def _inject_comparison_reason(self, selected: list[Dict], reason_map: Dict) -> Dict:
        compare_top_k = int(os.getenv("REASON_COMPARE_TOP_K", "2"))
        if compare_top_k <= 0:
            return reason_map
        upper = min(compare_top_k, max(0, len(selected) - 1))
        for i in range(upper):
            cur = selected[i]
            nxt = selected[i + 1]
            mid = cur["id"]
            clause = self._build_comparison_clause(cur, nxt)
            if clause and clause not in reason_map[mid]:
                reason_map[mid] = f"{reason_map[mid]} {clause}"
        return reason_map

    def _reason_metrics(self, items: list[RecommendationItem], reason_statuses: list[Dict]) -> Dict:
        if not items:
            return {
                "reason_repeat_rate": 0.0,
                "reason_avg_length": 0.0,
                "reason_evidence_coverage": 0.0,
                "reason_rewrite_rate": 0.0,
            }

        signs = [self._reason_signature(i.reason) for i in items]
        unique_count = len(set(signs))
        repeat_rate = round(max(0.0, 1 - unique_count / len(items)), 3)

        avg_len = round(sum(len(i.reason) for i in items) / len(items), 1)

        evidence_hits = 0
        evidence_tokens = ["预算", "公里", "分钟", "评分", "口味", "菜", "推荐", "相比"]
        for i in items:
            if any(t in i.reason for t in evidence_tokens):
                evidence_hits += 1
        evidence_coverage = round(evidence_hits / len(items), 3)

        rewrite_count = sum(1 for x in reason_statuses if "rewritten" in str(x.get("status", "")))
        rewrite_rate = round(rewrite_count / max(1, len(reason_statuses)), 3)

        return {
            "reason_repeat_rate": repeat_rate,
            "reason_avg_length": avg_len,
            "reason_evidence_coverage": evidence_coverage,
            "reason_rewrite_rate": rewrite_rate,
        }

    def recommend(self, request: RecommendRequest, top_n: int = 5) -> RecommendResponse:
        trace_id = f"tr_{uuid.uuid4().hex[:12]}"
        merchants = self._repository.list_all()
        data_source = "mock_json"
        amap_status = "not_requested"
        use_live_poi = os.getenv("USE_LIVE_POI", "false").lower() == "true"
        if request.location and use_live_poi:
            live_merchants, amap_status = self._amap_service.fetch_nearby_merchants(request.location)
            if live_merchants:
                merchants = live_merchants
                data_source = "amap_poi"
            else:
                data_source = "mock_json_fallback"

        # Optional scope lock for follow-up turns in chat:
        # only rerank within previously recommended merchants.
        if request.merchant_scope_ids:
            allow_ids = set(request.merchant_scope_ids)
            scoped = [m for m in merchants if str(m.get("id")) in allow_ids]
            if scoped:
                merchants = scoped
                data_source = f"{data_source}|scoped_followup"
        if request.exclude_merchant_ids:
            exclude_ids = set(request.exclude_merchant_ids)
            excluded = [m for m in merchants if str(m.get("id")) in exclude_ids]
            if excluded:
                merchants = [m for m in merchants if str(m.get("id")) not in exclude_ids]
                data_source = f"{data_source}|exclude_prev"

        parser_source = "rule"
        parser_status = "rule_only"
        use_llm_parser = os.getenv("USE_LLM_PARSER", "false").lower() == "true"
        parsed = parse_query(request.query)
        if use_llm_parser:
            llm_parsed, llm_status = parse_query_by_llm(request.query)
            parser_status = llm_status
            if llm_parsed is not None:
                parsed = llm_parsed
                parser_source = "llm"
        parsed = apply_directional_constraints(request.query, parsed)

        implicit_policy_notes = self._apply_implicit_meal_policy(request.query, parsed)

        parsed, policy_debug = apply_defaults_and_policy(parsed, merchants)
        if implicit_policy_notes:
            policy_debug.setdefault("policy_notes", []).extend(implicit_policy_notes)

        candidates, filter_debug = filter_merchants(parsed, merchants)
        fallback_applied = False

        # Empty-result fallback: loosen distance then budget.
        if not candidates:
            fallback_applied = True
            fallback_parsed = deepcopy(parsed)
            fallback_parsed.slots.distance_max_km = min(float(parsed.slots.distance_max_km) + 1.0, 8.0)
            candidates, filter_debug = filter_merchants(fallback_parsed, merchants)
            parsed = fallback_parsed

        if not candidates:
            fallback_applied = True
            fallback_parsed = deepcopy(parsed)
            fallback_parsed.slots.budget_max = float(parsed.slots.budget_max) * 1.1
            candidates, filter_debug = filter_merchants(fallback_parsed, merchants)
            parsed = fallback_parsed

        if request.fast_mode:
            semantic_scores, semantic_status = {}, "fast_mode_skip_vector"
        else:
            semantic_scores, semantic_status = self._semantic_service.score_merchants(
                user_query=request.query,
                parsed=parsed,
                merchants=candidates,
            )
        multi_recall_debug: Dict = {"enabled": False}
        if self._multi_recall_enabled() and not request.fast_mode:
            candidates, multi_recall_debug = self._build_multi_recall_pool(
                user_id=request.user_id,
                parsed=parsed,
                candidates=candidates,
                semantic_scores=semantic_scores,
            )
            # Recompute semantic on narrowed pool for consistent ranking signal.
            semantic_scores = {str(m.get("id", "")): semantic_scores.get(str(m.get("id", "")), 0.0) for m in candidates}

        ranked = rank_merchants(parsed, candidates, semantic_scores=semantic_scores)
        ranked, backup_notes, backup_debug = self._fallback_rank_with_beverage_backup(
            parsed=parsed,
            merchants=merchants,
            strict_ranked=ranked,
            top_n=top_n,
        )
        ranked = self._apply_preference_boost(request.user_id, ranked)
        if backup_notes:
            policy_debug.setdefault("policy_notes", []).extend(backup_notes)
        use_llm_reasoner = os.getenv("USE_LLM_REASONER", "true").lower() == "true"
        if request.fast_mode:
            use_llm_reasoner = False
        force_all_llm_reasons = os.getenv("FORCE_ALL_LLM_REASONS", "true").lower() == "true"
        # Default to all returned items using LLM reasons.
        llm_reason_top_k = int(os.getenv("LLM_REASON_TOP_K", str(top_n)))
        llm_reason_workers = int(os.getenv("LLM_REASON_WORKERS", "3"))
        reason_source = "rule"
        reason_statuses = []
        items = []
        selected = ranked[:top_n]
        if force_all_llm_reasons:
            llm_reason_top_k = len(selected)
        reason_style = self._reason_style_from_query(request.query)
        dish_map = {m["id"]: pick_recommended_dishes(parsed, m) for m in selected}
        dishes_source_map = {m["id"]: m.get("menu_source", "inferred") for m in selected}
        menu_statuses = []

        # Try reading real menu first (if provider configured), then fallback to inferred dishes.
        if self._menu_service.enabled():
            with ThreadPoolExecutor(max_workers=max(1, min(5, len(selected)))) as executor:
                future_map = {executor.submit(self._menu_service.fetch_menu, m): m["id"] for m in selected}
                for future, mid in future_map.items():
                    menu_dishes, menu_status = future.result()
                    menu_statuses.append({"merchant_id": mid, "status": menu_status})
                    if menu_dishes:
                        dish_map[mid] = menu_dishes[:3]
                        if "template_" in menu_status:
                            dishes_source_map[mid] = "template_menu"
                        elif "llm_menu_ok_" in menu_status:
                            dishes_source_map[mid] = "llm_inferred_menu"
                        elif "crawl_ok" in menu_status:
                            dishes_source_map[mid] = "web_crawled_menu"
                        elif menu_status.startswith("provider_") or "provider_error" in menu_status:
                            dishes_source_map[mid] = "provider_real_menu"
                        else:
                            dishes_source_map[mid] = "web_crawled_menu"
        else:
            menu_statuses = [{"merchant_id": m["id"], "status": "provider_not_configured"} for m in selected]
        reason_map = {m["id"]: build_reason(parsed, m) for m in selected}
        reason_status_map = {m["id"]: "skipped_rule_reason" for m in selected}

        llm_targets = selected[: min(llm_reason_top_k, len(selected))] if use_llm_reasoner else []
        if llm_targets:
            max_workers = max(1, min(llm_reason_workers, len(llm_targets)))
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_map = {
                    executor.submit(
                        generate_reason_by_llm,
                        user_query=request.query,
                        merchant=m,
                        recommended_dishes=dish_map[m["id"]],
                        evidence=reason_evidence(parsed, m, dish_map[m["id"]]),
                    ): m["id"]
                    for m in llm_targets
                }
                for future, merchant_id in future_map.items():
                    llm_reason, llm_reason_status = future.result()
                    reason_status_map[merchant_id] = llm_reason_status
                    if llm_reason:
                        reason_map[merchant_id] = llm_reason
                        reason_source = "llm"

        reason_map = self._diversify_reasons(selected, dish_map, reason_map, reason_style)
        enable_reason_compare = os.getenv("ENABLE_REASON_COMPARISON", "true").lower() == "true"
        if enable_reason_compare:
            reason_map = self._inject_comparison_reason(selected, reason_map)

        for m in selected:
            mid = m["id"]
            reason_statuses.append({"merchant_id": mid, "status": reason_status_map[mid]})
            items.append(
                RecommendationItem(
                    merchant_id=mid,
                    name=m["name"],
                    score=m["score"],
                    reason=reason_map[mid],
                    recommended_dishes=dish_map[mid],
                    dishes_source=dishes_source_map[mid],
                )
            )
        selected_snapshot = [
            {
                "merchant_id": str(m.get("id", "")),
                "name": m.get("name", ""),
                "distance_km": float(m.get("distance_km", 0.0)),
                "avg_price": float(m.get("avg_price", 0.0)),
                "delivery_eta_min": int(m.get("delivery_eta_min", 0)),
                "rating": float(m.get("rating", 0.0)),
            }
            for m in selected
        ]
        reason_metrics = self._reason_metrics(items, reason_statuses)

        debug: Dict = {
            "parser_source": parser_source,
            "parser_status": parser_status,
            "reason_source": reason_source,
            "llm_reason_top_k": llm_reason_top_k,
            "llm_reason_workers": llm_reason_workers,
            "reason_statuses": reason_statuses,
            "reason_style": reason_style,
            "reason_comparison_enabled": enable_reason_compare,
            "reason_metrics": reason_metrics,
            "menu_statuses": menu_statuses,
            "policy": policy_debug,
            "filter": filter_debug,
            "candidate_count": len(candidates),
            "multi_recall": multi_recall_debug,
            "merchant_scope_count": len(request.merchant_scope_ids or []),
            "merchant_exclude_count": len(request.exclude_merchant_ids or []),
            "selected_snapshot": selected_snapshot,
            "preference_applied": True,
            "beverage_backup": backup_debug,
            "semantic_status": semantic_status,
            "semantic_source": (
                "vector"
                if semantic_status.startswith("vector_ok") or semantic_status.startswith("langchain_ok")
                else "keyword_overlap"
            ),
            "fast_mode": request.fast_mode,
            "data_source": data_source,
            "amap_status": amap_status,
        }

        self._metrics_logger.log(
            {
                "ts": datetime.now().isoformat(),
                "trace_id": trace_id,
                "user_id": request.user_id,
                "query": request.query,
                "parser_source": parser_source,
                "reason_source": reason_source,
                "reason_style": reason_style,
                "reason_statuses": reason_statuses,
                "reason_metrics": reason_metrics,
                "data_source": data_source,
                "candidate_count": len(candidates),
            }
        )

        return RecommendResponse(
            trace_id=trace_id,
            parsed_query=parsed,
            recommendations=items,
            fallback_applied=fallback_applied,
            debug=debug,
        )

    def feedback(self, user_id: str, merchant_id: str, action: str) -> FeedbackResponse:
        merchants = self._repository.list_all()
        merchant = next((m for m in merchants if m["id"] == merchant_id), None)
        if merchant is None:
            return FeedbackResponse(ok=False, message="merchant_id 不存在")

        profile = self._profile_service.record_feedback(
            user_id=user_id,
            merchant_id=merchant_id,
            action=action,
            merchant_tags=merchant.get("tags", []),
        )
        return FeedbackResponse(ok=True, message="反馈已记录", profile=profile)
