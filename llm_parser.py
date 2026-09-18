# bot/llm_parser.py
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import requests

from core.logger import setup_logger

logger = setup_logger("formpilot.llm")

ALLOWED_INTENTS = {"submit", "status", "stop", "help", "start", "unknown"}

SYSTEM_PROMPT = """You are a command parser for FormPilot, a bot that submits USER-OWNED test forms only.
Extract intent from the user message. Return ONLY valid compact JSON, no markdown.

Schema:
{"intent":"submit|status|stop|help|start|unknown","url":"string or null","count":integer or null}

Rules:
- intent=submit only if user wants form submissions AND a real http(s) URL is present.
- url must be a full URL starting with http:// or https://. Else null.
- count is positive integer 1..100. Default 1 if submit but count missing.
- status/stop/help/start do not need url/count.
- If unclear -> intent=unknown.
- Never invent URLs.
"""


def _extract_json(text: str) -> Optional[Dict[str, Any]]:
    text = text.strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            return None
    return None


def _valid_url(url: Optional[str]) -> Optional[str]:
    if not url or not isinstance(url, str):
        return None
    url = url.strip()
    try:
        p = urlparse(url)
        if p.scheme in {"http", "https"} and p.netloc:
            return url
    except Exception:
        return None
    return None


def heuristic_parse(text: str) -> Dict[str, Any]:
    """Offline fallback if LLM is unavailable."""
    raw = text.strip()
    low = raw.lower()

    if low.startswith("/start") or low in {"start", "hi", "hello"}:
        return {"intent": "start", "url": None, "count": None}
    if low.startswith("/help") or "help" == low:
        return {"intent": "help", "url": None, "count": None}
    if low.startswith("/status") or "status" in low:
        return {"intent": "status", "url": None, "count": None}
    if low.startswith("/stop") or re.search(r"\bstop\b", low):
        return {"intent": "stop", "url": None, "count": None}

    m = re.match(r"^/submit\s+(\S+)\s+(\d+)\s*$", raw, re.I)
    if m:
        return {"intent": "submit", "url": _valid_url(m.group(1)), "count": int(m.group(2))}

    m = re.match(r"^/submit\s+(\S+)\s*$", raw, re.I)
    if m:
        return {"intent": "submit", "url": _valid_url(m.group(1)), "count": 1}

    url_match = re.search(r"https?://[^\s<>\"']+", raw)
    count_match = re.search(r"\b(\d{1,3})\b", raw)
    url = _valid_url(url_match.group(0)) if url_match else None

    submit_words = ("submit", "submission", "form", "bhar", "bharo", "kar do", "karde", "fill")
    if url and any(w in low for w in submit_words):
        count = int(count_match.group(1)) if count_match else 1
        count = max(1, min(count, 100))
        return {"intent": "submit", "url": url, "count": count}

    if url and count_match:
        count = max(1, min(int(count_match.group(1)), 100))
        return {"intent": "submit", "url": url, "count": count}

    return {"intent": "unknown", "url": None, "count": None}


def parse_command(text: str) -> Dict[str, Any]:
    """Prefer LLM; fall back to heuristics."""
    api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    model = os.getenv("LLM_MODEL", "meta-llama/llama-3.1-8b-instruct").strip()
    base = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/")

    if not api_key:
        logger.warning("OPENROUTER_API_KEY missing — using heuristic parser")
        return _normalize(heuristic_parse(text))

    try:
        resp = requests.post(
            f"{base}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": os.getenv("WEBHOOK_URL", "https://localhost"),
                "X-Title": "FormPilot",
            },
            json={
                "model": model,
                "temperature": 0,
                "max_tokens": 200,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": text},
                ],
            },
            timeout=25,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        data = _extract_json(content) or heuristic_parse(text)
        logger.info("LLM parse: %s", data)
        return _normalize(data)
    except Exception as exc:
        logger.warning("LLM parse failed (%s) — heuristic fallback", exc)
        return _normalize(heuristic_parse(text))


def _normalize(data: Dict[str, Any]) -> Dict[str, Any]:
    intent = str(data.get("intent") or "unknown").lower().strip()
    if intent not in ALLOWED_INTENTS:
        intent = "unknown"
    url = _valid_url(data.get("url"))
    count = data.get("count")
    try:
        count = int(count) if count is not None else None
    except Exception:
        count = None
    if count is not None:
        count = max(1, min(count, 100))
    if intent == "submit":
        if not url:
            intent = "unknown"
        if count is None:
            count = 1
    return {"intent": intent, "url": url, "count": count}
