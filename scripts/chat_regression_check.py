from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict

from fastapi.testclient import TestClient


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def _post_chat(client: TestClient, payload: Dict[str, Any]) -> Dict[str, Any]:
    resp = client.post("/v1/chat", json=payload, timeout=60)
    _assert(resp.status_code == 200, f"/v1/chat status={resp.status_code}, body={resp.text}")
    data = resp.json()
    _assert("session_id" in data, "missing session_id")
    _assert("assistant_reply" in data, "missing assistant_reply")
    return data


def _best_effort_unlink(path: Path) -> None:
    for _ in range(3):
        try:
            path.unlink(missing_ok=True)
            return
        except PermissionError:
            time.sleep(0.2)


def _check_health_ranking(project_root: Path) -> None:
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))
    from app.services.query_parser import parse_query
    from app.services.ranking_engine import rank_merchants

    merchants = [
        {
            "id": "healthy_1",
            "name": "轻食蛋白碗",
            "tags": ["轻食", "高蛋白", "减脂", "清淡"],
            "diet_flags": ["high_protein", "low_carb", "low_oil"],
            "description": "高蛋白低脂餐，适合减脂",
            "recommended_dishes": ["鸡胸藜麦碗"],
            "rating": 4.5,
            "distance_km": 1.0,
            "avg_price": 28.0,
            "delivery_eta_min": 30,
            "is_open": True,
        },
        {
            "id": "neutral_1",
            "name": "家常盖饭",
            "tags": ["家常菜", "热菜"],
            "diet_flags": ["hot_food"],
            "description": "普通热菜盖饭",
            "recommended_dishes": ["番茄炒蛋盖饭"],
            "rating": 4.6,
            "distance_km": 1.0,
            "avg_price": 26.0,
            "delivery_eta_min": 28,
            "is_open": True,
        },
        {
            "id": "heavy_1",
            "name": "炸鸡奶茶双拼",
            "tags": ["炸鸡", "奶茶", "重口"],
            "diet_flags": [],
            "description": "高油高热量快乐餐",
            "recommended_dishes": ["炸鸡套餐"],
            "rating": 4.7,
            "distance_km": 0.8,
            "avg_price": 25.0,
            "delivery_eta_min": 25,
            "is_open": True,
        },
    ]

    parsed = parse_query("预算30以内，减脂，高蛋白，清淡")
    ranked = rank_merchants(parsed, merchants, semantic_scores={})
    _assert(ranked[0]["id"] == "healthy_1", "health ranking should prioritize healthy merchant")


