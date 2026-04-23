from app.agents.memory_agent import MemoryAgent
from app.agents.orchestrator_agent import OrchestratorAgent


def test_orchestrator_routes_mixed_intent_when_question_and_optimize_coexist() -> None:
    agent = OrchestratorAgent()
    decision = agent.decide(message="解释下top1，然后换个更近的", has_last_query=True)
    assert decision.mode == "mixed_intent"
    assert decision.reason == "qa_plus_optimize"


def test_memory_agent_merges_constraint_layers_with_hard_priority() -> None:
    agent = MemoryAgent()
    prev_memory = {
        "constraint_layers": {
            "hard": ["no_meat"],
            "soft": ["prefer_closer"],
            "transient": ["heat_now"],
        }
    }
    layers = agent.constraint_layers(
        message="不要肉，预算再便宜点，今天想吃热乎点",
        memory=prev_memory,
    )
    assert "no_meat" in layers["hard"]
    assert "prefer_cheaper" in layers["soft"]
    assert layers["transient"] == ["heat_now"]


def test_memory_agent_detects_relax_distance_intent() -> None:
    agent = MemoryAgent()
    hard = agent.hard_constraints(message="远距离也可以，再推荐一家")
    assert hard["relax_distance"] is True
