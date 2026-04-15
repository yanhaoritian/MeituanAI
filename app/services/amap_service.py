from __future__ import annotations

import os
import re
from typing import Dict, List, Tuple

import requests

from app.schemas import Location


def _to_float(v, default: float) -> float:
    try:
        return float(v)
    except Exception:
        return default


def _infer_tags(name: str, type_text: str) -> List[str]:
    base = f"{name} {type_text}"
    tags = []
    rules = [
        ("汤", "暖胃"),
        ("面", "汤面"),
        ("米线", "米线"),
        ("粥", "粥"),
        ("轻食", "轻食"),
        ("沙拉", "轻食"),
        ("烤鸡", "高蛋白"),
        ("鸡胸", "高蛋白"),
        ("酸辣", "酸辣"),
        ("麻辣", "酸辣"),
        ("日料", "日料"),
        ("寿司", "日料"),
        ("快餐", "家常菜"),
        ("盖饭", "家常菜"),
    ]
    for kw, tag in rules:
        if kw in base:
            tags.append(tag)
    if not tags:
        tags = ["家常菜", "热菜"]
    # unique keep order
    seen = set()
    uniq = []
    for t in tags:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    return uniq


def _infer_dishes(tags: List[str], merchant_name: str = "") -> List[str]:
    prefix = merchant_name[:6] if merchant_name else ""
    if "汤面" in tags:
        return ["招牌牛肉汤面", "番茄鸡蛋汤面", "酸辣肥牛面"]
    if "米线" in tags:
        return ["菌菇鸡汤米线", "番茄肥牛米线", "青菜丸子米线"]
    if "粥" in tags:
        return ["皮蛋瘦肉粥", "虾仁瑶柱粥", "南瓜小米粥"]
    if "轻食" in tags:
        return ["鸡胸藜麦碗", "牛油果沙拉", "低脂牛肉能量碗"]
    if "日料" in tags:
        return ["三文鱼寿司", "照烧鸡腿饭", "鳗鱼饭"]
    if "高蛋白" in tags:
        return ["香煎鸡胸肉", "黑椒牛肉饭", "蛋白双拼碗"]
    # Avoid overly generic "招牌盖饭" in fallback.
    if prefix:
        return [f"{prefix}双拼饭", f"{prefix}招牌套餐", f"{prefix}热菜饭"]
    return ["家常小炒套餐", "热卤双拼饭", "时蔬鸡肉饭"]


