from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Tuple

import requests
from langchain_openai import OpenAIEmbeddings


def _normalize_embedding_url(raw_url: str) -> str:
    if raw_url.endswith("/v1/embeddings"):
        return raw_url
    if raw_url.endswith("/"):
        raw_url = raw_url[:-1]
    if raw_url.endswith("/v1"):
        return f"{raw_url}/embeddings"
    if raw_url.startswith("http"):
        return f"{raw_url}/v1/embeddings"
    return "https://api.openai.com/v1/embeddings"


def _norm(v: List[float]) -> float:
    return math.sqrt(sum(x * x for x in v))


def _cosine(a: List[float], b: List[float]) -> float:
    na = _norm(a)
    nb = _norm(b)
    if na <= 1e-12 or nb <= 1e-12:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    return max(-1.0, min(1.0, dot / (na * nb)))


class SemanticService:
    def __init__(self) -> None:
        self._enabled = os.getenv("USE_VECTOR_SEMANTIC", "true").lower() == "true"
        raw_backend = os.getenv("SEMANTIC_BACKEND", "legacy_vector").strip().lower()
        self._backend = raw_backend if raw_backend in {"legacy_vector", "langchain_retriever"} else "legacy_vector"
        self._timeout = int(os.getenv("EMBEDDING_TIMEOUT_SEC", os.getenv("LLM_TIMEOUT_SEC", "8")))
        self._api_key = os.getenv("OPENAI_API_KEY", "").strip()
        self._model = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small").strip()
        llm_api_url = os.getenv("LLM_API_URL", "https://api.openai.com/v1/chat/completions")
        emb_api_url = os.getenv("EMBEDDING_API_URL", "").strip()
        if emb_api_url:
            self._api_url = _normalize_embedding_url(emb_api_url)
        else:
            # Reuse base host from LLM url for third-party compatible gateways.
            base = llm_api_url.replace("/chat/completions", "")
            self._api_url = _normalize_embedding_url(base)
        cache_root = Path(__file__).resolve().parents[1] / "data"
        cache_path = os.getenv("EMBEDDING_CACHE_PATH", "").strip()
        self._cache_path = Path(cache_path) if cache_path else (cache_root / "embedding_cache.sqlite3")
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._cache_lock = threading.Lock()
        self._init_cache_db()

    def _connect_cache(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._cache_path, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_cache_db(self) -> None:
        with self._cache_lock:
            with self._connect_cache() as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS embedding_cache (
                        cache_key TEXT PRIMARY KEY,
                        merchant_id TEXT NOT NULL,
                        model TEXT NOT NULL,
                        text_hash TEXT NOT NULL,
                        vector_json TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                conn.commit()

    def enabled(self) -> bool:
        return self._enabled and bool(self._api_key) and bool(self._model)

    def backend_name(self) -> str:
        return self._backend

    def _merchant_text(self, merchant: Dict) -> str:
        name = str(merchant.get("name", ""))
        tags = " ".join(str(x) for x in merchant.get("tags", []))
        desc = str(merchant.get("description", ""))
        dishes = " ".join(str(x) for x in merchant.get("recommended_dishes", [])[:6])
        return f"{name} {tags} {desc} {dishes}".strip()

    def _query_text(self, user_query: str, parsed) -> str:
        slots = []
        slots.extend(parsed.slots.taste or [])
        slots.extend(parsed.slots.category or [])
        return f"{user_query} {' '.join(slots)}".strip()

    def _embed_texts(self, texts: List[str]) -> List[List[float]]:
        req = {"model": self._model, "input": texts}
        headers = {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}
        resp = requests.post(self._api_url, headers=headers, json=req, timeout=self._timeout)
        resp.raise_for_status()
        payload = resp.json()
        data = payload.get("data", [])
        if not isinstance(data, list) or len(data) != len(texts):
            raise ValueError("invalid_embedding_payload")
        vectors: List[List[float]] = []
        for item in data:
            emb = item.get("embedding", [])
            if not isinstance(emb, list) or not emb:
                raise ValueError("invalid_embedding_vector")
            vectors.append([float(x) for x in emb])
        return vectors

    def _embed_texts_langchain(self, texts: List[str]) -> List[List[float]]:
        emb = OpenAIEmbeddings(
            model=self._model,
            api_key=self._api_key,
            base_url=self._api_url.replace("/embeddings", ""),
            request_timeout=self._timeout,
        )
        vectors = emb.embed_documents(texts)
        return [[float(x) for x in v] for v in vectors]

    def _merchant_cache_key(self, merchant: Dict, text: str) -> str:
        mid = str(merchant.get("id", ""))
        text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        raw = f"{self._model}|{mid}|{text_hash}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _load_cached_vectors(self, merchants: List[Dict], merchant_texts: List[str]) -> Tuple[Dict[str, List[float]], List[Tuple[Dict, str]], int]:
        if not merchants:
            return {}, [], 0
        key_map = {}
        for merchant, text in zip(merchants, merchant_texts):
            key_map[self._merchant_cache_key(merchant, text)] = (merchant, text)

        cached_vectors: Dict[str, List[float]] = {}
        missing: List[Tuple[Dict, str]] = []
        with self._cache_lock:
            with self._connect_cache() as conn:
                placeholders = ",".join("?" for _ in key_map)
                rows = conn.execute(
                    f"SELECT cache_key, merchant_id, vector_json FROM embedding_cache WHERE cache_key IN ({placeholders})",
                    tuple(key_map.keys()),
                ).fetchall()
        hit_count = 0
        found_keys = set()
        for row in rows:
            try:
                vector = json.loads(row["vector_json"])
            except json.JSONDecodeError:
                continue
            if not isinstance(vector, list) or not vector:
                continue
            merchant_id = str(row["merchant_id"])
            cached_vectors[merchant_id] = [float(x) for x in vector]
            found_keys.add(row["cache_key"])
            hit_count += 1

        for cache_key, payload in key_map.items():
            if cache_key not in found_keys:
                missing.append(payload)
        return cached_vectors, missing, hit_count

    def _store_cached_vectors(self, merchant_vectors: List[Tuple[Dict, str, List[float]]]) -> None:
        if not merchant_vectors:
            return
        now = datetime.now().isoformat()
        rows = []
        for merchant, text, vector in merchant_vectors:
            text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
            rows.append(
                (
                    self._merchant_cache_key(merchant, text),
                    str(merchant.get("id", "")),
                    self._model,
                    text_hash,
                    json.dumps(vector, ensure_ascii=False),
                    now,
                )
            )
        with self._cache_lock:
            with self._connect_cache() as conn:
                conn.executemany(
                    """
                    INSERT INTO embedding_cache (
                        cache_key,
                        merchant_id,
                        model,
                        text_hash,
                        vector_json,
                        updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(cache_key) DO UPDATE SET
                        merchant_id = excluded.merchant_id,
                        model = excluded.model,
                        text_hash = excluded.text_hash,
                        vector_json = excluded.vector_json,
                        updated_at = excluded.updated_at
                    """,
                    rows,
                )
                conn.commit()

    def _score_merchants_legacy_vector(self, *, user_query: str, parsed, merchants: List[Dict]) -> Tuple[Dict[str, float], str]:
        q = self._query_text(user_query, parsed)
        merchant_texts = [self._merchant_text(m) for m in merchants]
        cached_vectors, missing, hit_count = self._load_cached_vectors(merchants, merchant_texts)

        vectors = self._embed_texts([q, *[text for _, text in missing]])
        q_vec = vectors[0]
        if missing:
            fresh_vectors = vectors[1:]
            self._store_cached_vectors(
                [
                    (merchant, text, vec)
                    for (merchant, text), vec in zip(missing, fresh_vectors)
                ]
            )
            for (merchant, _), vec in zip(missing, fresh_vectors):
                cached_vectors[str(merchant.get("id", ""))] = vec

        scores: Dict[str, float] = {}
        for merchant in merchants:
            mid = str(merchant.get("id", ""))
            vec = cached_vectors.get(mid)
            if not vec:
                continue
            scores[mid] = round((_cosine(q_vec, vec) + 1.0) / 2.0, 4)

        if len(scores) != len(merchants):
            return {}, "vector_failed:missing_cached_vectors"
        miss_count = len(missing)
        return scores, f"vector_ok|cache_hits={hit_count}|cache_misses={miss_count}"

    def _score_merchants_langchain_retriever(self, *, user_query: str, parsed, merchants: List[Dict]) -> Tuple[Dict[str, float], str]:
        q = self._query_text(user_query, parsed)
        merchant_texts = [self._merchant_text(m) for m in merchants]
        vectors = self._embed_texts_langchain([q, *merchant_texts])
        q_vec = vectors[0]
        merchant_vecs = vectors[1:]
        if len(merchant_vecs) != len(merchants):
            return {}, "langchain_failed:invalid_vector_size"
        scores: Dict[str, float] = {}
        for merchant, vec in zip(merchants, merchant_vecs):
            mid = str(merchant.get("id", ""))
            scores[mid] = round((_cosine(q_vec, vec) + 1.0) / 2.0, 4)
        return scores, f"langchain_ok|count={len(scores)}"

    def score_merchants(self, *, user_query: str, parsed, merchants: List[Dict]) -> Tuple[Dict[str, float], str]:
        if not self.enabled():
            return {}, "vector_disabled_or_missing_key"
        if not merchants:
            return {}, "vector_no_merchants"
        try:
            if self._backend == "langchain_retriever":
                try:
                    return self._score_merchants_langchain_retriever(
                        user_query=user_query,
                        parsed=parsed,
                        merchants=merchants,
                    )
                except Exception as exc:
                    fallback_scores, fallback_status = self._score_merchants_legacy_vector(
                        user_query=user_query,
                        parsed=parsed,
                        merchants=merchants,
                    )
                    return fallback_scores, f"langchain_failed:{type(exc).__name__}|fallback={fallback_status}"
            return self._score_merchants_legacy_vector(
                user_query=user_query,
                parsed=parsed,
                merchants=merchants,
            )
        except Exception as exc:
            return {}, f"vector_failed:{type(exc).__name__}"
