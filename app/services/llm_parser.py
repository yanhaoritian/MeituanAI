from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, Optional, Tuple

import requests
from requests.exceptions import SSLError
import urllib3
from urllib3.exceptions import InsecureRequestWarning

from app.schemas import ParsedQuery, ParsedSlots


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


def _extract_content(data: Dict[str, Any]) -> str:
    choices = data.get("choices", [])
    if not choices:
        return "{}"
    message = choices[0].get("message", {})
    content = message.get("content", "{}")
    # Some gateways return content as blocks.
    if isinstance(content, list):
        text_parts = []
        for c in content:
            if isinstance(c, dict):
                if c.get("type") == "text":
                    text_parts.append(str(c.get("text", "")))
            else:
                text_parts.append(str(c))
        content = "".join(text_parts)
    content = str(content).strip()
    # Strip markdown fences if present.
    content = re.sub(r"^```(?:json)?\s*", "", content)
    content = re.sub(r"\s*```$", "", content)
    return content.strip()


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


def _normalize_api_url(raw_url: str) -> str:
    if raw_url.endswith("/v1/chat/completions"):
        return raw_url
    if raw_url.endswith("/"):
        raw_url = raw_url[:-1]
    if raw_url.endswith("/v1"):
        return f"{raw_url}/chat/completions"
    if raw_url.startswith("http"):
        return f"{raw_url}/v1/chat/completions"
    return "https://api.openai.com/v1/chat/completions"


def parse_query_by_llm(query: str) -> Tuple[Optional[ParsedQuery], str]:
    api_key = os.getenv("OPENAI_API_KEY")
    model = os.getenv("PARSER_MODEL", "gpt-4o-mini")
    if not api_key:
        return None, "missing_api_key"

    api_url = _normalize_api_url(os.getenv("LLM_API_URL", "https://api.openai.com/v1/chat/completions"))
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

    req = {
        "model": model,
        "temperature": 0,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": query},
        ],
        "response_format": {"type": "json_object"},
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    try:
        try:
            resp = requests.post(api_url, headers=headers, json=req, timeout=timeout_sec, verify=verify_ssl)
            resp.raise_for_status()
            data = resp.json()
            content = _extract_content(data)
            parsed = json.loads(content)
            return _to_parsed_query(parsed), "ok"
        except SSLError:
            # Some third-party gateways have unstable cert chains.
            # Retry once with verify=False for compatibility.
            resp = requests.post(api_url, headers=headers, json=req, timeout=timeout_sec, verify=False)
            resp.raise_for_status()
            data = resp.json()
            content = _extract_content(data)
            parsed = json.loads(content)
            return _to_parsed_query(parsed), "ok_insecure_retry"
    except Exception as exc:
        return None, f"llm_call_failed:{type(exc).__name__}"
