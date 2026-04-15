from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, List, Tuple

import requests

from app.services.llm_menu_inferer import LlmMenuInferer
from app.services.web_menu_crawler import WebMenuCrawler


class MenuService:
    def __init__(self) -> None:
        self._base_url = os.getenv("MENU_PROVIDER_URL", "").strip()
        self._api_key = os.getenv("MENU_PROVIDER_API_KEY", "").strip()
        self._timeout = int(os.getenv("MENU_PROVIDER_TIMEOUT_SEC", "5"))
        self._crawler = WebMenuCrawler()
        self._llm_menu = LlmMenuInferer()
        self._templates = self._load_templates()

    def _load_templates(self) -> Dict:
        p = Path(__file__).resolve().parents[1] / "data" / "menu_templates.json"
        if not p.exists():
            return {"brand_keywords": {}, "tag_templates": {}}
        with p.open("r", encoding="utf-8") as f:
            try:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
            except json.JSONDecodeError:
                pass
        return {"brand_keywords": {}, "tag_templates": {}}

    def _template_menu(self, merchant: Dict) -> Tuple[List[str], str]:
        name = str(merchant.get("name", ""))
        tags = [str(t) for t in merchant.get("tags", [])]
        brand_map = self._templates.get("brand_keywords", {}) or {}
        tag_map = self._templates.get("tag_templates", {}) or {}

        for kw, dishes in brand_map.items():
            if kw and kw in name and isinstance(dishes, list) and dishes:
                return [str(x).strip() for x in dishes if str(x).strip()][:8], "template_brand_ok"

        merged: List[str] = []
        for t in tags:
            if t in tag_map and isinstance(tag_map[t], list):
                merged.extend([str(x).strip() for x in tag_map[t] if str(x).strip()])
        if merged:
            dedup = []
            seen = set()
            for d in merged:
                if d not in seen:
                    seen.add(d)
                    dedup.append(d)
            return dedup[:8], "template_tag_ok"
        return [], "template_not_matched"

    def enabled(self) -> bool:
        return bool(self._base_url)

    def fetch_menu(self, merchant: Dict) -> Tuple[List[str], str]:
        if not self.enabled():
            # Fallback to controlled web crawl when provider is unavailable.
            dishes, status, _ = self._crawler.crawl_menu(merchant.get("name", ""))
            if dishes:
                return dishes, status
            llm_dishes, llm_status = self._llm_menu.infer_menu(merchant)
            if llm_dishes:
                return llm_dishes, f"provider_not_configured|{status}|{llm_status}"
            t_dishes, t_status = self._template_menu(merchant)
            if t_dishes:
                return t_dishes, f"provider_not_configured|{status}|{llm_status}|{t_status}"
            return [], f"provider_not_configured|{status}|{llm_status}|{t_status}"

        url = f"{self._base_url.rstrip('/')}/menu"
        params = {
            "merchant_id": merchant.get("id", ""),
            "merchant_name": merchant.get("name", ""),
        }
        headers = {}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=self._timeout)
            resp.raise_for_status()
            data = resp.json()
            dishes = data.get("dishes", []) if isinstance(data, dict) else []
            dishes = [str(x).strip() for x in dishes if str(x).strip()]
            if not dishes:
                return [], "provider_empty_menu"
            return dishes[:8], "provider_ok"
        except Exception as exc:
            # Provider failed, fallback to crawler.
            dishes, crawl_status, _ = self._crawler.crawl_menu(merchant.get("name", ""))
            if dishes:
                return dishes, f"provider_error:{type(exc).__name__}|{crawl_status}"
            llm_dishes, llm_status = self._llm_menu.infer_menu(merchant)
            if llm_dishes:
                return llm_dishes, f"provider_error:{type(exc).__name__}|{crawl_status}|{llm_status}"
            t_dishes, t_status = self._template_menu(merchant)
            if t_dishes:
                return t_dishes, f"provider_error:{type(exc).__name__}|{crawl_status}|{llm_status}|{t_status}"
            return [], f"provider_error:{type(exc).__name__}|{crawl_status}|{llm_status}|{t_status}"
