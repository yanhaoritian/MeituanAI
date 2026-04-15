from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Dict


class ReasonMetricsLogger:
    def __init__(self, log_dir: Path | None = None) -> None:
        env_dir = os.getenv("REASON_METRICS_LOG_DIR", "").strip()
        default_dir = Path(__file__).resolve().parents[1] / "data" / "reason_metrics_logs"
        self._legacy_log_dir = Path(env_dir) if env_dir else (log_dir or default_dir)
        self._enabled = os.getenv("ENABLE_REASON_METRICS_LOG", "true").lower() == "true"
        self._legacy_log_dir.mkdir(parents=True, exist_ok=True)
        self._db_path = self._resolve_db_path(self._legacy_log_dir)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()
        self._migrate_jsonl_if_needed()

    def _resolve_db_path(self, path: Path) -> Path:
        if path.suffix.lower() == ".sqlite3":
            return path
        if path.suffix:
            return path.with_suffix(".sqlite3")
        return path.parent / "reason_metrics.sqlite3"

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS reason_metrics (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        ts TEXT NOT NULL,
                        day TEXT NOT NULL,
                        trace_id TEXT,
                        user_id TEXT,
                        query TEXT,
                        payload_json TEXT NOT NULL
                    )
                    """
                )
                conn.execute("CREATE INDEX IF NOT EXISTS idx_reason_metrics_day ON reason_metrics(day)")
                conn.commit()

    def _migrate_jsonl_if_needed(self) -> None:
        if self._legacy_log_dir.suffix:
            return
        with self._lock:
            with self._connect() as conn:
                row = conn.execute("SELECT COUNT(*) AS cnt FROM reason_metrics").fetchone()
                if row and int(row["cnt"]) > 0:
                    return
                for file_path in sorted(self._legacy_log_dir.glob("*.jsonl")):
                    day = file_path.stem
                    try:
                        lines = file_path.read_text(encoding="utf-8").splitlines()
                    except OSError:
                        continue
                    rows = []
                    for line in lines:
                        if not line.strip():
                            continue
                        try:
                            payload = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        ts = str(payload.get("ts", datetime.now().isoformat()))
                        rows.append(
                            (
                                ts,
                                day,
                                str(payload.get("trace_id", "")),
                                str(payload.get("user_id", "")),
                                str(payload.get("query", "")),
                                json.dumps(payload, ensure_ascii=False),
                            )
                        )
                    if rows:
                        conn.executemany(
                            """
                            INSERT INTO reason_metrics (
                                ts,
                                day,
                                trace_id,
                                user_id,
                                query,
                                payload_json
                            ) VALUES (?, ?, ?, ?, ?, ?)
                            """,
                            rows,
                        )
                conn.commit()

    def log(self, payload: Dict) -> None:
        if not self._enabled:
            return
        ts = str(payload.get("ts", datetime.now().isoformat()))
        day = ts[:10] if len(ts) >= 10 else datetime.now().strftime("%Y-%m-%d")
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO reason_metrics (
                        ts,
                        day,
                        trace_id,
                        user_id,
                        query,
                        payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        ts,
                        day,
                        str(payload.get("trace_id", "")),
                        str(payload.get("user_id", "")),
                        str(payload.get("query", "")),
                        json.dumps(payload, ensure_ascii=False),
                    ),
                )
                conn.commit()
