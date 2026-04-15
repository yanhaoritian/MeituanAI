from __future__ import annotations

import json
import sqlite3
import threading
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List


class ProfileService:
    def __init__(self, profile_path: Path) -> None:
        self._path = profile_path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._db_path = self._resolve_db_path(profile_path)
        self._lock = threading.RLock()
        self._init_db()
        self._migrate_json_if_needed()

    def _resolve_db_path(self, profile_path: Path) -> Path:
        if profile_path.suffix.lower() == ".sqlite3":
            return profile_path
        if profile_path.suffix.lower() == ".json":
            return profile_path.with_suffix(".sqlite3")
        return profile_path.with_name(f"{profile_path.name}.sqlite3")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS user_profiles (
                        user_id TEXT PRIMARY KEY,
                        liked_merchants_json TEXT NOT NULL,
                        disliked_merchants_json TEXT NOT NULL,
                        tag_weights_json TEXT NOT NULL,
                        events_json TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                conn.commit()

    def _default_profile(self) -> Dict:
        return {
            "liked_merchants": [],
            "disliked_merchants": [],
            "tag_weights": {},
            "events": [],
        }

    def _safe_json_load(self, raw: str | None, fallback):
        try:
            value = json.loads(raw or "")
        except (TypeError, json.JSONDecodeError):
            return fallback
        return value if isinstance(value, type(fallback)) else fallback

    def _normalize_profile(self, profile: Dict | None) -> Dict:
        base = self._default_profile()
        if isinstance(profile, dict):
            base.update(profile)
        if not isinstance(base.get("liked_merchants"), list):
            base["liked_merchants"] = []
        if not isinstance(base.get("disliked_merchants"), list):
            base["disliked_merchants"] = []
        if not isinstance(base.get("tag_weights"), dict):
            base["tag_weights"] = {}
        if not isinstance(base.get("events"), list):
            base["events"] = []
        return base

    def _write_profile(self, conn: sqlite3.Connection, user_id: str, profile: Dict) -> None:
        conn.execute(
            """
            INSERT INTO user_profiles (
                user_id,
                liked_merchants_json,
                disliked_merchants_json,
                tag_weights_json,
                events_json,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                liked_merchants_json = excluded.liked_merchants_json,
                disliked_merchants_json = excluded.disliked_merchants_json,
                tag_weights_json = excluded.tag_weights_json,
                events_json = excluded.events_json,
                updated_at = excluded.updated_at
            """,
            (
                user_id,
                json.dumps(profile.get("liked_merchants", []), ensure_ascii=False),
                json.dumps(profile.get("disliked_merchants", []), ensure_ascii=False),
                json.dumps(profile.get("tag_weights", {}), ensure_ascii=False),
                json.dumps(profile.get("events", []), ensure_ascii=False),
                datetime.now().isoformat(),
            ),
        )

    def _migrate_json_if_needed(self) -> None:
        if self._path.suffix.lower() != ".json" or not self._path.exists():
            return
        with self._lock:
            with self._connect() as conn:
                row = conn.execute("SELECT COUNT(*) AS cnt FROM user_profiles").fetchone()
                if row and int(row["cnt"]) > 0:
                    return
                try:
                    payload = json.loads(self._path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    return
                if not isinstance(payload, dict):
                    return
                for user_id, profile in payload.items():
                    self._write_profile(conn, str(user_id), self._normalize_profile(profile))
                conn.commit()

    def get_profile(self, user_id: str) -> Dict:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT
                    liked_merchants_json,
                    disliked_merchants_json,
                    tag_weights_json,
                    events_json
                FROM user_profiles
                WHERE user_id = ?
                """,
                (user_id,),
            ).fetchone()
        if row is None:
            profile = self._default_profile()
            with self._lock:
                with self._connect() as conn:
                    self._write_profile(conn, user_id, profile)
                    conn.commit()
            return profile
        return self._normalize_profile(
            {
                "liked_merchants": self._safe_json_load(row["liked_merchants_json"], []),
                "disliked_merchants": self._safe_json_load(row["disliked_merchants_json"], []),
                "tag_weights": self._safe_json_load(row["tag_weights_json"], {}),
                "events": self._safe_json_load(row["events_json"], []),
            }
        )

    def record_feedback(self, user_id: str, merchant_id: str, action: str, merchant_tags: List[str]) -> Dict:
        with self._lock:
            profile = self.get_profile(user_id)
            liked = set(profile.get("liked_merchants", []))
            disliked = set(profile.get("disliked_merchants", []))
            tag_weights = defaultdict(float, profile.get("tag_weights", {}))

            if action in {"like", "order"}:
                liked.add(merchant_id)
                disliked.discard(merchant_id)
                delta = 1.0 if action == "like" else 1.5
                for tag in merchant_tags:
                    tag_weights[tag] += delta
            elif action == "dislike":
                disliked.add(merchant_id)
                liked.discard(merchant_id)
                for tag in merchant_tags:
                    tag_weights[tag] -= 1.0

            events = profile.get("events", [])
            events.append({"merchant_id": merchant_id, "action": action})
            events = events[-50:]

            normalized = self._normalize_profile(
                {
                    "liked_merchants": sorted(liked),
                    "disliked_merchants": sorted(disliked),
                    "tag_weights": dict(tag_weights),
                    "events": events,
                }
            )
            with self._connect() as conn:
                self._write_profile(conn, user_id, normalized)
                conn.commit()
            return normalized
