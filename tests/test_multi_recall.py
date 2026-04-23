from app.schemas import ParsedQuery, ParsedSlots
from app.services.recommender import RecommenderService


class _ProfileStub:
    def __init__(self, liked):
        self._liked = liked

    def get_profile(self, user_id: str):
        return {"liked_merchants": self._liked, "disliked_merchants": [], "tag_weights": {}}


def _merchant(mid: str, name: str, tags: list[str], rating: float = 4.5):
    return {"id": mid, "name": name, "tags": tags, "description": "", "rating": rating}


def test_multi_recall_pool_keeps_liked_and_semantic_top() -> None:
    svc = RecommenderService.__new__(RecommenderService)
    svc._profile_service = _ProfileStub(liked=["m_like"])
    parsed = ParsedQuery(
        intent="order_food",
        slots=ParsedSlots(taste=["清淡"], category=["轻食"], budget_max=40.0, distance_max_km=3.0),
        confidence=0.8,
        conflict_flags=[],
    )
    candidates = [
        _merchant("m_like", "老用户爱店", ["家常"]),
        _merchant("m_sem", "语义高分店", ["轻食"]),
        _merchant("m_other", "普通店", ["盖饭"]),
    ]
    semantic = {"m_like": 0.1, "m_sem": 0.98, "m_other": 0.2}

    pool, debug = svc._build_multi_recall_pool(  # noqa: SLF001
        user_id="u1",
        parsed=parsed,
        candidates=candidates,
        semantic_scores=semantic,
    )

    mids = [m["id"] for m in pool]
    assert "m_like" in mids
    assert "m_sem" in mids
    assert debug["enabled"] is True
