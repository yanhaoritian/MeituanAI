from app.services.semantic_service import SemanticService


def test_semantic_service_uses_legacy_backend_by_default(monkeypatch) -> None:
    monkeypatch.delenv("SEMANTIC_BACKEND", raising=False)
    svc = SemanticService()
    assert svc.backend_name() == "legacy_vector"


def test_semantic_service_can_switch_to_langchain_backend(monkeypatch) -> None:
    monkeypatch.setenv("SEMANTIC_BACKEND", "langchain_retriever")
    svc = SemanticService()
    assert svc.backend_name() == "langchain_retriever"
