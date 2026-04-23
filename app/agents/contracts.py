from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal


AgentMode = Literal["recommend", "qa", "mixed_intent", "smalltalk", "reset", "fallback"]


@dataclass
class AgentDecision:
    mode: AgentMode
    reason: str
    confidence: float = 1.0


@dataclass
class AgentStep:
    agent: str
    status: str
    detail: Dict[str, Any] = field(default_factory=dict)


@dataclass
class RetrievalResult:
    recommendations: List[Dict[str, Any]]
    trace_id: str
    scope_debug: Dict[str, Any] = field(default_factory=dict)

