from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, Optional, Tuple

import requests
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from requests.exceptions import SSLError
import urllib3
from urllib3.exceptions import InsecureRequestWarning

from app.schemas import ParsedQuery, ParsedSlots
from app.services.langchain_client import (
    invoke_chat_via_langchain,
    invoke_chat_via_requests,
    normalize_chat_api_url,
    use_langchain_llm,
)


class _LLMParsedSlots(BaseModel):
    model_config = ConfigDict(extra="forbid")

    taste: list[str] = Field(default_factory=list)
    category: list[str] = Field(default_factory=list)
    budget_max: Optional[float] = None
    distance_max_km: Optional[float] = None
    delivery_eta_max_min: Optional[int] = None
    dietary_restrictions: list[str] = Field(default_factory=list)


class _LLMParsedQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: str = "order_food"
    slots: _LLMParsedSlots = Field(default_factory=_LLMParsedSlots)
    confidence: float = 0.75
    conflict_flags: list[str] = Field(default_factory=list)


def _to_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str):
        parts = re.split(r"[，,、/;；\s]+", value.strip())
        return [p for p in parts if p]
    return [str(value).strip()]


def _to_float(value: Any) -> Optional[float]:
    if value in (None, "", "null"):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        m = re.search(r"(\d+(?:\.\d+)?)", value)
        if m:
            return float(m.group(1))
    return None


def _to_int(value: Any) -> Optional[int]:
    num = _to_float(value)
    return int(num) if num is not None else None


def _to_parsed_query(payload: Dict[str, Any]) -> ParsedQuery:
    slots = payload.get("slots", {}) if isinstance(payload, dict) else {}
    return ParsedQuery(
        intent=str(payload.get("intent", "order_food")),
        slots=ParsedSlots(
            taste=_to_list(slots.get("taste", [])),
            category=_to_list(slots.get("category", [])),
            budget_max=_to_float(slots.get("budget_max")),
            distance_max_km=_to_float(slots.get("distance_max_km")),
            delivery_eta_max_min=_to_int(slots.get("delivery_eta_max_min")),
            dietary_restrictions=_to_list(slots.get("dietary_restrictions", [])),
        ),
        confidence=max(0.0, min(1.0, _to_float(payload.get("confidence")) or 0.75)),
        conflict_flags=_to_list(payload.get("conflict_flags", [])),
    )


def _parse_llm_content(content: str) -> Tuple[Optional[ParsedQuery], str]:
    try:
        raw_payload = json.loads(content or "{}")
    except json.JSONDecodeError:
        return None, "llm_invalid_json"
    try:
        validated = _LLMParsedQuery.model_validate(raw_payload)
    except ValidationError:
        return None, "llm_invalid_schema"
    return _to_parsed_query(validated.model_dump()), "ok_validated_schema"


def parse_query_by_llm(query: str) -> Tuple[Optional[ParsedQuery], str]:
    api_key = os.getenv("OPENAI_API_KEY")
    model = os.getenv("PARSER_MODEL", "gpt-4o-mini")
    if not api_key:
        return None, "missing_api_key"

    api_url = normalize_chat_api_url(os.getenv("LLM_API_URL", "https://api.openai.com/v1/chat/completions"))
    timeout_sec = int(os.getenv("LLM_TIMEOUT_SEC", "8"))
    verify_ssl = os.getenv("LLM_VERIFY_SSL", "true").lower() == "true"
    suppress_warn = os.getenv("SUPPRESS_INSECURE_WARNING", "true").lower() == "true"
    if not verify_ssl and suppress_warn:
        urllib3.disable_warnings(InsecureRequestWarning)
    system_prompt = (
        "你是外卖推荐系统的语义解析器。"
        "请把用户输入解析为JSON，字段仅允许: "
        "intent, slots, confidence, conflict_flags。"
        "slots字段仅允许: taste, category, budget_max, distance_max_km, delivery_eta_max_min, dietary_restrictions。"
        "不要输出额外文本。"
    )

    try:
        if use_langchain_llm() and verify_ssl:
            content, status = invoke_chat_via_langchain(
                api_url=api_url,
                api_key=api_key,
                model=model,
                timeout_sec=timeout_sec,
                system_prompt=system_prompt,
                user_content=query,
                temperature=0,
                force_json_object=True,
                run_name="parse_query_by_llm",
                tags=["parser", "chat"],
                metadata={"module": "llm_parser"},
            )
            parsed_query, parse_status = _parse_llm_content(content)
            if parsed_query is None:
                return None, parse_status
            return parsed_query, f"{status}|{parse_status}"

        try:
            content, status = invoke_chat_via_requests(
                requests_module=requests,
                api_url=api_url,
                api_key=api_key,
                model=model,
                timeout_sec=timeout_sec,
                verify_ssl=verify_ssl,
                system_prompt=system_prompt,
                user_content=query,
                temperature=0,
                force_json_object=True,
            )
            parsed_query, parse_status = _parse_llm_content(content)
            if parsed_query is None:
                return None, parse_status
            return parsed_query, f"{status}|{parse_status}"
        except SSLError:
            # Some third-party gateways have unstable cert chains.
            # Retry once with verify=False for compatibility.
            content, _ = invoke_chat_via_requests(
                requests_module=requests,
                api_url=api_url,
                api_key=api_key,
                model=model,
                timeout_sec=timeout_sec,
                verify_ssl=False,
                system_prompt=system_prompt,
                user_content=query,
                temperature=0,
                force_json_object=True,
            )
            parsed_query, parse_status = _parse_llm_content(content)
            if parsed_query is None:
                return None, parse_status
            return parsed_query, f"ok_insecure_retry|{parse_status}"
    except Exception as exc:
        return None, f"llm_call_failed:{type(exc).__name__}"
