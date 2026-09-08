"""
GatewayService — the first-party gateway data plane.

A unified, in-process AI gateway served by the backend under /v1:

    GET  /v1/models              OpenAI-style model list
    POST /v1/chat/completions    OpenAI Chat Completions (stream + non-stream)
    POST /v1/messages            Anthropic Messages API (stream + non-stream)
    POST /v1/responses           OpenAI Responses API (stream + non-stream)

Routing and protocol adaptation:
- The requested model is matched against the model registry
  (models table) and routed to the owning provider.
- The provider's ACTIVE credential is fetched from the SecretStore and
  used to authenticate with the upstream provider.
- Requests are passed through unchanged when the client speaks the
  provider's native protocol, and translated otherwise:
      OpenAI  ↔ Anthropic
      Responses → OpenAI / Anthropic
- Streaming is preserved end-to-end (SSE), with usage extraction.

Token usage is counted per request and applied to the credential's
usage counters; UsageManager periodically snapshots those counters.

Auth model: local single-user trust. Any Bearer token is accepted unless
GCC_GATEWAY_API_KEY is configured, in which case it must match.

No provider is hard-coded here: routing works purely from the provider /
model / credential tables. The data plane never touches dashboard APIs.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Optional

import httpx
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from app.core.config import get_settings
from app.core.logging import get_logger
from app.storage.database import get_async_session
from app.storage.repositories import (
    CredentialRepository,
    ModelRepository,
    ProviderRepository,
)
from app.core.secrets import get_secret_store

logger = get_logger("gateway")

router = APIRouter(tags=["gateway-data-plane"])

# ─────────────────────────────────────────────────────────────────
# Stats / state
# ─────────────────────────────────────────────────────────────────

@dataclass
class GatewayStats:
    """Runtime statistics for the data plane."""
    enabled: bool = True
    started_at: Optional[str] = None
    total_requests: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_errors: int = 0
    last_request_at: Optional[str] = None
    last_error: Optional[str] = None
    requests_by_model: dict = field(default_factory=dict)
    error_rate: float = 0.0


class GatewayService:
    """Owns the gateway data plane business logic."""

    def __init__(self) -> None:
        self._stats = GatewayStats()
        self._stats.started_at = datetime.now(timezone.utc).isoformat()
        self._client: Optional[httpx.AsyncClient] = None
        self._enabled_override: Optional[bool] = None  # runtime toggle

    # ── State ────────────────────────────────────────────────────

    def enabled(self) -> bool:
        if self._enabled_override is not None:
            return self._enabled_override
        return get_settings().gateway_enabled

    async def start(self) -> None:
        """Enable the data plane (in-process)."""
        self._enabled_override = True
        self._stats.enabled = True
        logger.info("Gateway data plane enabled")

    async def stop(self) -> None:
        """Disable the data plane — /v1 returns 503 until re-enabled."""
        self._enabled_override = False
        self._stats.enabled = False
        logger.info("Gateway data plane disabled")

    async def restart(self) -> None:
        await self.stop()
        await self.start()

    def status(self) -> dict:
        s = self._stats
        return {
            "state": "running" if self.enabled() else "stopped",
            "enabled": self.enabled(),
            "started_at": s.started_at,
            "total_requests": s.total_requests,
            "total_input_tokens": s.total_input_tokens,
            "total_output_tokens": s.total_output_tokens,
            "total_errors": s.total_errors,
            "error_rate": s.error_rate,
            "last_request_at": s.last_request_at,
            "last_error": s.last_error,
            "requests_by_model": s.requests_by_model,
            "base_path": get_settings().gateway_base_path,
            "public_base_url": get_settings().gateway_public_base_url,
        }

    def _record(self, model: str, ok: bool) -> None:
        s = self._stats
        s.total_requests += 1
        s.last_request_at = datetime.now(timezone.utc).isoformat()
        s.requests_by_model[model] = s.requests_by_model.get(model, 0) + 1
        s.error_rate = round(s.total_errors / max(s.total_requests, 1), 4)

    def _record_tokens(self, inp: int, out: int) -> None:
        self._stats.total_input_tokens += inp
        self._stats.total_output_tokens += out

    def _record_error(self, message: str) -> None:
        self._stats.total_errors += 1
        self._stats.last_error = message
        logger.warning("Gateway request error: %s", message)

    # ── Auth ─────────────────────────────────────────────────────

    def _check_auth(self, request: Request) -> bool:
        expected = get_settings().gateway_api_key
        if not expected:
            return True  # local single-user trust model
        auth = request.headers.get("authorization", "")
        token = auth.removeprefix("Bearer ").strip() if auth.lower().startswith("bearer ") else auth
        api_key = request.headers.get("x-api-key", "")
        return token == expected or api_key == expected

    # ── Routing helpers ──────────────────────────────────────────

    async def _resolve(self, model: Optional[str]) -> tuple:
        """Resolve (provider_row, model_row, credential_value, upstream_model).

        Returns the model row (for stats) and provider; 'model' may be
        'auto'/'default' in which case the default model is used.
        Raises LookupError with a client-safe message.
        """
        async with get_async_session() as session:
            providers = await ProviderRepository.list_all(session)
            enabled_providers = [p for p in providers if p.enabled]
            if not enabled_providers:
                raise LookupError("No enabled providers are configured")

            models = await ModelRepository.list_all(session)

            enabled_models = [m for m in models if m.enabled and
                              any(p.id == m.provider_id and p.enabled for p in enabled_providers)]
            if not enabled_models:
                raise LookupError("No enabled models are configured")

            if model in (None, "", "auto", "default"):
                default_models = [m for m in enabled_models if m.is_default]
                model_row = default_models[0] if default_models else enabled_models[0]
            else:
                matches = [m for m in enabled_models
                           if m.model_id == model or m.display_name == model]
                if not matches:
                    available = ", ".join(sorted({m.model_id for m in enabled_models}))
                    raise LookupError(
                        f"Unknown model '{model}'. Available models: {available}"
                    )
                model_row = matches[0]

            provider = next(p for p in enabled_providers if p.id == model_row.provider_id)

            credential = await CredentialRepository.get_active(session, provider.id)
            if credential is None or not credential.secret_ref:
                raise LookupError(
                    f"Provider '{provider.name}' has no active credential — "
                    f"activate one in the Credentials page"
                )

        secret = get_secret_store().retrieve(credential.secret_ref)
        if not secret:
            raise LookupError(
                f"Active credential for provider '{provider.name}' has no stored secret"
            )

        return provider, model_row, secret, credential

    async def list_models(self) -> list[dict]:
        """OpenAI-style model list from the registry."""
        now = int(time.time())
        out = []
        async with get_async_session() as session:
            providers = {p.id: p for p in await ProviderRepository.list_all(session)}
            models = await ModelRepository.list_all(session)
            for m in models:
                provider = providers.get(m.provider_id)
                if not provider or not provider.enabled or not m.enabled:
                    continue
                out.append({
                    "id": m.model_id,
                    "object": "model",
                    "created": now,
                    "owned_by": provider.name,
                })
        return out

    # ── Upstream helpers ─────────────────────────────────────────

    def _client_ctx(self) -> httpx.AsyncClient:
        settings = get_settings()
        return httpx.AsyncClient(
            timeout=httpx.Timeout(
                settings.gateway_upstream_timeout,
                connect=settings.gateway_upstream_connect_timeout,
            ),
            follow_redirects=False,
        )

    def _upstream_url(self, provider, endpoint: str) -> str:
        base = (provider.base_url or "").rstrip("/")
        return f"{base}{endpoint}"

    def _openai_headers(self, secret: str, extra: Optional[dict] = None) -> dict:
        headers = {
            "Authorization": f"Bearer {secret}",
            "Content-Type": "application/json",
        }
        if extra:
            headers.update(extra)
        return headers

    def _anthropic_headers(self, secret: str) -> dict:
        return {
            "x-api-key": secret,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _json_error(status: int, message: str, code: str) -> JSONResponse:
        return JSONResponse(
            status_code=status,
            content={"error": {"message": message, "type": code, "code": status}},
        )

    # ── OpenAI passthrough ───────────────────────────────────────

    async def chat_completions(self, request: Request, model: str, body: dict):
        provider, model_row, secret, credential = await self._resolve(model)
        # Replace any routing alias ('auto', display name) with the real id
        body = dict(body)
        body["model"] = model_row.model_id

        if provider.protocol == "openai-completions":
            url = self._upstream_url(provider, "/chat/completions")
            return await self._openai_passthrough(url, secret, body, credential)
        if provider.protocol == "anthropic-messages":
            url = self._upstream_url(provider, "/messages")
            return await self._openai_to_anthropic(url, secret, body, credential)
        raise LookupError(f"Unsupported provider protocol: {provider.protocol}")

    async def _openai_passthrough(self, url, secret, body, credential) -> Response:
        headers = self._openai_headers(secret)
        upstream = self._client_ctx()
        try:
            if body.get("stream"):
                req = upstream.build_request("POST", url, headers=headers, json=body)
                upstream_response = await upstream.send(req, stream=True)
                if upstream_response.status_code >= 400:
                    error_text = await upstream_response.aread()
                    await upstream.aclose()
                    self._record_error(f"upstream {upstream_response.status_code}")
                    return Response(
                        content=error_text,
                        status_code=upstream_response.status_code,
                        media_type="application/json",
                    )
                return StreamingResponse(
                    self._relay_openai_stream(upstream, upstream_response, credential.id),
                    media_type="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
                )
            resp = await upstream.request("POST", url, headers=headers, json=body)
        except httpx.HTTPError as e:
            self._record_error(str(e))
            return self._json_error(502, f"Upstream request failed: {e}", "upstream_error")

        try:
            data = resp.json()
        except Exception:
            data = None
        if resp.status_code >= 400:
            self._record_error(f"upstream {resp.status_code}")
            return Response(content=resp.content, status_code=resp.status_code,
                            media_type="application/json")
        usage = (data or {}).get("usage") or {}
        inp = int(usage.get("prompt_tokens") or 0)
        out = int(usage.get("completion_tokens") or 0)
        self._record_tokens(inp, out)
        await self._apply_credential_usage(credential.id, inp, out)
        return JSONResponse(content=data, status_code=resp.status_code)

    async def _relay_openai_stream(self, upstream, upstream_response, credential_id) -> AsyncGenerator[str, None]:
        """Relay an OpenAI SSE stream verbatim while extracting usage."""
        try:
            async for line in upstream_response.aiter_lines():
                if not line:
                    continue
                yield line + "\n"
                if line.startswith("data:") and "[DONE]" not in line:
                    try:
                        chunk = json.loads(line[5:].strip())
                        usage = chunk.get("usage")
                        if usage:
                            inp = int(usage.get("prompt_tokens") or 0)
                            out = int(usage.get("completion_tokens") or 0)
                            self._record_tokens(inp, out)
                            await self._apply_credential_usage(credential_id, inp, out)
                    except (ValueError, TypeError):
                        pass
        except httpx.HTTPError as e:
            self._record_error(str(e))
            yield f"data: {json.dumps({'error': {'message': str(e)}})}\n\n"
        finally:
            await upstream.aclose()

    # ── Anthropic passthrough ────────────────────────────────────

    async def anthropic_messages(self, request: Request, model: str, body: dict):
        provider, model_row, secret, credential = await self._resolve(model)
        # Replace any routing alias ('auto', display name) with the real id
        body = dict(body)
        body["model"] = model_row.model_id

        if provider.protocol == "anthropic-messages":
            url = self._upstream_url(provider, "/messages")
            return await self._anthropic_passthrough(url, secret, body, credential)
        if provider.protocol == "openai-completions":
            url = self._upstream_url(provider, "/chat/completions")
            return await self._anthropic_to_openai(url, secret, body, credential)
        raise LookupError(f"Unsupported provider protocol: {provider.protocol}")

    async def _anthropic_passthrough(self, url, secret, body, credential) -> Response:
        headers = self._anthropic_headers(secret)
        upstream = self._client_ctx()
        try:
            if body.get("stream"):
                req = upstream.build_request("POST", url, headers=headers, json=body)
                upstream_response = await upstream.send(req, stream=True)
                if upstream_response.status_code >= 400:
                    error_text = await upstream_response.aread()
                    await upstream.aclose()
                    self._record_error(f"upstream {upstream_response.status_code}")
                    return Response(content=error_text, status_code=upstream_response.status_code,
                                    media_type="application/json")
                return StreamingResponse(
                    self._relay_anthropic_stream(upstream, upstream_response, credential.id),
                    media_type="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
                )
            resp = await upstream.request("POST", url, headers=headers, json=body)
        except httpx.HTTPError as e:
            self._record_error(str(e))
            return self._json_error(502, f"Upstream request failed: {e}", "upstream_error")

        if resp.status_code >= 400:
            self._record_error(f"upstream {resp.status_code}")
            return Response(content=resp.content, status_code=resp.status_code,
                            media_type="application/json")
        try:
            data = resp.json()
        except Exception:
            data = None
        usage = (data or {}).get("usage") or {}
        inp = int(usage.get("input_tokens") or 0)
        out = int(usage.get("output_tokens") or 0)
        self._record_tokens(inp, out)
        await self._apply_credential_usage(credential.id, inp, out)
        return JSONResponse(content=data, status_code=resp.status_code)

    async def _relay_anthropic_stream(self, upstream, upstream_response, credential_id) -> AsyncGenerator[str, None]:
        """Relay an Anthropic SSE stream verbatim while extracting usage."""
        try:
            async for line in upstream_response.aiter_lines():
                if not line:
                    continue
                yield line + "\n"
                if line.startswith("data:"):
                    try:
                        evt = json.loads(line[5:].strip())
                    except ValueError:
                        continue
                    etype = evt.get("type")
                    if etype == "message_start":
                        usage = (evt.get("message") or {}).get("usage") or {}
                        inp = int(usage.get("input_tokens") or 0)
                        self._record_tokens(inp, 0)
                        await self._apply_credential_usage(credential_id, inp, 0)
                    elif etype == "message_delta":
                        out = int((evt.get("usage") or {}).get("output_tokens") or 0)
                        self._record_tokens(0, out)
                        await self._apply_credential_usage(credential_id, 0, out)
        except httpx.HTTPError as e:
            self._record_error(str(e))
            yield f"event: error\ndata: {json.dumps({'error': {'message': str(e)}})}\n\n"
        finally:
            await upstream.aclose()

    # ── OpenAI → Anthropic translation ───────────────────────────

    async def _openai_to_anthropic(self, url, secret, body, credential) -> Response:
        messages = body.get("messages") or []
        system = "\n\n".join(
            str(m.get("content")) for m in messages if m.get("role") == "system"
        ) or None
        anthropic_messages = []
        for m in messages:
            if m.get("role") in ("user", "assistant"):
                anthropic_messages.append({
                    "role": m["role"],
                    "content": m.get("content") or "",
                })

        anthropic_body: dict[str, Any] = {
            "model": body.get("model"),
            "max_tokens": body.get("max_tokens") or body.get("max_completion_tokens")
                          or get_settings().gateway_default_max_tokens,
            "messages": anthropic_messages,
        }
        if system:
            anthropic_body["system"] = system
        for key in ("temperature", "top_p"):
            if body.get(key) is not None:
                anthropic_body[key] = body[key]
        if body.get("tools"):
            anthropic_body["tools"] = [
                {
                    "name": t.get("function", {}).get("name", "tool"),
                    "description": t.get("function", {}).get("description", ""),
                    "input_schema": t.get("function", {}).get("parameters") or {"type": "object"},
                }
                for t in body["tools"] if t.get("type") == "function"
            ]
        anthropic_body["stream"] = bool(body.get("stream"))

        headers = self._anthropic_headers(secret)
        upstream = self._client_ctx()
        try:
            if anthropic_body["stream"]:
                req = upstream.build_request("POST", url, headers=headers, json=anthropic_body)
                upstream_response = await upstream.send(req, stream=True)
                if upstream_response.status_code >= 400:
                    error_text = await upstream_response.aread()
                    await upstream.aclose()
                    self._record_error(f"upstream {upstream_response.status_code}")
                    return Response(content=error_text, status_code=upstream_response.status_code,
                                    media_type="application/json")
                return StreamingResponse(
                    self._translate_anthropic_stream_to_openai(upstream, upstream_response, credential.id),
                    media_type="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
                )
            resp = await upstream.request("POST", url, headers=headers, json=anthropic_body)
        except httpx.HTTPError as e:
            self._record_error(str(e))
            return self._json_error(502, f"Upstream request failed: {e}", "upstream_error")

        if resp.status_code >= 400:
            self._record_error(f"upstream {resp.status_code}")
            return Response(content=resp.content, status_code=resp.status_code,
                            media_type="application/json")
        data = resp.json()
        usage = data.get("usage") or {}
        inp = int(usage.get("input_tokens") or 0)
        out = int(usage.get("output_tokens") or 0)
        self._record_tokens(inp, out)
        await self._apply_credential_usage(credential.id, inp, out)

        text = "".join(
            b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"
        )
        openai_response = {
            "id": data.get("id"),
            "object": "chat.completion",
            "created": int(time.time()),
            "model": body.get("model"),
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": self._map_stop_reason(data.get("stop_reason")),
                "logprobs": None,
            }],
            "usage": {
                "prompt_tokens": inp,
                "completion_tokens": out,
                "total_tokens": inp + out,
            },
        }
        return JSONResponse(content=openai_response)

    @staticmethod
    def _map_stop_reason(reason: Optional[str]) -> Optional[str]:
        return {
            "end_turn": "stop",
            "max_tokens": "length",
            "stop_sequence": "stop",
            "tool_use": "tool_calls",
        }.get(reason or "", "stop")

    async def _translate_anthropic_stream_to_openai(self, upstream, upstream_response, credential_id) -> AsyncGenerator[str, None]:
        created = int(time.time())
        model_name = ""
        try:
            yield f"data: {json.dumps({'id': 'chatcmpl-gcc', 'object': 'chat.completion.chunk', 'created': created, 'model': model_name, 'choices': [{'index': 0, 'delta': {'role': 'assistant'}, 'finish_reason': None}]})}\n\n"
            input_tokens = 0
            async for line in upstream_response.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                try:
                    evt = json.loads(line[5:].strip())
                except ValueError:
                    continue
                etype = evt.get("type")
                if etype == "message_start":
                    model_name = (evt.get("message") or {}).get("model", "")
                    input_tokens = int(((evt.get("message") or {}).get("usage") or {}).get("input_tokens") or 0)
                elif etype == "content_block_delta":
                    delta = evt.get("delta") or {}
                    if delta.get("type") == "text_delta":
                        chunk = {"id": "chatcmpl-gcc", "object": "chat.completion.chunk", "created": created,
                                 "model": model_name,
                                 "choices": [{"index": 0, "delta": {"content": delta.get("text", "")}, "finish_reason": None}]}
                        yield f"data: {json.dumps(chunk)}\n\n"
                elif etype == "message_delta":
                    usage = evt.get("usage") or {}
                    output_tokens = int(usage.get("output_tokens") or 0)
                    self._record_tokens(0, output_tokens)
                    await self._apply_credential_usage(credential_id, 0, output_tokens)
                    reason = self._map_stop_reason((evt.get("delta") or {}).get("stop_reason"))
                    chunk = {"id": "chatcmpl-gcc", "object": "chat.completion.chunk", "created": created,
                             "model": model_name,
                             "choices": [{"index": 0, "delta": {}, "finish_reason": reason}],
                             "usage": {"prompt_tokens": input_tokens, "completion_tokens": output_tokens,
                                       "total_tokens": input_tokens + output_tokens}}
                    yield f"data: {json.dumps(chunk)}\n\n"
            yield "data: [DONE]\n\n"
        except httpx.HTTPError as e:
            self._record_error(str(e))
            yield f"data: {json.dumps({'error': {'message': str(e)}})}\n\n"
        finally:
            await upstream.aclose()

    # ── Anthropic → OpenAI translation ───────────────────────────

    async def _anthropic_to_openai(self, url, secret, body, credential) -> Response:
        system_blocks = body.get("system")
        system_text = ""
        if isinstance(system_blocks, str):
            system_text = system_blocks
        elif isinstance(system_blocks, list):
            system_text = "\n".join(b.get("text", "") for b in system_blocks if b.get("type") == "text")

        openai_messages = []
        if system_text:
            openai_messages.append({"role": "system", "content": system_text})
        for m in body.get("messages") or []:
            role = m.get("role")
            content = m.get("content")
            if isinstance(content, str):
                openai_messages.append({"role": role, "content": content})
                continue
            if role == "assistant":
                text_parts = [b.get("text", "") for b in content if b.get("type") == "text"]
                msg = {"role": "assistant", "content": "\n".join(text_parts) or None}
                tool_uses = [b for b in content if b.get("type") == "tool_use"]
                if tool_uses:
                    msg["tool_calls"] = [
                        {"id": b.get("id", f"call_{i}"), "type": "function",
                         "function": {"name": b.get("name", ""), "arguments": json.dumps(b.get("input") or {})}}
                        for i, b in enumerate(tool_uses)
                    ]
                openai_messages.append(msg)
            elif role == "user":
                text_parts = [b.get("text", "") for b in content if b.get("type") == "text"]
                openai_messages.append({"role": "user", "content": "\n".join(text_parts)})
                for b in content:
                    if b.get("type") == "tool_result":
                        openai_messages.append({
                            "role": "tool",
                            "tool_call_id": b.get("tool_use_id", ""),
                            "content": b.get("content") if isinstance(b.get("content"), str) else json.dumps(b.get("content") or {}),
                        })

        openai_body: dict[str, Any] = {
            "model": body.get("model"),
            "messages": openai_messages,
            "max_tokens": body.get("max_tokens") or get_settings().gateway_default_max_tokens,
        }
        for key in ("temperature", "top_p"):
            if body.get(key) is not None:
                openai_body[key] = body[key]
        if body.get("tools"):
            openai_body["tools"] = [
                {"type": "function",
                 "function": {"name": t.get("name", "tool"), "description": t.get("description", ""),
                              "parameters": t.get("input_schema") or {"type": "object"}}}
                for t in body["tools"]
            ]
        openai_body["stream"] = bool(body.get("stream"))
        if openai_body["stream"]:
            openai_body["stream_options"] = {"include_usage": True}

        headers = self._openai_headers(secret)
        upstream = self._client_ctx()
        try:
            if openai_body["stream"]:
                req = upstream.build_request("POST", url, headers=headers, json=openai_body)
                upstream_response = await upstream.send(req, stream=True)
                if upstream_response.status_code >= 400:
                    error_text = await upstream_response.aread()
                    await upstream.aclose()
                    self._record_error(f"upstream {upstream_response.status_code}")
                    return Response(content=error_text, status_code=upstream_response.status_code,
                                    media_type="application/json")
                return StreamingResponse(
                    self._translate_openai_stream_to_anthropic(upstream, upstream_response, credential.id, body.get("model")),
                    media_type="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
                )
            resp = await upstream.request("POST", url, headers=headers, json=openai_body)
        except httpx.HTTPError as e:
            self._record_error(str(e))
            return self._json_error(502, f"Upstream request failed: {e}", "upstream_error")

        if resp.status_code >= 400:
            self._record_error(f"upstream {resp.status_code}")
            return Response(content=resp.content, status_code=resp.status_code,
                            media_type="application/json")
        data = resp.json()
        usage = data.get("usage") or {}
        inp = int(usage.get("prompt_tokens") or 0)
        out = int(usage.get("completion_tokens") or 0)
        self._record_tokens(inp, out)
        await self._apply_credential_usage(credential.id, inp, out)

        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        content_blocks = []
        if message.get("content"):
            content_blocks.append({"type": "text", "text": message["content"]})
        for tc in message.get("tool_calls") or []:
            try:
                arguments = json.loads(tc.get("function", {}).get("arguments") or "{}")
            except ValueError:
                arguments = {}
            content_blocks.append({
                "type": "tool_use", "id": tc.get("id", f"call_{len(content_blocks)}"),
                "name": tc.get("function", {}).get("name", ""), "input": arguments,
            })
        anthropic_response = {
            "id": data.get("id"),
            "type": "message",
            "role": "assistant",
            "model": body.get("model"),
            "content": content_blocks,
            "stop_reason": self._map_finish_reason(choice.get("finish_reason")),
            "stop_sequence": None,
            "usage": {"input_tokens": inp, "output_tokens": out},
        }
        return JSONResponse(content=anthropic_response)

    @staticmethod
    def _map_finish_reason(reason: Optional[str]) -> Optional[str]:
        return {
            "stop": "end_turn",
            "length": "max_tokens",
            "tool_calls": "tool_use",
            "content_filter": "end_turn",
        }.get(reason or "", "end_turn")

    async def _translate_openai_stream_to_anthropic(self, upstream, upstream_response, credential_id, model_name) -> AsyncGenerator[str, None]:
        msg_id = f"msg_{uuid.uuid4().hex[:24]}"
        import itertools
        seq = itertools.count()

        def sse(event: str, payload: dict) -> str:
            return f"event: {event}\ndata: {json.dumps(payload)}\n\n"

        try:
            yield sse("message_start", {
                "type": "message_start",
                "message": {"id": msg_id, "type": "message", "role": "assistant",
                            "content": [], "model": model_name,
                            "stop_reason": None, "stop_sequence": None,
                            "usage": {"input_tokens": 0, "output_tokens": 0}},
            })
            yield sse("content_block_start", {
                "type": "content_block_start", "index": 0,
                "content_block": {"type": "text", "text": ""},
            })
            finish_reason = None
            input_tokens = 0
            output_tokens = 0
            tool_calls: dict[int, dict] = {}
            async for line in upstream_response.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                raw = line[5:].strip()
                if raw == "[DONE]":
                    continue
                try:
                    chunk = json.loads(raw)
                except ValueError:
                    continue
                if chunk.get("usage"):
                    u = chunk["usage"]
                    input_tokens = int(u.get("prompt_tokens") or 0)
                    output_tokens = int(u.get("completion_tokens") or 0)
                for choice in chunk.get("choices") or []:
                    delta = choice.get("delta") or {}
                    if delta.get("content"):
                        yield sse("content_block_delta", {
                            "type": "content_block_delta", "index": 0,
                            "delta": {"type": "text_delta", "text": delta["content"]},
                        })
                    for tc in delta.get("tool_calls") or []:
                        idx = tc.get("index", 0)
                        slot = tool_calls.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                        if tc.get("id"):
                            slot["id"] = tc["id"]
                        if tc.get("function", {}).get("name"):
                            slot["name"] = tc["function"]["name"]
                        if tc.get("function", {}).get("arguments"):
                            slot["arguments"] += tc["function"]["arguments"]
                    if choice.get("finish_reason"):
                        finish_reason = choice["finish_reason"]

            yield sse("content_block_stop", {"type": "content_block_stop", "index": 0})
            for idx in sorted(tool_calls):
                slot = tool_calls[idx]
                try:
                    arguments = json.loads(slot["arguments"] or "{}")
                except ValueError:
                    arguments = {}
                yield sse("content_block_start", {
                    "type": "content_block_start", "index": idx + 1,
                    "content_block": {"type": "tool_use", "id": slot["id"] or f"toolu_{idx}",
                                      "name": slot["name"], "input": {}},
                })
                yield sse("content_block_delta", {
                    "type": "content_block_delta", "index": idx + 1,
                    "delta": {"type": "input_json_delta", "partial_json": json.dumps(arguments)},
                })
                yield sse("content_block_stop", {"type": "content_block_stop", "index": idx + 1})

            self._record_tokens(input_tokens, output_tokens)
            await self._apply_credential_usage(credential_id, input_tokens, output_tokens)
            yield sse("message_delta", {
                "type": "message_delta",
                "delta": {"stop_reason": self._map_finish_reason(finish_reason), "stop_sequence": None},
                "usage": {"output_tokens": output_tokens},
            })
            yield sse("message_stop", {"type": "message_stop"})
        except httpx.HTTPError as e:
            self._record_error(str(e))
            yield sse("error", {"type": "error", "error": {"type": "api_error", "message": str(e)}})
        finally:
            await upstream.aclose()

    # ── Responses API ────────────────────────────────────────────

    async def responses(self, request: Request, body: dict):
        model = body.get("model")
        provider, model_row, secret, credential = await self._resolve(model)
        target_model = model_row.model_id

        if provider.protocol == "openai-completions":
            return await self._responses_via_openai(provider, secret, credential, body, target_model)
        if provider.protocol == "anthropic-messages":
            return await self._responses_via_anthropic(provider, secret, credential, body, target_model)
        raise LookupError(f"Unsupported provider protocol: {provider.protocol}")

    @staticmethod
    def _responses_input_to_messages(body: dict) -> tuple[list[dict], str]:
        instructions = str(body.get("instructions") or "")
        messages: list[dict] = []
        if instructions:
            messages.append({"role": "system", "content": instructions})
        inp = body.get("input")
        if isinstance(inp, str):
            messages.append({"role": "user", "content": inp})
        elif isinstance(inp, list):
            for item in inp:
                itype = item.get("type")
                if itype == "message":
                    content = item.get("content") or []
                    if isinstance(content, str):
                        text = content
                    else:
                        text = "".join(
                            c.get("text", "") for c in content
                            if isinstance(c, dict) and c.get("type") in ("input_text", "output_text", "text")
                        )
                    messages.append({"role": item.get("role", "user"), "content": text})
        return messages, instructions

    async def _responses_via_openai(self, provider, secret, credential, body, target_model) -> Response:
        messages, _ = self._responses_input_to_messages(body)
        openai_body: dict[str, Any] = {
            "model": target_model,
            "messages": messages,
            "max_tokens": body.get("max_output_tokens") or get_settings().gateway_default_max_tokens,
        }
        for key in ("temperature", "top_p"):
            if body.get(key) is not None:
                openai_body[key] = body[key]
        if body.get("tools"):
            openai_body["tools"] = body["tools"]
        openai_body["stream"] = bool(body.get("stream"))
        if openai_body["stream"]:
            openai_body["stream_options"] = {"include_usage": True}

        url = self._upstream_url(provider, "/chat/completions")
        headers = self._openai_headers(secret)
        upstream = self._client_ctx()
        try:
            if openai_body["stream"]:
                req = upstream.build_request("POST", url, headers=headers, json=openai_body)
                upstream_response = await upstream.send(req, stream=True)
                if upstream_response.status_code >= 400:
                    error_text = await upstream_response.aread()
                    await upstream.aclose()
                    self._record_error(f"upstream {upstream_response.status_code}")
                    return Response(content=error_text, status_code=upstream_response.status_code,
                                    media_type="application/json")
                return StreamingResponse(
                    self._responses_stream_from_openai(upstream, upstream_response, credential.id, target_model),
                    media_type="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
                )
            resp = await upstream.request("POST", url, headers=headers, json=openai_body)
        except httpx.HTTPError as e:
            self._record_error(str(e))
            return self._json_error(502, f"Upstream request failed: {e}", "upstream_error")

        if resp.status_code >= 400:
            self._record_error(f"upstream {resp.status_code}")
            return Response(content=resp.content, status_code=resp.status_code,
                            media_type="application/json")
        data = resp.json()
        usage = data.get("usage") or {}
        inp = int(usage.get("prompt_tokens") or 0)
        out = int(usage.get("completion_tokens") or 0)
        self._record_tokens(inp, out)
        await self._apply_credential_usage(credential.id, inp, out)

        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        output_items = []
        text_content = [{"type": "output_text", "text": message.get("content") or "", "annotations": []}]
        output_items.append({
            "id": f"msg_{uuid.uuid4().hex[:24]}", "type": "message", "status": "completed",
            "role": "assistant", "content": text_content,
        })
        for tc in message.get("tool_calls") or []:
            output_items.append({
                "id": tc.get("id"), "type": "function_call", "status": "completed",
                "name": tc.get("function", {}).get("name"),
                "arguments": tc.get("function", {}).get("arguments"),
                "call_id": tc.get("id"),
            })
        return JSONResponse(content=self._build_responses_object(
            resp_id=data.get("id") or f"resp_{uuid.uuid4().hex[:24]}",
            model=target_model, output_items=output_items, inp=inp, out=out,
            text=message.get("content") or "",
        ))

    async def _responses_stream_from_openai(self, upstream, upstream_response, credential_id, model_name) -> AsyncGenerator[str, None]:
        resp_id = f"resp_{uuid.uuid4().hex[:24]}"
        msg_id = f"msg_{uuid.uuid4().hex[:24]}"
        seq = [0]

        def evt(payload: dict) -> str:
            payload["sequence_number"] = seq[0]
            seq[0] += 1
            return f"data: {json.dumps(payload)}\n\n"

        try:
            yield evt({"type": "response.created", "response": self._responses_skeleton(resp_id, model_name, "in_progress")})
            yield evt({"type": "response.in_progress", "response": self._responses_skeleton(resp_id, model_name, "in_progress")})
            yield evt({"type": "response.output_item.added", "output_index": 0, "item": {
                "id": msg_id, "type": "message", "status": "in_progress", "role": "assistant", "content": []}})
            yield evt({"type": "response.content_part.added", "item_id": msg_id, "output_index": 0,
                       "content_index": 0, "part": {"type": "output_text", "text": "", "annotations": []}})

            text_parts: list[str] = []
            inp = 0
            out = 0
            finish_reason = None
            async for line in upstream_response.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                raw = line[5:].strip()
                if raw == "[DONE]":
                    continue
                try:
                    chunk = json.loads(raw)
                except ValueError:
                    continue
                if chunk.get("usage"):
                    u = chunk["usage"]
                    inp = int(u.get("prompt_tokens") or 0)
                    out = int(u.get("completion_tokens") or 0)
                for choice in chunk.get("choices") or []:
                    delta = choice.get("delta") or {}
                    if delta.get("content"):
                        text_parts.append(delta["content"])
                        yield evt({"type": "response.output_text.delta", "item_id": msg_id,
                                   "output_index": 0, "content_index": 0, "delta": delta["content"]})
                    if choice.get("finish_reason"):
                        finish_reason = choice["finish_reason"]

            self._record_tokens(inp, out)
            await self._apply_credential_usage(credential_id, inp, out)
            full_text = "".join(text_parts)
            yield evt({"type": "response.output_text.done", "item_id": msg_id, "output_index": 0,
                       "content_index": 0, "text": full_text})
            yield evt({"type": "response.content_part.done", "item_id": msg_id, "output_index": 0,
                       "content_index": 0, "part": {"type": "output_text", "text": full_text, "annotations": []}})
            item = {"id": msg_id, "type": "message", "status": "completed", "role": "assistant",
                    "content": [{"type": "output_text", "text": full_text, "annotations": []}]}
            yield evt({"type": "response.output_item.done", "output_index": 0, "item": item})
            response = self._responses_skeleton(resp_id, model_name, "completed")
            response["output"] = [item]
            response["usage"] = {"input_tokens": inp, "output_tokens": out, "total_tokens": inp + out}
            yield evt({"type": "response.completed", "response": response})
        except httpx.HTTPError as e:
            self._record_error(str(e))
            yield evt({"type": "response.failed", "response": self._responses_skeleton(resp_id, model_name, "failed")})
        finally:
            await upstream.aclose()

    async def _responses_via_anthropic(self, provider, secret, credential, body, target_model) -> Response:
        messages, instructions = self._responses_input_to_messages(body)
        system = instructions or None
        anthropic_messages = [{"role": m["role"], "content": m["content"]}
                              for m in messages if m.get("role") in ("user", "assistant")]
        anthropic_body: dict[str, Any] = {
            "model": target_model,
            "max_tokens": body.get("max_output_tokens") or get_settings().gateway_default_max_tokens,
            "messages": anthropic_messages,
        }
        if system:
            anthropic_body["system"] = system
        if body.get("temperature") is not None:
            anthropic_body["temperature"] = body["temperature"]
        if body.get("tools"):
            anthropic_body["tools"] = [
                {"name": t.get("function", {}).get("name", "tool"),
                 "description": t.get("function", {}).get("description", ""),
                 "input_schema": t.get("function", {}).get("parameters") or {"type": "object"}}
                for t in body["tools"] if t.get("type") == "function"
            ]
        anthropic_body["stream"] = bool(body.get("stream"))

        url = self._upstream_url(provider, "/messages")
        headers = self._anthropic_headers(secret)
        upstream = self._client_ctx()
        try:
            if anthropic_body["stream"]:
                req = upstream.build_request("POST", url, headers=headers, json=anthropic_body)
                upstream_response = await upstream.send(req, stream=True)
                if upstream_response.status_code >= 400:
                    error_text = await upstream_response.aread()
                    await upstream.aclose()
                    self._record_error(f"upstream {upstream_response.status_code}")
                    return Response(content=error_text, status_code=upstream_response.status_code,
                                    media_type="application/json")
                return StreamingResponse(
                    self._responses_stream_from_anthropic(upstream, upstream_response, credential.id, target_model),
                    media_type="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
                )
            resp = await upstream.request("POST", url, headers=headers, json=anthropic_body)
        except httpx.HTTPError as e:
            self._record_error(str(e))
            return self._json_error(502, f"Upstream request failed: {e}", "upstream_error")

        if resp.status_code >= 400:
            self._record_error(f"upstream {resp.status_code}")
            return Response(content=resp.content, status_code=resp.status_code,
                            media_type="application/json")
        data = resp.json()
        usage = data.get("usage") or {}
        inp = int(usage.get("input_tokens") or 0)
        out = int(usage.get("output_tokens") or 0)
        self._record_tokens(inp, out)
        await self._apply_credential_usage(credential.id, inp, out)

        text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        output_items = [{
            "id": f"msg_{uuid.uuid4().hex[:24]}", "type": "message", "status": "completed",
            "role": "assistant",
            "content": [{"type": "output_text", "text": text, "annotations": []}],
        }]
        for block in data.get("content", []):
            if block.get("type") == "tool_use":
                output_items.append({
                    "id": block.get("id"), "type": "function_call", "status": "completed",
                    "name": block.get("name"), "arguments": json.dumps(block.get("input") or {}),
                    "call_id": block.get("id"),
                })
        return JSONResponse(content=self._build_responses_object(
            resp_id=data.get("id") or f"resp_{uuid.uuid4().hex[:24]}",
            model=target_model, output_items=output_items, inp=inp, out=out, text=text,
        ))

    async def _responses_stream_from_anthropic(self, upstream, upstream_response, credential_id, model_name) -> AsyncGenerator[str, None]:
        resp_id = f"resp_{uuid.uuid4().hex[:24]}"
        msg_id = f"msg_{uuid.uuid4().hex[:24]}"
        seq = [0]

        def evt(payload: dict) -> str:
            payload["sequence_number"] = seq[0]
            seq[0] += 1
            return f"data: {json.dumps(payload)}\n\n"

        try:
            yield evt({"type": "response.created", "response": self._responses_skeleton(resp_id, model_name, "in_progress")})
            yield evt({"type": "response.in_progress", "response": self._responses_skeleton(resp_id, model_name, "in_progress")})
            yield evt({"type": "response.output_item.added", "output_index": 0, "item": {
                "id": msg_id, "type": "message", "status": "in_progress", "role": "assistant", "content": []}})
            yield evt({"type": "response.content_part.added", "item_id": msg_id, "output_index": 0,
                       "content_index": 0, "part": {"type": "output_text", "text": "", "annotations": []}})

            text_parts: list[str] = []
            inp = 0
            out = 0
            stop_reason = None
            async for line in upstream_response.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                try:
                    ev = json.loads(line[5:].strip())
                except ValueError:
                    continue
                etype = ev.get("type")
                if etype == "message_start":
                    inp = int(((ev.get("message") or {}).get("usage") or {}).get("input_tokens") or 0)
                elif etype == "content_block_delta":
                    delta = ev.get("delta") or {}
                    if delta.get("type") == "text_delta" and delta.get("text"):
                        text_parts.append(delta["text"])
                        yield evt({"type": "response.output_text.delta", "item_id": msg_id,
                                   "output_index": 0, "content_index": 0, "delta": delta["text"]})
                elif etype == "message_delta":
                    out = int((ev.get("usage") or {}).get("output_tokens") or 0)
                    stop_reason = (ev.get("delta") or {}).get("stop_reason")

            self._record_tokens(inp, out)
            await self._apply_credential_usage(credential_id, inp, out)
            full_text = "".join(text_parts)
            yield evt({"type": "response.output_text.done", "item_id": msg_id, "output_index": 0,
                       "content_index": 0, "text": full_text})
            yield evt({"type": "response.content_part.done", "item_id": msg_id, "output_index": 0,
                       "content_index": 0, "part": {"type": "output_text", "text": full_text, "annotations": []}})
            item = {"id": msg_id, "type": "message", "status": "completed", "role": "assistant",
                    "content": [{"type": "output_text", "text": full_text, "annotations": []}]}
            yield evt({"type": "response.output_item.done", "output_index": 0, "item": item})
            response = self._responses_skeleton(resp_id, model_name, "completed")
            response["output"] = [item]
            response["usage"] = {"input_tokens": inp, "output_tokens": out, "total_tokens": inp + out}
            yield evt({"type": "response.completed", "response": response})
        except httpx.HTTPError as e:
            self._record_error(str(e))
            yield evt({"type": "response.failed", "response": self._responses_skeleton(resp_id, model_name, "failed")})
        finally:
            await upstream.aclose()

    @staticmethod
    def _responses_skeleton(resp_id: str, model: str, status: str) -> dict:
        return {
            "id": resp_id, "object": "response", "created_at": int(time.time()),
            "status": status, "model": model, "output": [],
            "parallel_tool_calls": True,
            "previous_response_id": None,
        }

    @staticmethod
    def _build_responses_object(resp_id, model, output_items, inp, out, text) -> dict:
        return {
            "id": resp_id, "object": "response", "created_at": int(time.time()),
            "status": "completed", "model": model, "output": output_items,
            "output_text": text,
            "usage": {"input_tokens": inp, "output_tokens": out, "total_tokens": inp + out},
            "parallel_tool_calls": True, "previous_response_id": None,
        }

    # ── Credential usage accounting ──────────────────────────────

    async def _apply_credential_usage(self, credential_id: Optional[str], inp: int, out: int) -> None:
        if not credential_id or (not inp and not out):
            return
        try:
            from sqlalchemy import update
            from app.storage.models import CredentialRow, _utcnow
            async with get_async_session() as session:
                await session.execute(
                    update(CredentialRow)
                    .where(CredentialRow.id == credential_id)
                    .values(
                        usage_input=CredentialRow.usage_input + inp,
                        usage_output=CredentialRow.usage_output + out,
                        usage_total=CredentialRow.usage_total + inp + out,
                        updated_at=_utcnow(),
                    )
                )
                await session.commit()
        except Exception as e:
            logger.warning("Failed to update credential usage: %s", e)


# ─────────────────────────────────────────────────────────────────
# Routes (mounted at /v1)
# ─────────────────────────────────────────────────────────────────

_service: Optional[GatewayService] = None


def get_gateway_service() -> GatewayService:
    global _service
    if _service is None:
        _service = GatewayService()
    return _service


def set_gateway_service(service: GatewayService) -> None:
    global _service
    _service = service


@router.get("/models")
async def v1_models():
    service = get_gateway_service()
    if not service.enabled():
        return service._json_error(503, "Gateway data plane is stopped", "gateway_stopped")
    models = await service.list_models()
    return {"object": "list", "data": models}


@router.get("/models/{model_id:path}")
async def v1_model_detail(model_id: str):
    service = get_gateway_service()
    if not service.enabled():
        return service._json_error(503, "Gateway data plane is stopped", "gateway_stopped")
    models = await service.list_models()
    match = next((m for m in models if m["id"] == model_id), None)
    if not match:
        return service._json_error(404, f"Model '{model_id}' not found", "model_not_found")
    return match


@router.post("/chat/completions")
async def v1_chat_completions(request: Request):
    service = get_gateway_service()
    if not service.enabled():
        return service._json_error(503, "Gateway data plane is stopped", "gateway_stopped")
    if not service._check_auth(request):
        return service._json_error(401, "Invalid gateway API key", "unauthorized")
    try:
        body = await request.json()
    except Exception:
        return service._json_error(400, "Invalid JSON body", "invalid_request")
    model = body.get("model") or "auto"
    service._record(model, ok=True)
    try:
        return await service.chat_completions(request, model, body)
    except LookupError as e:
        return service._json_error(404, str(e), "model_not_found")
    except Exception as e:
        service._record_error(str(e))
        logger.exception("chat_completions failed")
        return service._json_error(500, f"Gateway error: {e}", "gateway_error")


@router.post("/messages")
async def v1_messages(request: Request):
    service = get_gateway_service()
    if not service.enabled():
        return service._json_error(503, "Gateway data plane is stopped", "gateway_stopped")
    if not service._check_auth(request):
        return service._json_error(401, "Invalid gateway API key", "unauthorized")
    try:
        body = await request.json()
    except Exception:
        return service._json_error(400, "Invalid JSON body", "invalid_request")
    model = body.get("model") or "auto"
    service._record(model, ok=True)
    try:
        return await service.anthropic_messages(request, model, body)
    except LookupError as e:
        return service._json_error(404, str(e), "model_not_found")
    except Exception as e:
        service._record_error(str(e))
        logger.exception("messages failed")
        return service._json_error(500, f"Gateway error: {e}", "gateway_error")


@router.post("/responses")
async def v1_responses(request: Request):
    service = get_gateway_service()
    if not service.enabled():
        return service._json_error(503, "Gateway data plane is stopped", "gateway_stopped")
    if not service._check_auth(request):
        return service._json_error(401, "Invalid gateway API key", "unauthorized")
    try:
        body = await request.json()
    except Exception:
        return service._json_error(400, "Invalid JSON body", "invalid_request")
    model = body.get("model") or "auto"
    service._record(model, ok=True)
    try:
        return await service.responses(request, body)
    except LookupError as e:
        return service._json_error(404, str(e), "model_not_found")
    except Exception as e:
        service._record_error(str(e))
        logger.exception("responses failed")
        return service._json_error(500, f"Gateway error: {e}", "gateway_error")
