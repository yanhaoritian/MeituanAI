from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from langchain_openai import ChatOpenAI


def normalize_chat_api_url(raw_url: str) -> str:
    if raw_url.endswith("/v1/chat/completions"):
        return raw_url
    if raw_url.endswith("/"):
        raw_url = raw_url[:-1]
    if raw_url.endswith("/v1"):
        return f"{raw_url}/chat/completions"
    if raw_url.startswith("http"):
        return f"{raw_url}/v1/chat/completions"
    return "https://api.openai.com/v1/chat/completions"


def extract_message_content(data: Dict[str, Any]) -> str:
    choices = data.get("choices", [])
    if not choices:
        return ""
    message = choices[0].get("message", {})
    content = message.get("content", "")
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
            else:
                parts.append(str(item))
        content = "".join(parts)
    content = str(content).strip()
    content = re.sub(r"^```(?:json)?\s*", "", content)
    content = re.sub(r"\s*```$", "", content)
    return content.strip()


def use_langchain_llm() -> bool:
    return os.getenv("USE_LANGCHAIN_LLM", "true").lower() == "true"


def invoke_chat_via_langchain(
    *,
    api_url: str,
    api_key: str,
    model: str,
    timeout_sec: int,
    system_prompt: str,
    user_content: str,
    temperature: float,
    force_json_object: bool = False,
    run_name: Optional[str] = None,
    tags: Optional[List[str]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Tuple[str, str]:
    llm = ChatOpenAI(
        model=model,
        api_key=api_key,
        base_url=api_url.replace("/chat/completions", ""),
        timeout=timeout_sec,
        temperature=temperature,
    )
    if force_json_object:
        llm = llm.bind(response_format={"type": "json_object"})
    runnable = llm.with_config(
        {
            "run_name": run_name or "llm_call",
            "tags": tags or [],
            "metadata": metadata or {},
        }
    )
    result = runnable.invoke(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]
    )
    content = result.content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                parts.append(str(item.get("text", "")))
            else:
                parts.append(str(item))
        text = "".join(parts).strip()
    else:
        text = str(content).strip()
    return text, "ok_langchain"


def invoke_chat_via_requests(
    *,
    requests_module,
    api_url: str,
    api_key: str,
    model: str,
    timeout_sec: int,
    verify_ssl: bool,
    system_prompt: str,
    user_content: str,
    temperature: float,
    force_json_object: bool = False,
) -> Tuple[str, str]:
    req: Dict[str, Any] = {
        "model": model,
        "temperature": temperature,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
    }
    if force_json_object:
        req["response_format"] = {"type": "json_object"}
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    resp = requests_module.post(api_url, headers=headers, json=req, timeout=timeout_sec, verify=verify_ssl)
    resp.raise_for_status()
    return extract_message_content(resp.json()), "ok_requests"


def to_json_user_payload(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)
