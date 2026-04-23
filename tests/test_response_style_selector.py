from app.agents.response_agent import ResponseAgent
import app.agents.response_agent as response_agent_module


def test_detect_explain_style_budget() -> None:
    agent = ResponseAgent()
    assert agent._detect_explain_style("预算30以内，便宜点") == "budget"  # noqa: SLF001


def test_detect_explain_style_health() -> None:
    agent = ResponseAgent()
    assert agent._detect_explain_style("想吃清淡减脂一点") == "health"  # noqa: SLF001


def test_detect_explain_style_speed() -> None:
    agent = ResponseAgent()
    assert agent._detect_explain_style("要更快送到，赶时间") == "speed"  # noqa: SLF001


def test_polish_payload_includes_style_hint(monkeypatch) -> None:
    monkeypatch.setenv("USE_RESPONSE_POLISHER", "true")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("USE_LANGCHAIN_LLM", "false")
    captured = {"user_content": ""}

    def fake_invoke_requests(**kwargs):
        captured["user_content"] = kwargs.get("user_content", "")
        return "已润色文本", "ok_requests"

    monkeypatch.setattr(response_agent_module, "invoke_chat_via_requests", fake_invoke_requests)

    agent = ResponseAgent()
    out = agent._polish(base_text="原始文本", mode="recommend", recs=[], style_hint="budget")  # noqa: SLF001

    assert out
    assert '"style_hint": "budget"' in captured["user_content"]