class AmapPoiService:
    def __init__(self) -> None:
        self._key = os.getenv("AMAP_API_KEY", "")
        self._radius = int(os.getenv("AMAP_RADIUS_M", "3000"))
        self._page_size = int(os.getenv("AMAP_PAGE_SIZE", "25"))
        self._timeout = int(os.getenv("AMAP_TIMEOUT_SEC", "6"))

    def enabled(self) -> bool:
        return bool(self._key)

    def fetch_nearby_merchants(self, location: Location) -> Tuple[List[Dict], str]:
        if not self._key:
            return [], "missing_amap_key"

        url = "https://restapi.amap.com/v3/place/around"
        params = {
            "key": self._key,
            "location": f"{location.lng},{location.lat}",
            "types": "050000",
            "radius": self._radius,
            "sortrule": "distance",
            "offset": self._page_size,
            "page": 1,
            "extensions": "all",
        }
        try:
            resp = requests.get(url, params=params, timeout=self._timeout)
            resp.raise_for_status()
            data = resp.json()
            if str(data.get("status")) != "1":
                return [], f"amap_error:{data.get('info', 'unknown')}"

            pois = data.get("pois", []) or []
            merchants = []
            for i, p in enumerate(pois):
                name = p.get("name", "").strip()
                if not name:
                    continue
                distance_m = _to_float(p.get("distance"), 1500.0)
                distance_km = round(distance_m / 1000.0, 2)
                type_text = p.get("type", "")
                tags = _infer_tags(name, type_text)
                cost = _to_float((p.get("biz_ext") or {}).get("cost"), 0.0)
                if cost <= 0:
                    cost = 22.0 + min(distance_km, 4.0) * 3.0
                rating = _to_float((p.get("biz_ext") or {}).get("rating"), 0.0)
                if rating <= 0:
                    # keep deterministic and in realistic range
                    rating = 4.2 + (abs(hash(name)) % 8) / 10.0
                    rating = min(4.9, rating)

                eta = int(18 + distance_km * 7)
                dishes = _infer_dishes(tags, name)
                merchants.append(
                    {
                        "id": f"amap_{p.get('id', i)}",
                        "name": name,
                        "tags": tags,
                        "avg_price": round(cost, 1),
                        "distance_km": distance_km,
                        "rating": round(rating, 1),
                        "delivery_eta_min": eta,
                        "description": f"来自高德POI：{type_text or '周边餐饮'}，距离约{distance_km}km。",
                        "recommended_dishes": dishes,
                        "menu_source": "inferred_amap",
                        "diet_flags": [],
                        "is_open": True,
                    }
                )

            if not merchants:
                return [], "amap_empty_result"
            return merchants, "ok"
        except Exception as exc:
            return [], f"amap_exception:{type(exc).__name__}"

    def geocode_address(self, address: str, city: str = "") -> Tuple[Dict, str]:
        if not self._key:
            return {}, "missing_amap_key"
        if not address.strip():
            return {}, "empty_address"
        url = "https://restapi.amap.com/v3/geocode/geo"
        params = {"key": self._key, "address": address.strip()}
        if city.strip():
            params["city"] = city.strip()
        try:
            resp = requests.get(url, params=params, timeout=self._timeout)
            resp.raise_for_status()
            data = resp.json()
            if str(data.get("status")) != "1":
                return {}, f"amap_error:{data.get('info', 'unknown')}"
            geocodes = data.get("geocodes", []) or []
            if not geocodes:
                return {}, "amap_empty_geocode"
            g = geocodes[0]
            location = g.get("location", "")
            if "," not in location:
                return {}, "amap_invalid_geocode_location"
            lng_str, lat_str = location.split(",", 1)
            return {
                "lat": _to_float(lat_str, 0.0),
                "lng": _to_float(lng_str, 0.0),
                "formatted_address": g.get("formatted_address", address),
                "province": g.get("province", ""),
                "city": g.get("city", ""),
                "district": g.get("district", ""),
            }, "ok"
        except Exception as exc:
            return {}, f"amap_exception:{type(exc).__name__}"

    def _address_tokens(self, text: str) -> List[str]:
        raw = re.split(r"[（）()，,、\s\-]+", text or "")
        return [t.strip() for t in raw if t and len(t.strip()) >= 2]

    def _candidate_score(self, query: str, name: str, addr: str) -> float:
        hay = f"{name} {addr}"
        score = 0.0
        if query and query in hay:
            score += 8.0
        for tok in self._address_tokens(query):
            if tok in hay:
                score += 2.0
        # Prefer exact POI name prefix.
        if query and str(name).startswith(query[: min(len(query), 6)]):
            score += 1.0
        return score

    def _place_text_search(self, address: str, city: str = "") -> Tuple[Dict, str]:
        if not self._key:
            return {}, "missing_amap_key"
        if not address.strip():
            return {}, "empty_address"
        url = "https://restapi.amap.com/v3/place/text"
        params = {
            "key": self._key,
            "keywords": address.strip(),
            "offset": 20,
            "page": 1,
            "extensions": "all",
        }
        if city.strip():
            params["city"] = city.strip()
            params["citylimit"] = "true"
        try:
            resp = requests.get(url, params=params, timeout=self._timeout)
            resp.raise_for_status()
            data = resp.json()
            if str(data.get("status")) != "1":
                return {}, f"amap_error:{data.get('info', 'unknown')}"
            pois = data.get("pois", []) or []
            if not pois:
                return {}, "amap_empty_place_text"

            scored = []
            for p in pois:
                name = str(p.get("name", ""))
                addr = str(p.get("address", ""))
                loc = str(p.get("location", ""))
                if "," not in loc:
                    continue
                score = self._candidate_score(address, name, addr)
                scored.append((score, p))
            if not scored:
                return {}, "amap_invalid_place_text_location"
            scored.sort(key=lambda x: x[0], reverse=True)
            best = scored[0][1]
            lng_str, lat_str = str(best.get("location", "")).split(",", 1)
            return {
                "lat": _to_float(lat_str, 0.0),
                "lng": _to_float(lng_str, 0.0),
                "formatted_address": f"{best.get('pname', '')}{best.get('cityname', '')}{best.get('adname', '')}{best.get('address', '')}",
                "name": best.get("name", ""),
            }, "ok_place_text"
        except Exception as exc:
            return {}, f"amap_exception:{type(exc).__name__}"

    def resolve_address(self, address: str, city: str = "") -> Tuple[Dict, str]:
        # 1) POI text search is usually better for named institutions/shops.
        data, status = self._place_text_search(address=address, city=city)
        if status.startswith("ok"):
            return data, status

        # 2) Fallback to generic geocoding.
        return self.geocode_address(address=address, city=city)

    def ip_locate(self) -> Tuple[Dict, str]:
        if not self._key:
            return {}, "missing_amap_key"
        url = "https://restapi.amap.com/v3/ip"
        params = {"key": self._key}
        try:
            resp = requests.get(url, params=params, timeout=self._timeout)
            resp.raise_for_status()
            data = resp.json()
            if str(data.get("status")) != "1":
                return {}, f"amap_error:{data.get('info', 'unknown')}"
            rect = str(data.get("rectangle", ""))
            # rectangle: "lng1,lat1;lng2,lat2"
            lat = 0.0
            lng = 0.0
            if ";" in rect and "," in rect:
                p1, p2 = rect.split(";", 1)
                lng1, lat1 = p1.split(",", 1)
                lng2, lat2 = p2.split(",", 1)
                lng = (_to_float(lng1, 0.0) + _to_float(lng2, 0.0)) / 2
                lat = (_to_float(lat1, 0.0) + _to_float(lat2, 0.0)) / 2
            result = {
                "lat": round(lat, 6),
                "lng": round(lng, 6),
                "province": data.get("province", ""),
                "city": data.get("city", ""),
                "adcode": data.get("adcode", ""),
            }, "ok"
            if result[0]["lat"] <= 0 or result[0]["lng"] <= 0:
                return result[0], "amap_invalid_ip_location"
            return result
        except Exception as exc:
            return {}, f"amap_exception:{type(exc).__name__}"

    def reverse_geocode(self, location: Location) -> Tuple[Dict, str]:
        if not self._key:
            return {}, "missing_amap_key"
        url = "https://restapi.amap.com/v3/geocode/regeo"
        params = {
            "key": self._key,
            "location": f"{location.lng},{location.lat}",
            "extensions": "base",
            "radius": 1000,
        }
        try:
            resp = requests.get(url, params=params, timeout=self._timeout)
            resp.raise_for_status()
            data = resp.json()
            if str(data.get("status")) != "1":
                return {}, f"amap_error:{data.get('info', 'unknown')}"
            regeo = data.get("regeocode", {}) or {}
            addr = regeo.get("formatted_address", "")
            comp = regeo.get("addressComponent", {}) or {}
            return {
                "formatted_address": addr,
                "province": comp.get("province", ""),
                "city": comp.get("city", ""),
                "district": comp.get("district", ""),
                "township": comp.get("township", ""),
            }, "ok"
        except Exception as exc:
            return {}, f"amap_exception:{type(exc).__name__}"