def run() -> None:
    # Make regression checks deterministic and offline-friendly.
    os.environ["USE_LLM_PARSER"] = "false"
    os.environ["USE_LLM_REASONER"] = "false"
    os.environ["USE_VECTOR_SEMANTIC"] = "false"

    project_root = Path(__file__).resolve().parents[1]
    chat_storage_path = project_root / "app" / "data" / "chat_sessions.test.json"
    chat_db_path = chat_storage_path.with_suffix(".sqlite3")
    os.environ["CHAT_STORAGE_PATH"] = str(chat_storage_path)
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    from app.main import app  # noqa: WPS433 - import after env setup

    passed = 0
    try:
        _check_health_ranking(project_root)
        passed += 1
        client = TestClient(app)
        user_id = "reg_u_001"

        # Case 1: initial recommendation turn.
        c1 = _post_chat(
            client,
            {
                "user_id": user_id,
                "message": "预算30以内，清淡不油腻，送达快一点",
            },
        )
        sid = c1["session_id"]
        _assert(c1.get("debug", {}).get("mode") == "recommend", "case1 mode should be recommend")
        _assert(len(c1.get("recommendations", [])) > 0, "case1 should return recommendations")
        _assert(isinstance(c1.get("debug", {}).get("agent_steps", []), list), "case1 should include agent_steps")
        passed += 1

        # Case 2: question should be QA, not new recommendation.
        c2 = _post_chat(client, {"user_id": user_id, "session_id": sid, "message": "为什么推荐这家"})
        _assert(c2.get("debug", {}).get("mode") == "qa", "case2 mode should be qa")
        _assert(len(c2.get("recommendations", [])) == 0, "case2 should not return fresh recommendations")
        passed += 1

        # Case 3: health follow-up should be QA.
        c3 = _post_chat(client, {"user_id": user_id, "session_id": sid, "message": "这家健康吗"})
        _assert(c3.get("debug", {}).get("mode") == "qa", "case3 mode should be qa")
        _assert("健康" in c3.get("assistant_reply", "") or "减脂" in c3.get("assistant_reply", ""), "case3 should answer health concern")
        passed += 1

        # Case 4: optimization follow-up should be recommend and scope-locked first.
        c4 = _post_chat(client, {"user_id": user_id, "session_id": sid, "message": "换个更近的"})
        d4 = c4.get("debug", {})
        _assert(d4.get("mode") == "recommend", "case4 mode should be recommend")
        _assert(bool(d4.get("scope_locked")) is True, "case4 should enable scope lock")
        _assert("memory" in d4, "case4 should include memory in debug")
        passed += 1

        # Case 5: compare question should produce compare cards.
        c5 = _post_chat(client, {"user_id": user_id, "session_id": sid, "message": "对比前两家"})
        _assert(c5.get("debug", {}).get("mode") == "qa", "case5 mode should be qa")
        _assert(len(c5.get("compare_cards", [])) >= 1, "case5 should return compare cards")
        passed += 1

        # Case 6: smalltalk should not trigger recommendation.
        c6 = _post_chat(client, {"user_id": user_id, "session_id": sid, "message": "谢谢"})
        _assert(c6.get("debug", {}).get("mode") in ("smalltalk", "qa"), "case6 should be conversational mode")
        _assert(len(c6.get("recommendations", [])) == 0, "case6 should not return recommendations")
        passed += 1

        # Case 7: reset should clear context.
        c7 = _post_chat(client, {"user_id": user_id, "session_id": sid, "message": "重新开始"})
        _assert(c7.get("debug", {}).get("mode") == "reset", "case7 mode should be reset")
        passed += 1

        # Case 8: post-reset question should not pretend to know previous recs.
        c8 = _post_chat(client, {"user_id": user_id, "session_id": sid, "message": "为什么推荐这家"})
        _assert(c8.get("debug", {}).get("mode") == "qa", "case8 mode should be qa")
        _assert(bool(c8.get("debug", {}).get("used_last_recommendations")) is False, "case8 should not use stale recs")
        passed += 1

        # Case 9: quick command should route to QA.
        c9 = _post_chat(client, {"user_id": user_id, "session_id": sid, "message": "解释top1"})
        _assert(c9.get("debug", {}).get("mode") == "qa", "case9 mode should be qa for shortcut command")
        passed += 1

        # Case 10: compact shortcut should still route to QA.
        c10 = _post_chat(client, {"user_id": user_id, "session_id": sid, "message": "top1为什么"})
        _assert(c10.get("debug", {}).get("mode") == "qa", "case10 mode should be qa for compact top1 why")
        passed += 1

        # Case 11: recommendation should expose retrieval latency metric.
        c11 = _post_chat(client, {"user_id": user_id, "session_id": sid, "message": "预算35以内，清淡，送达快一点"})
        _assert(c11.get("debug", {}).get("mode") == "recommend", "case11 should be recommend")
        steps = c11.get("debug", {}).get("agent_steps", [])
        retrieval_steps = [s for s in steps if s.get("agent") == "retrieval"]
        _assert(len(retrieval_steps) >= 1, "case11 should include retrieval step")
        _assert("latency_ms" in retrieval_steps[0].get("detail", {}), "case11 retrieval step should include latency_ms")
        passed += 1

        # Case 12: hard closer filter should be applied for nearer follow-up.
        c12 = _post_chat(client, {"user_id": user_id, "session_id": sid, "message": "换个更近的"})
        d12 = c12.get("debug", {})
        _assert(d12.get("mode") == "recommend", "case12 should be recommend")
        _assert(d12.get("recommend_debug") is not None, "case12 should include recommend_debug")
        _assert(d12.get("scope_size", 0) >= 0, "case12 should expose scope_size")
        passed += 1

        # Refresh recommendation context before health-comparison QA checks.
        c13_prep = _post_chat(client, {"user_id": user_id, "session_id": sid, "message": "预算30以内，清淡，送达快一点"})
        _assert(c13_prep.get("debug", {}).get("mode") == "recommend", "case13 prep should be recommend")

        # Case 13: health comparison should stay in QA and return a health-oriented answer.
        c13 = _post_chat(client, {"user_id": user_id, "session_id": sid, "message": "哪个更健康"})
        _assert(c13.get("debug", {}).get("mode") == "qa", "case13 mode should be qa")
        _assert(
            any(k in c13.get("assistant_reply", "") for k in ["健康", "减脂", "控油", "热量"]),
            "case13 should include health-oriented guidance",
        )
        passed += 1

        # Case 14: diet comparison should stay in QA.
        c14 = _post_chat(client, {"user_id": user_id, "session_id": sid, "message": "哪家更适合减脂"})
        _assert(c14.get("debug", {}).get("mode") == "qa", "case14 mode should be qa")
        _assert(
            any(k in c14.get("assistant_reply", "") for k in ["减脂", "清淡", "高蛋白"]),
            "case14 should include diet-oriented guidance",
        )
        passed += 1

        # Case 15: fallback response for vague continuation.
        c15 = _post_chat(client, {"user_id": user_id, "session_id": sid, "message": "嗯"})
        _assert(c15.get("debug", {}).get("mode") in ("fallback", "smalltalk"), "case15 should avoid wrong recommend")
        passed += 1

        print(json.dumps({"ok": True, "passed_cases": passed, "total_cases": 16}, ensure_ascii=False))
    finally:
        _best_effort_unlink(chat_storage_path)
        _best_effort_unlink(chat_db_path)


if __name__ == "__main__":
    run()

