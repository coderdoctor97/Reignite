"""
Demo Provider — a local mock provider for testing the whole system.

Serves TWO API surfaces on one port (default 5900):

1. OpenAI-compatible inference API (what the gateway proxies to):
       GET  /v1/models
       POST /v1/chat/completions   (stream + non-stream)

2. Legacy-style Opus dashboard API (what session validation and the
   provider key workflows talk to):
       GET    /dashboard/api/keys
       POST   /dashboard/api/keys
       DELETE /dashboard/api/keys/{key_id}

The dashboard requires the session cookie declared in the demo provider
row (default cookie name: opus_session, value: demo-session-secret).
Keys live in memory with a fixed token quota; creating keys when quota
is exhausted returns the legacy-style 400 "Not enough tokens" error.

Run:  python -m app.demo.demo_provider
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Optional

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

app = FastAPI(title="Demo Provider", docs_url=None, redoc_url=None)

SESSION_COOKIE = "opus_session"
SESSION_SECRET = "demo-session-secret"
TOTAL_QUOTA = 1_500_000
DAILY_LIMIT = 300_000

_keys: dict[str, dict] = {}
_quota_used = 0

OPENAI_MODELS = [
    {"id": "demo-gpt-fast", "object": "model", "created": 1700000000, "owned_by": "demo"},
    {"id": "demo-gpt-pro", "object": "model", "created": 1700000000, "owned_by": "demo"},
    {"id": "demo-claude-sonnet", "object": "model", "created": 1700000000, "owned_by": "demo"},
]


# ── Auth helpers ─────────────────────────────────────────────────

def _dashboard_authorized(request: Request) -> bool:
    return request.cookies.get(SESSION_COOKIE) == SESSION_SECRET


def _api_authorized(request: Request) -> bool:
    auth = request.headers.get("authorization", "")
    return auth.startswith("Bearer ")


# ── Dashboard API (legacy Opus style) ────────────────────────────

@app.get("/dashboard/api/keys")
async def list_keys(request: Request):
    if not _dashboard_authorized(request):
        return JSONResponse(status_code=401, content={"error": "unauthorized"})
    return list(_keys.values())


@app.post("/dashboard/api/keys")
async def create_key(request: Request):
    if not _dashboard_authorized(request):
        return JSONResponse(status_code=401, content={"error": "unauthorized"})
    global _quota_used
    body = await request.json()
    if _quota_used >= TOTAL_QUOTA:
        return JSONResponse(status_code=400, content={"error": "Not enough tokens"})
    key_id = str(uuid.uuid4().hex[:12])
    key_value = f"demo-{uuid.uuid4().hex[:24]}"
    _keys[key_id] = {
        "id": key_id,
        "name": body.get("name", "demo"),
        "key": key_value,
        "dailyTokenLimit": body.get("dailyTokenLimit", DAILY_LIMIT),
        "createdAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    _quota_used += body.get("dailyTokenLimit", DAILY_LIMIT)
    return JSONResponse(status_code=201, content=_keys[key_id])


@app.delete("/dashboard/api/keys/{key_id}")
async def delete_key(key_id: str, request: Request):
    if not _dashboard_authorized(request):
        return JSONResponse(status_code=401, content={"error": "unauthorized"})
    if key_id in _keys:
        _keys.pop(key_id)
        return Response(status_code=204)
    return JSONResponse(status_code=404, content={"error": "not found"})


# ── OpenAI-compatible inference API ──────────────────────────────

@app.get("/v1/models")
async def list_models(request: Request):
    if not _api_authorized(request):
        return JSONResponse(status_code=401, content={"error": "unauthorized"})
    return {"object": "list", "data": OPENAI_MODELS}


def _demo_reply(model: str, messages: list) -> str:
    last_user = next(
        (m.get("content") for m in reversed(messages) if m.get("role") == "user"), ""
    )
    return (
        f"[demo provider] You asked: {str(last_user)[:200]}\n"
        f"Routed through the Gateway Control Center demo provider "
        f"(model: {model}). Everything works!"
    )


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    if not _api_authorized(request):
        return JSONResponse(status_code=401, content={"error": "unauthorized"})
    body = await request.json()
    model = body.get("model", "demo-gpt-fast")
    messages = body.get("messages", [])
    text = _demo_reply(model, messages)
    resp_id = f"chatcmpl-demo-{uuid.uuid4().hex[:12]}"
    usage = {"prompt_tokens": 42, "completion_tokens": len(text.split()), "total_tokens": 0}
    usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]

    if body.get("stream"):
        async def stream():
            chunk = {
                "id": resp_id, "object": "chat.completion.chunk",
                "created": int(time.time()), "model": model,
                "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
            }
            yield f"data: {json.dumps(chunk)}\n\n"
            for word in text.split():
                chunk = {
                    "id": resp_id, "object": "chat.completion.chunk",
                    "created": int(time.time()), "model": model,
                    "choices": [{"index": 0, "delta": {"content": word + " "}, "finish_reason": None}],
                }
                yield f"data: {json.dumps(chunk)}\n\n"
            final = {
                "id": resp_id, "object": "chat.completion.chunk",
                "created": int(time.time()), "model": model,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                "usage": usage,
            }
            yield f"data: {json.dumps(final)}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(stream(), media_type="text/event-stream")

    return {
        "id": resp_id,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": text},
            "finish_reason": "stop",
            "logprobs": None,
        }],
        "usage": usage,
    }


@app.get("/health")
async def health():
    return {"ok": True, "keys": len(_keys), "quota_used": _quota_used}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5900, log_level="warning")
