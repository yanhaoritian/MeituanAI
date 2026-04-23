from app.agents.response_agent import ResponseAgent


def test_postprocess_polish_text_removes_stiff_phrases() -> None:
    agent = ResponseAgent()
    text = "综合考虑你的需求，我推荐给您这家店，比较适合你。"
    out = agent._postprocess_polish_text(text)  # noqa: SLF001
    assert "综合考虑" not in out
    assert "推荐给您" not in out


def test_postprocess_polish_text_keeps_concise_length() -> None:
    agent = ResponseAgent()
    long_text = "这家店口味很稳，送达也快，预算也合适，" * 20
    out = agent._postprocess_polish_text(long_text)  # noqa: SLF001
    assert len(out) <= 140
