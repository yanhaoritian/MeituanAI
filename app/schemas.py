from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class Location(BaseModel):
    lat: float
    lng: float


class RecommendRequest(BaseModel):
    user_id: str = Field(..., min_length=1)
    query: str = Field(..., min_length=1, max_length=300)
    location: Optional[Location] = None
    merchant_scope_ids: List[str] = Field(default_factory=list)
    exclude_merchant_ids: List[str] = Field(default_factory=list)
    fast_mode: bool = False


class ParsedSlots(BaseModel):
    taste: List[str] = Field(default_factory=list)
    category: List[str] = Field(default_factory=list)
    budget_max: Optional[float] = None
    distance_max_km: Optional[float] = None
    delivery_eta_max_min: Optional[int] = None
    dietary_restrictions: List[str] = Field(default_factory=list)


class ParsedQuery(BaseModel):
    intent: str
    slots: ParsedSlots
    confidence: float
    conflict_flags: List[str] = Field(default_factory=list)


class RecommendationItem(BaseModel):
    merchant_id: str
    name: str
    score: float
    reason: str
    recommended_dishes: List[str] = Field(default_factory=list)
    dishes_source: str = "inferred"


class RecommendResponse(BaseModel):
    trace_id: str
    parsed_query: ParsedQuery
    recommendations: List[RecommendationItem]
    fallback_applied: bool = False
    debug: Optional[Dict] = None


class FeedbackRequest(BaseModel):
    user_id: str = Field(..., min_length=1)
    merchant_id: str = Field(..., min_length=1)
    action: str = Field(..., pattern="^(like|dislike|order)$")


class FeedbackResponse(BaseModel):
    ok: bool
    message: str
    profile: Optional[Dict] = None


class ChatMessage(BaseModel):
    role: str = Field(..., pattern="^(user|assistant)$")
    content: str = Field(..., min_length=1, max_length=2000)
    time: Optional[str] = None


class ChatRequest(BaseModel):
    user_id: str = Field(..., min_length=1)
    message: str = Field(..., min_length=1, max_length=500)
    session_id: Optional[str] = Field(default=None, min_length=4, max_length=64)
    location: Optional[Location] = None


class ChatResponse(BaseModel):
    session_id: str
    assistant_reply: str
    recommendations: List[RecommendationItem] = Field(default_factory=list)
    compare_cards: List[Dict] = Field(default_factory=list)
    followup_suggestions: List[str] = Field(default_factory=list)
    history: List[ChatMessage] = Field(default_factory=list)
    debug: Optional[Dict] = None
