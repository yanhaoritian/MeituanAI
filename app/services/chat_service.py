from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

from app.agents.contracts import AgentStep
from app.agents.memory_agent import MemoryAgent
from app.agents.orchestrator_agent import OrchestratorAgent
from app.agents.retrieval_agent import RetrievalAgent
from app.agents.response_agent import ResponseAgent
from app.schemas import ChatMessage, ChatRequest, ChatResponse
from app.services.recommender import RecommenderService


class ChatService:
    def __init__(self, recommender: RecommenderService, storage_path: Path) -> None:
        self._recommender = recommender
        self._orchestrator = OrchestratorAgent()
        self._retrieval_agent = RetrievalAgent(recommender)
        self._response_agent = ResponseAgent()
        self._memory_agent = MemoryAgent()
        self._storage_path = storage_path
        self._storage_path.parent.mkdir(parents=True, exist_ok=True)
        self._db_path = self._resolve_db_path(storage_path)
        self._db_lock = threading.Lock()
        self._session_locks: Dict[str, threading.Lock] = {}
        self._session_locks_guard = threading.Lock()
        self._init_db()
        self._migrate_json_if_needed()

    def _resolve_db_path(self, storage_path: Path) -> Path:
        if storage_path.suffix.lower() == ".sqlite3":
            return storage_path
        if storage_path.suffix.lower() == ".json":
            return storage_path.with_suffix(".sqlite3")
        return storage_path.with_name(f"{storage_path.name}.sqlite3")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._db_lock:
            with self._connect() as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS chat_sessions (
                        session_id TEXT PRIMARY KEY,
                        user_id TEXT NOT NULL,
                        history_json TEXT NOT NULL,
                        last_query TEXT NOT NULL,
                        last_trace_id TEXT NOT NULL,
                        last_scope_ids_json TEXT NOT NULL,
                        last_recommendations_json TEXT NOT NULL,
                        last_top_metrics_json TEXT NOT NULL,
                        memory_json TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                conn.commit()

    def _safe_json_load(self, raw: str, fallback: Any) -> Any:
        try:
            value = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return fallback
        return value if isinstance(value, type(fallback)) else fallback

    def _default_session(self, user_id: str) -> Dict[str, Any]:
        return {
            "history": [],
            "last_query": "",
            "last_trace_id": "",
            "last_scope_ids": [],
            "last_recommendations": [],
            "last_top_metrics": {},
            "memory": {},
            "user_id": user_id,
        }

    def _normalize_session(self, session: Dict[str, Any] | None, user_id: str) -> Dict[str, Any]:
        base = self._default_session(user_id)
        if not isinstance(session, dict):
            return base
        base.update(session)
        if not isinstance(base.get("history"), list):
            base["history"] = []
        if not isinstance(base.get("last_scope_ids"), list):
            base["last_scope_ids"] = []
        if not isinstance(base.get("last_recommendations"), list):
            base["last_recommendations"] = []
        if not isinstance(base.get("last_top_metrics"), dict):
            base["last_top_metrics"] = {}
        if not isinstance(base.get("memory"), dict):
            base["memory"] = {}
        base["user_id"] = user_id
        return base

    def _migrate_json_if_needed(self) -> None:
        if self._storage_path.suffix.lower() != ".json" or not self._storage_path.exists():
            return
        with self._db_lock:
            with self._connect() as conn:
                row = conn.execute("SELECT COUNT(*) AS cnt FROM chat_sessions").fetchone()
                if row and int(row["cnt"]) > 0:
                    return
                try:
                    legacy = json.loads(self._storage_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    return
                if not isinstance(legacy, dict):
                    return
                for session_id, payload in legacy.items():
                    session = self._normalize_session(payload, str((payload or {}).get("user_id", "")))
                    self._write_session(conn, session_id, session)
                conn.commit()

    def _write_session(self, conn: sqlite3.Connection, session_id: str, session: Dict[str, Any]) -> None:
        conn.execute(
            """
            INSERT INTO chat_sessions (
                session_id,
                user_id,
                history_json,
                last_query,
                last_trace_id,
                last_scope_ids_json,
                last_recommendations_json,
                last_top_metrics_json,
                memory_json,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                user_id = excluded.user_id,
                history_json = excluded.history_json,
                last_query = excluded.last_query,
                last_trace_id = excluded.last_trace_id,
                last_scope_ids_json = excluded.last_scope_ids_json,
                last_recommendations_json = excluded.last_recommendations_json,
                last_top_metrics_json = excluded.last_top_metrics_json,
                memory_json = excluded.memory_json,
                updated_at = excluded.updated_at
            """,
            (
                session_id,
                str(session.get("user_id", "")),
                json.dumps(session.get("history", []), ensure_ascii=False),
                str(session.get("last_query", "")),
                str(session.get("last_trace_id", "")),
                json.dumps(session.get("last_scope_ids", []), ensure_ascii=False),
                json.dumps(session.get("last_recommendations", []), ensure_ascii=False),
                json.dumps(session.get("last_top_metrics", {}), ensure_ascii=False),
                json.dumps(session.get("memory", {}), ensure_ascii=False),
                datetime.now().isoformat(),
            ),
        )

    def _load_session(self, session_id: str, user_id: str) -> Dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT
                    user_id,
                    history_json,
                    last_query,
                    last_trace_id,
                    last_scope_ids_json,
                    last_recommendations_json,
                    last_top_metrics_json,
                    memory_json
                FROM chat_sessions
                WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()
        if row is None:
            return self._default_session(user_id)
        return self._normalize_session(
            {
                "history": self._safe_json_load(row["history_json"], []),
                "last_query": row["last_query"],
                "last_trace_id": row["last_trace_id"],
                "last_scope_ids": self._safe_json_load(row["last_scope_ids_json"], []),
                "last_recommendations": self._safe_json_load(row["last_recommendations_json"], []),
                "last_top_metrics": self._safe_json_load(row["last_top_metrics_json"], {}),
                "memory": self._safe_json_load(row["memory_json"], {}),
                "user_id": row["user_id"],
            },
            user_id,
        )

    def _save_session(self, session_id: str, session: Dict[str, Any]) -> None:
        with self._db_lock:
            with self._connect() as conn:
                self._write_session(conn, session_id, session)
                conn.commit()

    def _session_lock(self, session_id: str) -> threading.Lock:
        with self._session_locks_guard:
            lock = self._session_locks.get(session_id)
            if lock is None:
                lock = threading.Lock()
                self._session_locks[session_id] = lock
            return lock

    def _new_session_id(self) -> str:
        return f"s_{uuid.uuid4().hex[:12]}"

    def _answer_question(self, text: str, session: Dict[str, Any]) -> Tuple[str, List[Dict[str, Any]]]:
        recs = session.get("last_recommendations", []) or []
        return self._response_agent.answer_question(text=text, recs=recs)

    def _handle_smalltalk(self, text: str, session: Dict[str, Any]) -> str:
        return self._response_agent.handle_smalltalk(text=text, has_last_query=bool(session.get("last_query")))

    def chat(self, payload: ChatRequest) -> ChatResponse:
        session_id = payload.session_id or self._new_session_id()
        with self._session_lock(session_id):
            session = self._load_session(session_id, payload.user_id)

            now = datetime.now().strftime("%H:%M:%S")
            user_msg = ChatMessage(role="user", content=payload.message, time=now)
            session.setdefault("history", []).append(user_msg.model_dump())
            session["user_id"] = payload.user_id

            debug: Dict[str, Any] = {"mode": "chitchat"}
            recs = []
            compare_cards: List[Dict[str, Any]] = []
            assistant_reply = ""
            mode = "chitchat"
            agent_steps: List[Dict[str, Any]] = []
            decision = self._orchestrator.decide(
                message=payload.message,
                has_last_query=bool(session.get("last_query")),
            )
            agent_steps.append(
                AgentStep(
                    agent="orchestrator",
                    status="ok",
                    detail={
                        "mode": decision.mode,
                        "reason": decision.reason,
                        "confidence": decision.confidence,
                    },
                ).__dict__
            )

            if decision.mode == "reset":
                session["last_query"] = ""
                session["last_trace_id"] = ""
                session["last_scope_ids"] = []
                session["last_recommendations"] = []
                session["last_top_metrics"] = {}
                session["memory"] = {}
                assistant_reply = "好的，我们从头来。你现在告诉我这次想吃什么，我按新的需求重新给你推荐。"
                mode = "reset"
                debug = {"mode": "reset"}
            elif decision.mode == "qa":
                assistant_reply, compare_cards = self._answer_question(payload.message, session)
                qa_style = self._response_agent.explain_style(payload.message)
                assistant_reply = self._response_agent.finalize_reply(
                    base_text=assistant_reply,
                    mode="qa",
                    recs=session.get("last_recommendations", []) or [],
                    fast_mode=False,
                    style_hint=qa_style,
                )
                mode = "qa"
                debug = {
                    "mode": "qa",
                    "used_last_recommendations": bool(session.get("last_recommendations")),
                    "compare_cards_count": len(compare_cards),
                }
            elif decision.mode in ("recommend", "mixed_intent"):
                pref_note = ""
                if decision.mode == "mixed_intent":
                    qa_hint, _ = self._answer_question(payload.message, session)
                    if qa_hint:
                        pref_note = f"{qa_hint}\n\n"
                merged_query, rewrite_status = self._memory_agent.build_query(
                    message=payload.message,
                    last_query=str(session.get("last_query", "")),
                )
                hard = self._memory_agent.hard_constraints(message=payload.message)
                scope_ids = []
                exclude_ids: List[str] = []
                fast_mode = False
                require_closer_than_km = None
                if rewrite_status == "followup_merged":
                    scope_ids = list(session.get("last_scope_ids", []) or [])
                    fast_mode = True
                    if any(k in payload.message for k in ["换", "更近", "换个", "另一家", "再推荐一家", "再来一家", "再给一家"]):
                        top_prev = (session.get("last_recommendations", []) or [{}])[0].get("merchant_id")
                        if top_prev:
                            exclude_ids.append(str(top_prev))
                    if hard.get("require_closer"):
                        prev_dist = (session.get("last_top_metrics") or {}).get("distance_km")
                        if isinstance(prev_dist, (float, int)):
                            require_closer_than_km = float(prev_dist)
                    if hard.get("relax_distance"):
                        # User explicitly allows farther candidates: unlock scope and avoid inheriting strict near-distance phrasing.
                        scope_ids = []
                        fast_mode = False
                        merged_query = f"{payload.message}；8公里内也可接受"
                t0 = time.perf_counter()
                retrieval_result = self._retrieval_agent.recommend_with_scope_fallback(
                    user_id=payload.user_id,
                    merged_query=merged_query,
                    location=payload.location,
                    scope_ids=scope_ids,
                    exclude_ids=exclude_ids,
                    fast_mode=fast_mode,
                    require_closer_than_km=require_closer_than_km,
                )
                recs = retrieval_result.recommendations
                trace_id = retrieval_result.trace_id
                scope_debug = retrieval_result.scope_debug
                t1 = time.perf_counter()
                agent_steps.append(
                    AgentStep(
                        agent="retrieval",
                        status="ok",
                        detail={
                            "rewrite_status": rewrite_status,
                            "scope_size": len(scope_ids),
                            "exclude_size": len(exclude_ids),
                            "scope_fallback_unlocked": scope_debug.get("scope_fallback_unlocked", False),
                            "fast_mode": fast_mode,
                            "latency_ms": int((t1 - t0) * 1000),
                            "hard_closer_applied": scope_debug.get("hard_closer_applied", False),
                        },
                    ).__dict__
                )
                assistant_reply = self._response_agent.build_recommend_reply(query=merged_query, recs=recs)
                recommend_style = self._response_agent.explain_style(merged_query)
                if hard.get("require_closer") and not recs:
                    assistant_reply = "我按“更近”这个硬条件筛过了，当前附近没有比上一家更近且满足你条件的店了。要不要我改成“更快送达”再给你一版？"
                if scope_debug.get("scope_fallback_unlocked"):
                    assistant_reply = f"{assistant_reply}（这轮在原备选里无可行结果，我已自动扩展到全量店铺继续找）"
                assistant_reply = self._response_agent.finalize_reply(
                    base_text=f"{pref_note}{assistant_reply}" if pref_note else assistant_reply,
                    mode="recommend",
                    recs=recs,
                    fast_mode=fast_mode,
                    style_hint=recommend_style,
                )
                session["last_query"] = merged_query
                session["last_trace_id"] = trace_id
                session["last_scope_ids"] = [str(x.get("merchant_id")) for x in recs if x.get("merchant_id")]
                session["last_recommendations"] = recs[:5]
                session["memory"] = self._memory_agent.update_memory(
                    memory=session.get("memory") or {},
                    merged_query=merged_query,
                )
                snapshot = (scope_debug.get("recommend_debug") or {}).get("selected_snapshot", [])
                session["last_top_metrics"] = snapshot[0] if snapshot else {}
                debug = {
                    "mode": "mixed_intent" if decision.mode == "mixed_intent" else "recommend",
                    "rewrite_status": rewrite_status,
                    "scope_locked": scope_debug.get("scope_locked", bool(scope_ids)),
                    "scope_fallback_unlocked": scope_debug.get("scope_fallback_unlocked", False),
                    "scope_size": len(scope_ids),
                    "exclude_size": len(exclude_ids),
                    "fast_mode": fast_mode,
                    "trace_id": trace_id,
                    "recommend_debug": scope_debug.get("recommend_debug", {}),
                    "known_constraints": self._response_agent.extract_known_constraints(merged_query),
                    "memory": session.get("memory", {}),
                    "constraint_layers": (session.get("memory", {}) or {}).get("constraint_layers", {}),
                }
                mode = "recommend"
            elif decision.mode == "smalltalk":
                assistant_reply = self._handle_smalltalk(payload.message, session)
                mode = "smalltalk"
                debug = {"mode": "smalltalk"}
            else:
                assistant_reply = (
                    "我理解到你在继续聊点餐，但这句约束还不够明确。"
                    "你可以补一句像“预算30以内、清淡不油腻、40分钟内送到”，我就能精准重排。"
                )
                debug = {"mode": "fallback"}

            debug["agent_steps"] = agent_steps

            assistant_msg = ChatMessage(role="assistant", content=assistant_reply, time=now)
            session["history"].append(assistant_msg.model_dump())
            session["history"] = session["history"][-20:]
            self._save_session(session_id, session)

            history = [ChatMessage(**x) for x in session["history"]]
            return ChatResponse(
                session_id=session_id,
                assistant_reply=assistant_reply,
                recommendations=recs,
                compare_cards=compare_cards,
                followup_suggestions=self._response_agent.suggestions(mode=mode, recs=recs),
                history=history,
                debug=debug,
            )
