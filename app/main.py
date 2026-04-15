import os
from pathlib import Path

from fastapi import FastAPI, Query
from dotenv import load_dotenv

from app.schemas import (
    ChatRequest,
    ChatResponse,
    FeedbackRequest,
    FeedbackResponse,
    Location,
    RecommendRequest,
    RecommendResponse,
)
from app.services.chat_service import ChatService
from app.services.amap_service import AmapPoiService
from app.services.data_repository import MerchantRepository
from app.services.metrics_logger import ReasonMetricsLogger
from app.services.menu_service import MenuService
from app.services.profile_service import ProfileService
from app.services.recommender import RecommenderService

load_dotenv()

app = FastAPI(title="Meituan AI-Diet API", version="0.1.0")

base_dir = Path(__file__).resolve().parent
chat_storage_path = Path(os.getenv("CHAT_STORAGE_PATH", str(base_dir / "data" / "chat_sessions.json")))
profile_storage_path = Path(os.getenv("PROFILE_STORAGE_PATH", str(base_dir / "data" / "user_profiles.json")))
reason_metrics_storage_path = Path(
    os.getenv("REASON_METRICS_LOG_DIR", str(base_dir / "data" / "reason_metrics_logs"))
)
repo = MerchantRepository(
    base_dir / "data" / "merchants.json",
    base_dir / "data" / "merchant_profiles.json",
)
profile_service = ProfileService(profile_storage_path)
amap_service = AmapPoiService()
menu_service = MenuService()
metrics_logger = ReasonMetricsLogger(reason_metrics_storage_path)
recommender = RecommenderService(repo, profile_service, amap_service, menu_service, metrics_logger)
chat_service = ChatService(recommender, chat_storage_path)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/v1/recommend", response_model=RecommendResponse)
def recommend(payload: RecommendRequest) -> RecommendResponse:
    return recommender.recommend(payload)


@app.post("/v1/chat", response_model=ChatResponse)
def chat(payload: ChatRequest) -> ChatResponse:
    return chat_service.chat(payload)


@app.post("/v1/feedback", response_model=FeedbackResponse)
def feedback(payload: FeedbackRequest) -> FeedbackResponse:
    return recommender.feedback(payload.user_id, payload.merchant_id, payload.action)


@app.get("/v1/location/geocode")
def geocode(address: str = Query(..., min_length=2), city: str = "") -> dict:
    data, status = amap_service.resolve_address(address=address, city=city)
    return {"ok": status.startswith("ok"), "status": status, "data": data}


@app.get("/v1/location/ip")
def locate_by_ip() -> dict:
    data, status = amap_service.ip_locate()
    return {"ok": status == "ok", "status": status, "data": data}


@app.get("/v1/location/reverse")
def reverse_geocode(lat: float, lng: float) -> dict:
    data, status = amap_service.reverse_geocode(location=Location(lat=lat, lng=lng))
    return {"ok": status == "ok", "status": status, "data": data}


@app.get("/v1/location/health")
def location_health(test_lat: float = 31.2304, test_lng: float = 121.4737) -> dict:
    checks = {}

    key_ok = amap_service.enabled()
    checks["key_configured"] = {"ok": key_ok, "status": "ok" if key_ok else "missing_amap_key"}

    ip_data, ip_status = amap_service.ip_locate()
    checks["ip_locate"] = {"ok": ip_status == "ok", "status": ip_status, "data": ip_data}

    probe_source = "ip"
    probe_reason = "ip_locate_ok"
    lat = test_lat
    lng = test_lng
    if ip_status == "ok" and ip_data.get("lat") and ip_data.get("lng"):
        lat = float(ip_data["lat"])
        lng = float(ip_data["lng"])
    else:
        probe_source = "fallback_test_coord"
        probe_reason = f"ip_locate_unavailable:{ip_status}"

    loc = Location(lat=lat, lng=lng)
    reverse_data, reverse_status = amap_service.reverse_geocode(loc)
    checks["reverse_geocode"] = {"ok": reverse_status == "ok", "status": reverse_status, "data": reverse_data}

    poi_data, poi_status = amap_service.fetch_nearby_merchants(loc)
    checks["nearby_poi"] = {
        "ok": poi_status == "ok",
        "status": poi_status,
        "count": len(poi_data),
        "sample_names": [m["name"] for m in poi_data[:3]],
    }

    overall_ok = checks["key_configured"]["ok"] and checks["reverse_geocode"]["ok"] and checks["nearby_poi"]["ok"]
    return {
        "ok": overall_ok,
        "probe_source": probe_source,
        "probe_reason": probe_reason,
        "probe_location": {"lat": lat, "lng": lng},
        "checks": checks,
    }
