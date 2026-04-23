import app.services.llm_parser as llm_parser


def test_parse_query_by_llm_uses_langchain_backend_when_enabled(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("USE_LANGCHAIN_LLM", "true")
    monkeypatch.setenv("LLM_VERIFY_SSL", "true")

    def fake_langchain_call(**kwargs):
        return (
            '{"intent":"order_food","slots":{"taste":["清淡"],"budget_max":30},"confidence":0.9,"conflict_flags":[]}',
            "ok_langchain",
        )

    monkeypatch.setattr(llm_parser, "invoke_chat_via_langchain", fake_langchain_call)
    monkeypatch.setattr(
        llm_parser,
        "invoke_chat_via_requests",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("requests backend should not be called")),
    )

    parsed, status = llm_parser.parse_query_by_llm("预算30以内，清淡")

    assert parsed is not None
    assert parsed.slots.budget_max == 30.0
    assert "清淡" in parsed.slots.taste
    assert status == "ok_langchain|ok_validated_schema"


def test_parse_query_by_llm_falls_back_to_requests_when_ssl_verify_disabled(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("USE_LANGCHAIN_LLM", "true")
    monkeypatch.setenv("LLM_VERIFY_SSL", "false")

    monkeypatch.setattr(
        llm_parser,
        "invoke_chat_via_langchain",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("langchain backend should not be called")),
    )
    monkeypatch.setattr(
        llm_parser,
        "invoke_chat_via_requests",
        lambda **kwargs: (
            '{"intent":"order_food","slots":{"distance_max_km":2},"confidence":0.8,"conflict_flags":[]}',
            "ok_requests",
        ),
    )

    parsed, status = llm_parser.parse_query_by_llm("2公里内")

    assert parsed is not None
    assert parsed.slots.distance_max_km == 2.0
    assert status == "ok_requests|ok_validated_schema"


def test_parse_query_by_llm_returns_invalid_schema_for_unexpected_fields(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("USE_LANGCHAIN_LLM", "true")
    monkeypatch.setenv("LLM_VERIFY_SSL", "true")

    monkeypatch.setattr(
        llm_parser,
        "invoke_chat_via_langchain",
        lambda **kwargs: (
            '{"intent":"order_food","slots":{"taste":["清淡"],"unknown_key":"x"},"confidence":0.9,"conflict_flags":[]}',
            "ok_langchain",
        ),
    )

    parsed, status = llm_parser.parse_query_by_llm("清淡")

    assert parsed is None
    assert status == "llm_invalid_schema"
