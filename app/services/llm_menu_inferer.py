from __future__ import annotations

import json
import os
import re
from typing import Dict, List, Tuple
from urllib.parse import quote_plus

import requests


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


def _extract_content(data: Dict) -> str:
    choices = data.get("choices", [])
    if not choices:
        return "{}"
    msg = choices[0].get("message", {})
    content = msg.get("content", "{}")
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
            else:
                parts.append(str(item))
        content = "".join(parts)
    content = str(content).strip()
    content = re.sub(r"^```(?:json)?\s*", "", content)
    content = re.sub(r"\s*```$", "", content)
    return content.strip()


def _clean_dishes(items: List[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for x in items:
        d = str(x).strip()
        if not d or len(d) < 2 or len(d) > 18:
            continue
        if any(k in d for k in ("登录", "首页", "下载", "客服", "版权", "更多", "全部")):
            continue
        if d not in seen:
            seen.add(d)
            out.append(d)
    return out[:8]


class LlmMenuInferer:
    def __init__(self) -> None:
        self._enabled = os.getenv("USE_LLM_MENU_INFER", "true").lower() == "true"
        self._timeout_sec = int(os.getenv("LLM_MENU_TIMEOUT_SEC", os.getenv("LLM_TIMEOUT_SEC", "8")))
        self._search_timeout = int(os.getenv("LLM_MENU_SEARCH_TIMEOUT_SEC", "5"))
        self._search_pages = int(os.getenv("LLM_MENU_SEARCH_PAGES", "3"))

    def enabled(self) -> bool:
        return self._enabled and bool(os.getenv("OPENAI_API_KEY", "").strip())

    def _search_snippets(self, merchant: Dict) -> List[Dict]:
        merchant_name = str(merchant.get("name", "")).strip()
        if not merchant_name:
            return []
        query = quote_plus(f"{merchant_name} 推荐菜 菜单")
        url = f"https://duckduckgo.com/html/?q={query}"
        headers = {"User-Agent": "Mozilla/5.0"}
        try:
            resp = requests.get(url, headers=headers, timeout=self._search_timeout)
            resp.raise_for_status()
        except Exception:
            return []

        # Parse result blocks from html text without adding extra parser dependencies.
        html = resp.text
        blocks = re.findall(
            r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>(?:.|\n)*?<a[^>]*class="result__snippet"[^>]*>(.*?)</a>',
            html,
            flags=re.I,
        )
        out: List[Dict] = []
        for href, title, snippet in blocks[: self._search_pages]:
            clean_title = re.sub(r"<[^>]+>", "", title).strip()
            clean_snippet = re.sub(r"<[^>]+>", "", snippet).strip()
            out.append({"url": href, "title": clean_title, "snippet": clean_snippet})
        return out

    def _call_llm(self, payload: Dict) -> Tuple[List[str], str]:
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        if not api_key:
            return [], "llm_menu_missing_api_key"
        api_url = _normalize_api_url(os.getenv("LLM_API_URL", "https://api.openai.com/v1/chat/completions"))
        model = os.getenv("LLM_MENU_MODEL", os.getenv("REASON_MODEL", os.getenv("PARSER_MODEL", "gpt-4o-mini")))
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        req = {
            "model": model,
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是外卖菜单推断器。请根据商家信息和网页检索片段，输出JSON："
                        '{"dishes":["菜名1","菜名2"],"confidence":"high|medium|low"}。'
                        "要求：只输出可能真实存在的具体菜名，禁止泛词如“招牌盖饭”“特色套餐”。"
                    ),
                },
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
        }
        try:
            resp = requests.post(api_url, headers=headers, json=req, timeout=self._timeout_sec)
            resp.raise_for_status()
            content = _extract_content(resp.json())
            data = json.loads(content)
            dishes = _clean_dishes(data.get("dishes", []) if isinstance(data, dict) else [])
            if not dishes:
                return [], "llm_menu_empty"
            confidence = str(data.get("confidence", "medium")).lower()
            return dishes, f"llm_menu_ok_{confidence}"
        except Exception as exc:
            return [], f"llm_menu_failed:{type(exc).__name__}"

    def infer_menu(self, merchant: Dict) -> Tuple[List[str], str]:
        if not self.enabled():
            return [], "llm_menu_disabled"
        snippets = self._search_snippets(merchant)
        payload = {
            "merchant": {
                "id": merchant.get("id"),
                "name": merchant.get("name"),
                "tags": merchant.get("tags", []),
                "description": merchant.get("description", ""),
                "avg_price": merchant.get("avg_price"),
                "city": merchant.get("city", ""),
            },
            "web_snippets": snippets,
            "task": "推断该商家最可能的3-8个菜品名",
        }
        dishes, status = self._call_llm(payload)
        if dishes:
            return dishes, status
        # no snippets case can still fallback by merchant semantic guess via LLM
        if snippets:
            return [], status
        payload["task"] = "未检索到网页片段时，结合商家名称和标签推断3-6个具体菜品名，避免泛词"
        return self._call_llm(payload)

