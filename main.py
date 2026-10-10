from __future__ import annotations

import json
import os
import secrets
import uuid
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse

BASE_DIR = Path(__file__).resolve().parent
WEB_DIR = BASE_DIR / "static"
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "").strip()
MAX_BODY_BYTES = 16_000
signals: deque[dict[str, Any]] = deque(maxlen=300)

app = FastAPI(title="SALEM TradingView Bridge", version="1.0.0")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def authorized(token: str | None) -> bool:
    return bool(WEBHOOK_SECRET) and bool(token) and secrets.compare_digest(token, WEBHOOK_SECRET)


def safe_text(value: Any, default: str = "", max_len: int = 100) -> str:
    if value is None:
        return default
    return str(value).strip()[:max_len]


def to_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        result = float(value)
        if result != result or result in (float("inf"), float("-inf")):
            return None
        return round(result, 6)
    except (TypeError, ValueError):
        return None


def to_score(value: Any) -> int | None:
    n = to_float(value)
    if n is None:
        return None
    return max(0, min(100, int(round(n))))


def normalize_payload(payload: Any, *, is_test: bool = False) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Webhook body must be a JSON object")

    action = safe_text(payload.get("action") or payload.get("signal") or payload.get("side") or payload.get("direction"), "WAIT", 24).upper()
    action_map = {
        "BUY": "CALL", "LONG": "CALL", "BULLISH": "CALL", "BULL": "CALL",
        "SELL": "PUT", "SHORT": "PUT", "BEARISH": "PUT", "BEAR": "PUT",
        "HOLD": "WAIT", "NEUTRAL": "WAIT", "NO TRADE": "WAIT",
    }
    action = action_map.get(action, action)
    if action not in {"CALL", "PUT", "WAIT", "BUY", "SELL", "LONG", "SHORT", "UNKNOWN"}:
        action = "UNKNOWN"

    price = to_float(payload.get("price", payload.get("close", payload.get("last"))))
    received = {
        "id": uuid.uuid4().hex,
        "received_at": utc_now(),
        "symbol": safe_text(payload.get("symbol") or payload.get("ticker") or payload.get("tickerid"), "UNKNOWN", 40).upper(),
        "exchange": safe_text(payload.get("exchange"), "", 40),
        "action": action,
        "price": price,
        "timeframe": safe_text(payload.get("timeframe") or payload.get("interval"), "", 24),
        "score": to_score(payload.get("score") or payload.get("ai_score")),
        "indicator": safe_text(payload.get("indicator") or payload.get("strategy") or payload.get("source"), "Pine Script", 120),
        "bar_time": safe_text(payload.get("time") or payload.get("bar_time"), "", 50),
        "test": bool(is_test or payload.get("test", False)),
    }
    return received


@app.get("/", include_in_schema=False)
async def home() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {"status": "ok", "webhook_configured": bool(WEBHOOK_SECRET), "signal_count": len(signals), "timestamp": utc_now()}


@app.get("/api/signals")
async def list_signals() -> dict[str, Any]:
    return {"signals": list(reversed(signals)), "count": len(signals)}


@app.post("/webhook")
async def receive_webhook(request: Request, token: str | None = Query(default=None)) -> JSONResponse:
    if not authorized(token):
        raise HTTPException(status_code=401, detail="Invalid webhook token")
    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="Webhook body too large")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise HTTPException(status_code=400, detail="Webhook body must be valid JSON")
    event = normalize_payload(payload)
    signals.append(event)
    return JSONResponse({"status": "accepted", "id": event["id"]}, status_code=202)


@app.post("/api/test-signal")
async def test_signal(token: str | None = Query(default=None)) -> dict[str, Any]:
    if not authorized(token):
        raise HTTPException(status_code=401, detail="Secret does not match WEBHOOK_SECRET")
    event = normalize_payload({
        "indicator": "SALEM Bridge Test",
        "symbol": "TEST",
        "exchange": "DEMO",
        "action": "CALL",
        "price": 100.25,
        "timeframe": "15",
        "score": 88,
        "test": True,
    }, is_test=True)
    signals.append(event)
    return {"status": "accepted", "signal": event}


@app.get("/favicon.ico", include_in_schema=False)
async def favicon() -> JSONResponse:
    return JSONResponse({}, status_code=204)
