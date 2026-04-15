import json
from pathlib import Path
from typing import Dict, List


class MerchantRepository:
    def __init__(self, json_path: Path, profile_path: Path | None = None) -> None:
        self._json_path = json_path
        self._profile_path = profile_path
        self._merchants = self._load()
        self._profiles = self._load_profiles()
        self._enriched_merchants = self._merge_profiles()

    def _load(self) -> List[Dict]:
        with self._json_path.open("r", encoding="utf-8") as f:
            return json.load(f)

    def _load_profiles(self) -> Dict:
        if not self._profile_path or not self._profile_path.exists():
            return {}
        with self._profile_path.open("r", encoding="utf-8") as f:
            try:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
            except json.JSONDecodeError:
                return {}

    def _merge_profiles(self) -> List[Dict]:
        merged: List[Dict] = []
        for m in self._merchants:
            profile = self._profiles.get(m.get("id", ""), {})
            merged.append({**m, **profile})
        return merged

    def list_all(self) -> List[Dict]:
        return self._enriched_merchants
