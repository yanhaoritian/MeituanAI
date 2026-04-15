from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import List, Tuple
from urllib.parse import quote_plus, urlparse

import requests
from bs4 import BeautifulSoup


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _extract_dish_candidates(html: str) -> List[str]:
    soup = BeautifulSoup(html, "html.parser")
    tags = soup.select("li, p, span, h3, h4")
    dish_like = []
    # Common dish keywords to reduce noise.
    kw = ("饭", "面", "粉", "粥", "汤", "鸡", "牛", "鱼", "虾", "套餐", "汉堡", "奶茶", "寿司")
    for t in tags:
        txt = _normalize_text(t.get_text(" ", strip=True))
        if len(txt) < 2 or len(txt) > 22:
            continue
        if any(k in txt for k in kw) and not re.search(r"(登录|注册|购物车|立即|优惠|下载|首页)", txt):
            dish_like.append(txt)
    # Deduplicate keep order.
    seen = set()
    out = []
    for x in dish_like:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out[:30]


class WebMenuCrawler:
    def __init__(self) -> None:
        self._enabled = os.getenv("CRAWL_MENU_ENABLED", "false").lower() == "true"
        self._timeout = int(os.getenv("CRAWL_TIMEOUT_SEC", "5"))
        self._max_pages = int(os.getenv("CRAWL_MAX_PAGES", "3"))
        self._max_subpages = int(os.getenv("CRAWL_MAX_SUBPAGES", "2"))
        self._cache_ttl_sec = int(os.getenv("CRAWL_CACHE_TTL_SEC", "86400"))
        domains = os.getenv(
            "CRAWL_ALLOWED_DOMAINS",
            "dianping.com,meituan.com,ele.me,xiachufang.com,douyin.com",
        )
        self._allow_domains = [d.strip().lower() for d in domains.split(",") if d.strip()]
        self._cache_file = Path(__file__).resolve().parents[1] / "data" / "crawl_cache.json"
        self._cache_file.parent.mkdir(parents=True, exist_ok=True)
        self._cache = self._load_cache()

    def _load_cache(self) -> dict:
        if not self._cache_file.exists():
            return {}
        try:
            with self._cache_file.open("r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _save_cache(self) -> None:
        try:
            with self._cache_file.open("w", encoding="utf-8") as f:
                json.dump(self._cache, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _cache_key(self, merchant_name: str) -> str:
        return _normalize_text(merchant_name).lower()

    def enabled(self) -> bool:
        return self._enabled

    def _allowed(self, url: str) -> bool:
        host = urlparse(url).netloc.lower()
        return any(d in host for d in self._allow_domains)

    def _extract_result_links(self, html: str) -> List[str]:
        soup = BeautifulSoup(html, "html.parser")
        links = []
        for a in soup.select("a"):
            href = str(a.get("href", "")).strip()
            if href.startswith("http") and self._allowed(href):
                links.append(href)
        seen = set()
        out = []
        for x in links:
            if x not in seen:
                seen.add(x)
                out.append(x)
        return out

    def _search_urls(self, merchant_name: str) -> List[str]:
        # Multi-entry search on DuckDuckGo endpoints.
        query = quote_plus(f"{merchant_name} 菜单 推荐菜")
        endpoints = [
            f"https://duckduckgo.com/html/?q={query}",
            f"https://lite.duckduckgo.com/lite/?q={query}",
        ]
        headers = {"User-Agent": "Mozilla/5.0"}
        links: List[str] = []
        for url in endpoints:
            try:
                resp = requests.get(url, headers=headers, timeout=self._timeout)
                resp.raise_for_status()
                links.extend(self._extract_result_links(resp.text))
            except Exception:
                continue
        seen = set()
        dedup = []
        for x in links:
            if x not in seen:
                seen.add(x)
                dedup.append(x)
        return dedup[: self._max_pages]

    def _discover_subpages(self, html: str, base_url: str) -> List[str]:
        base_host = urlparse(base_url).netloc.lower()
        soup = BeautifulSoup(html, "html.parser")
        keys = ("menu", "dish", "foods", "caidan", "recommend", "item")
        out = []
        for a in soup.select("a"):
            href = str(a.get("href", "")).strip()
            text = _normalize_text(a.get_text(" ", strip=True)).lower()
            if not href:
                continue
            if not href.startswith("http"):
                continue
            host = urlparse(href).netloc.lower()
            if host != base_host:
                continue
            low = href.lower()
            if any(k in low for k in keys) or any(k in text for k in keys):
                if self._allowed(href):
                    out.append(href)
        seen = set()
        dedup = []
        for x in out:
            if x not in seen:
                seen.add(x)
                dedup.append(x)
        return dedup[: self._max_subpages]

    def _dish_score(self, dish: str, merchant_name: str) -> float:
        s = 0.0
        if 2 <= len(dish) <= 14:
            s += 2.0
        if any(k in dish for k in ("饭", "面", "粉", "粥", "汤", "鸡", "牛", "鱼", "虾", "堡", "茶", "奶")):
            s += 2.0
        if any(k in dish for k in ("登录", "注册", "下载", "首页", "购物车", "客服", "版权")):
            s -= 4.0
        # Slight boost if dish contains merchant keyword fragment.
        frag = _normalize_text(merchant_name)[:3]
        if frag and frag in dish:
            s += 0.8
        return s

    def crawl_menu(self, merchant_name: str) -> Tuple[List[str], str, List[str]]:
        if not self._enabled:
            return [], "crawl_disabled", []
        if not merchant_name.strip():
            return [], "empty_merchant_name", []
        key = self._cache_key(merchant_name)
        now = int(time.time())
        cache_hit = self._cache.get(key)
        if isinstance(cache_hit, dict):
            ts = int(cache_hit.get("ts", 0))
            dishes = cache_hit.get("dishes", [])
            urls = cache_hit.get("urls", [])
            if now - ts <= self._cache_ttl_sec and isinstance(dishes, list) and dishes:
                return [str(x) for x in dishes[:15]], "crawl_cache_hit", list(urls)[:5]
        try:
            urls = self._search_urls(merchant_name)
            if not urls:
                return [], "crawl_no_candidate_urls", []

            headers = {"User-Agent": "Mozilla/5.0"}
            all_dishes: List[str] = []
            used_urls: List[str] = []
            for url in urls:
                try:
                    r = requests.get(url, headers=headers, timeout=self._timeout)
                    r.raise_for_status()
                    dishes = _extract_dish_candidates(r.text)
                    if dishes:
                        all_dishes.extend(dishes)
                        used_urls.append(url)
                    # Follow menu-like subpages for stronger extraction.
                    subpages = self._discover_subpages(r.text, url)
                    for sp in subpages:
                        try:
                            sr = requests.get(sp, headers=headers, timeout=self._timeout)
                            sr.raise_for_status()
                            sd = _extract_dish_candidates(sr.text)
                            if sd:
                                all_dishes.extend(sd)
                                used_urls.append(sp)
                        except Exception:
                            continue
                except Exception:
                    continue

            if not all_dishes:
                return [], "crawl_no_dishes_extracted", used_urls

            # Deduplicate + score + sort
            seen = set()
            scored = []
            for d in all_dishes:
                if d not in seen:
                    seen.add(d)
                    scored.append((self._dish_score(d, merchant_name), d))
            scored.sort(key=lambda x: x[0], reverse=True)
            out = [d for s, d in scored if s >= 1.5][:15]
            if not out:
                return [], "crawl_low_confidence", used_urls

            self._cache[key] = {"ts": now, "dishes": out, "urls": used_urls[:10]}
            self._save_cache()
            return out, "crawl_ok", used_urls[:10]
        except Exception as exc:
            return [], f"crawl_error:{type(exc).__name__}", []
