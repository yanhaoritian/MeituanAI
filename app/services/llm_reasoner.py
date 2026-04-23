from __future__ import annotations

import json
import os
import re
from datetime import datetime
from typing import Dict, List, Tuple

import requests
import urllib3
from urllib3.exceptions import InsecureRequestWarning

from app.services.langchain_client import (
    invoke_chat_via_langchain,
    invoke_chat_via_requests,
    normalize_chat_api_url,
    to_json_user_payload,
    use_langchain_llm,
)


def _infer_user_scene(user_query: str) -> str:
    q = user_query or ""
    if any(k in q for k in ["上班", "打工", "午休", "工作日", "赶时间"]):
        return "工作日快决策场景"
    if any(k in q for k in ["夜宵", "晚上", "加班后", "深夜"]):
        return "夜间进食场景"
    if any(k in q for k in ["减脂", "健身", "高蛋白", "控卡"]):
        return "饮食管理场景"
    if any(k in q for k in ["暖胃", "不舒服", "热乎"]):
        return "舒缓暖胃场景"
    return "日常点餐场景"


def _time_context() -> str:
    h = datetime.now().hour
    if 6 <= h < 10:
        return "早餐时段"
    if 10 <= h < 14:
        return "午餐时段"
    if 14 <= h < 18:
        return "下午加餐时段"
    if 18 <= h < 22:
        return "晚餐时段"
    return "夜宵时段"


def _reason_quality_score(text: str) -> int:
    # Lightweight heuristic to trigger one rewrite if too generic.
    score = 0
    if any(k in text for k in ["因为", "所以", "更适合", "虽然", "但"]):
        score += 1
    if any(k in text for k in ["预算", "公里", "分钟", "评分", "口味", "菜"]):
        score += 1
    if len(text) >= 18:
        score += 1
    if any(k in text for k in ["符合您的需求", "综合考虑", "推荐给您", "比较适合你"]):
        score -= 1
    if any(k in text for k in ["首先", "其次", "综上", "因此推荐"]):
        score -= 1
    return score


def _call_llm(
    *,
    api_url: str,
    api_key: str,
    model: str,
    timeout_sec: int,
    verify_ssl: bool,
    system_prompt: str,
    user_payload: Dict,
) -> str:
    user_content = to_json_user_payload(user_payload)
    reason_temperature = float(os.getenv("REASON_TEMPERATURE", "0.75"))
    if use_langchain_llm() and verify_ssl:
        text, _ = invoke_chat_via_langchain(
            api_url=api_url,
            api_key=api_key,
            model=model,
            timeout_sec=timeout_sec,
            system_prompt=system_prompt,
            user_content=user_content,
            temperature=reason_temperature,
            force_json_object=False,
            run_name="generate_reason_by_llm",
            tags=["reasoner", "chat"],
            metadata={"module": "llm_reasoner"},
        )
        return text
    text, _ = invoke_chat_via_requests(
        requests_module=requests,
        api_url=api_url,
        api_key=api_key,
        model=model,
        timeout_sec=timeout_sec,
        verify_ssl=verify_ssl,
        system_prompt=system_prompt,
        user_content=user_content,
        temperature=reason_temperature,
        force_json_object=False,
    )
    return text


def generate_reason_by_llm(
    user_query: str,
    merchant: Dict,
    recommended_dishes: List[str],
    evidence: Dict,
) -> Tuple[str | None, str]:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        return None, "missing_api_key"

    api_url = normalize_chat_api_url(os.getenv("LLM_API_URL", "https://api.openai.com/v1/chat/completions"))
    timeout_sec = int(os.getenv("REASON_TIMEOUT_SEC", os.getenv("LLM_TIMEOUT_SEC", "8")))
    verify_ssl = os.getenv("LLM_VERIFY_SSL", "true").lower() == "true"
    suppress_warn = os.getenv("SUPPRESS_INSECURE_WARNING", "true").lower() == "true"
    if not verify_ssl and suppress_warn:
        urllib3.disable_warnings(InsecureRequestWarning)
    model = os.getenv("REASON_MODEL", os.getenv("PARSER_MODEL", "gpt-4o-mini"))

    prompt = {
        "user_query": user_query,
        "user_scene": _infer_user_scene(user_query),
        "time_context": _time_context(),
        "merchant": {
            "name": merchant.get("name"),
            "avg_price": merchant.get("avg_price"),
            "distance_km": merchant.get("distance_km"),
            "rating": merchant.get("rating"),
            "delivery_eta_min": merchant.get("delivery_eta_min"),
            "tags": merchant.get("tags", []),
            "description": merchant.get("description", ""),
            "scene_copy": merchant.get("scene_copy", ""),
            "flavor_notes": merchant.get("flavor_notes", []),
            "dish_highlights": merchant.get("dish_highlights", []),
        },
        "recommended_dishes": recommended_dishes,
        "evidence": evidence,
    }

    system_prompt = (
        "你是外卖推荐解释器。请基于给定证据生成一句22-55字中文推荐理由。"
        "要求：1) 必须提到至少一个具体证据（预算/距离/时效/评分/口味/菜品）；"
        "2) 语气自然、口语化，像懂用户场景的朋友，不要客服腔；"
        "3) 尽量包含轻微权衡表达（如虽然...但...）；"
        "4) 禁止空泛模板化套话，避免“综合考虑、推荐给您、符合您的需求”；"
        "5) 仅输出一句话，不要JSON。"
    )

    try:
        text = _call_llm(
            api_url=api_url,
            api_key=api_key,
            model=model,
            timeout_sec=timeout_sec,
            verify_ssl=verify_ssl,
            system_prompt=system_prompt,
            user_payload=prompt,
        )
        if not text:
            return None, "empty_reason"
        text = text.replace("\n", " ").strip()
        if _reason_quality_score(text) < 2:
            rewrite_prompt = {
                **prompt,
                "first_draft": text,
                "rewrite_target": "让理由更像真人对话，带一点场景感，避免客服模板口吻，且必须带具体证据词。",
            }
            rewritten = _call_llm(
                api_url=api_url,
                api_key=api_key,
                model=model,
                timeout_sec=timeout_sec,
                verify_ssl=verify_ssl,
                system_prompt=system_prompt,
                user_payload=rewrite_prompt,
            )
            if rewritten:
                return rewritten.replace("\n", " ").strip(), "ok_rewritten"
        return text, "ok"
    except Exception as exc:
        return None, f"llm_reason_failed:{type(exc).__name__}"

