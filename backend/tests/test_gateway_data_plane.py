"""
Tests for the first-party gateway data plane (/v1).

Covers routing, protocol passthrough and translation (OpenAI ↔ Anthropic,
Responses API), streaming, usage accounting, auth, and error handling.

Upstream providers are simulated with httpx.MockTransport — no real
network calls are made.
"""

import json

import httpx
import pytest
from httpx import AsyncClient, ASGITransport
from httpx._types import AsyncByteStream


class IteratorStream(AsyncByteStream):
    def __init__(self, chunks):
        self._chunks = list(chunks)

    async def __aiter__(self):
        for c in self._chunks:
            yield c

    async def aclose(self):
        pass


def sse_stream(events: list[dict], done: bool = True) -> IteratorStream:
    def gen():
        for evt in events:
            if isinstance(evt, str):
                yield evt.encode()
            else:
                yield f"data: {json.dumps(evt)}\n\n".encode()
        if done:
            yield b"data: [DONE]\n\n"
    return IteratorStream(gen())


# ── Upstream simulators ──────────────────────────────────────────

class FakeUpstream:
    """Routes mock requests per path; records what was received."""

    def __init__(self, protocol: str):
        self.protocol = protocol  # 'openai' | 'anthropic'
        self.calls = []
        self.openai_stream_usage = {"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150}
        self.anthropic_input_tokens = 120
        self.anthropic_output_tokens = 30
        self.fail_status = None  # set to force an error status

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append({"path": request.url.path, "body": json.loads(request.content or b"{}"),
                           "headers": dict(request.headers)})
        if self.fail_status:
            return httpx.Response(self.fail_status, json={"error": {"message": "boom"}})

        path = request.url.path
        body = json.loads(request.content or b"{}")

        if path.endswith("/chat/completions"):
            return self._chat_completions(body)
        if path.endswith("/messages"):
            return self._messages(body)
        return httpx.Response(404, json={"error": "unknown"})

    # ── OpenAI-side behavior ─────────────────────────────────────

    def _chat_completions(self, body):
        model = body.get("model")
        text = f"hello from {model}"
        if body.get("stream"):
            events = [
                {"id": "c1", "object": "chat.completion.chunk", "model": model,
                 "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]},
                {"id": "c1", "object": "chat.completion.chunk", "model": model,
                 "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}]},
                {"id": "c1", "object": "chat.completion.chunk", "model": model,
                 "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                 "usage": self.openai_stream_usage},
            ]
            return httpx.Response(200, stream=sse_stream(events),
                                  headers={"Content-Type": "text/event-stream"})
        return httpx.Response(200, json={
            "id": "c1", "object": "chat.completion", "model": model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": text},
                         "finish_reason": "stop", "logprobs": None}],
            "usage": self.openai_stream_usage,
        })

    # ── Anthropic-side behavior ──────────────────────────────────

    def _messages(self, body):
        model = body.get("model")
        text = f"bonjour from {model}"
        if body.get("stream"):
            events = [
                {"type": "message_start", "message": {
                    "id": "msg_1", "type": "message", "role": "assistant", "model": model,
                    "content": [], "usage": {"input_tokens": self.anthropic_input_tokens, "output_tokens": 0}}},
                {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
                {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text}},
                {"type": "content_block_stop", "index": 0},
                {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                 "usage": {"output_tokens": self.anthropic_output_tokens}},
                {"type": "message_stop"},
            ]
            # Anthropic streams do not use [DONE]
            return httpx.Response(200, stream=sse_stream(events, done=False),
                                  headers={"Content-Type": "text/event-stream"})
        return httpx.Response(200, json={
            "id": "msg_1", "type": "message", "role": "assistant", "model": model,
            "content": [{"type": "text", "text": text}],
            "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": self.anthropic_input_tokens, "output_tokens": self.anthropic_output_tokens},
        })


# ── Fixtures ─────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _configure_for_test(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    monkeypatch.setenv("GCC_DATABASE_PATH", db_path)
    monkeypatch.setenv("GCC_GATEWAY_API_KEY", "")
    monkeypatch.setenv("GCC_DEMO_PROVIDER_ENABLED", "false")

    from app.core.config import get_settings
    get_settings.cache_clear()

    import app.storage.database as db_mod
    db_mod._engine = None
    db_mod._session_factory = None

    import app.core.secrets as secrets_mod
    secrets_mod._store = None

    import app.services.gateway_service as gw_mod
    gw_mod._service = None

    yield

    get_settings.cache_clear()
    db_mod._engine = None
    db_mod._session_factory = None
    secrets_mod._store = None
    gw_mod._service = None


@pytest.fixture
async def app():
    from app.main import create_app
    from app.storage.database import init_database
    app = create_app()
    await init_database()
    return app


@pytest.fixture
async def client(app):
    from app.storage.database import close_database
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    await close_database()


async def setup_provider(protocol: str, provider_id: str, model_ids: list[str], default_model: str) -> None:
    """Create provider + active credential + models for routing tests."""
    import json as _json
    from app.core.secrets import get_secret_store
    from app.storage.database import get_async_session
    from app.storage.repositories import ProviderRepository, CredentialRepository, ModelRepository

    async with get_async_session() as session:
        await ProviderRepository.create(
            session, name=f"Provider {provider_id}", protocol=protocol,
            base_url="http://upstream", auth_type="api-key", provider_id=provider_id,
        )
        ref = get_secret_store().store(f"secret-for-{provider_id}")
        cred = await CredentialRepository.create(
            session, provider_id=provider_id, key_masked="****abcd", secret_ref=ref, source="manual")
        await CredentialRepository.update_fields(session, cred.id, state="active", activated_at="now")
        for model_id in model_ids:
            m = await ModelRepository.create(
                session, provider_id=provider_id, display_name=model_id, model_id=model_id,
                capabilities=_json.dumps(["chat"]))
            await ModelRepository.update_fields(
                session, m.id, enabled=True, is_default=(model_id == default_model))
        await session.commit()


def install_upstream(upstream: FakeUpstream):
    """Point the gateway at the mock transport."""
    from app.services.gateway_service import get_gateway_service
    service = get_gateway_service()

    def client_ctx():
        settings = get_settings()
        return httpx.AsyncClient(
            transport=httpx.MockTransport(upstream),
            timeout=httpx.Timeout(settings.gateway_upstream_timeout, connect=settings.gateway_upstream_connect_timeout),
        )

    service._client_ctx = client_ctx
    return service


from app.core.config import get_settings  # noqa: E402


# ── Models listing ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_models_endpoint(client):
    await setup_provider("openai-completions", "p1", ["m1", "m2"], "m1")
    resp = await client.get("/v1/models")
    assert resp.status_code == 200
    data = resp.json()
    ids = {m["id"] for m in data["data"]}
    assert ids == {"m1", "m2"}


# ── OpenAI passthrough ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_openai_passthrough_nonstream(client):
    await setup_provider("openai-completions", "p1", ["m1"], "m1")
    upstream = FakeUpstream("openai")
    install_upstream(upstream)

    resp = await client.post("/v1/chat/completions", json={
        "model": "m1", "messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 200
    data = resp.json()
    assert data["choices"][0]["message"]["content"] == "hello from m1"
    assert data["usage"]["prompt_tokens"] == 120
    # Upstream received the bearer secret
    assert upstream.calls[0]["headers"]["authorization"] == "Bearer secret-for-p1"


@pytest.mark.asyncio
async def test_openai_passthrough_stream(client):
    await setup_provider("openai-completions", "p1", ["m1"], "m1")
    install_upstream(FakeUpstream("openai"))

    resp = await client.post("/v1/chat/completions", json={
        "model": "m1", "stream": True, "messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 200
    text = resp.text
    assert "hello from m1" in text
    assert "data: [DONE]" in text


# ── Anthropic passthrough ────────────────────────────────────────

@pytest.mark.asyncio
async def test_anthropic_passthrough_nonstream(client):
    await setup_provider("anthropic-messages", "p2", ["claude-1"], "claude-1")
    upstream = FakeUpstream("anthropic")
    install_upstream(upstream)

    resp = await client.post("/v1/messages", json={
        "model": "claude-1", "max_tokens": 100,
        "messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 200
    data = resp.json()
    assert data["content"][0]["text"] == "bonjour from claude-1"
    assert data["usage"]["input_tokens"] == 120
    assert upstream.calls[0]["headers"]["x-api-key"] == "secret-for-p2"


@pytest.mark.asyncio
async def test_anthropic_passthrough_stream(client):
    await setup_provider("anthropic-messages", "p2", ["claude-1"], "claude-1")
    install_upstream(FakeUpstream("anthropic"))

    resp = await client.post("/v1/messages", json={
        "model": "claude-1", "max_tokens": 100, "stream": True,
        "messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 200
    text = resp.text
    assert "message_start" in text
    assert "bonjour from claude-1" in text
    assert "message_stop" in text


# ── OpenAI client → Anthropic provider (translation) ─────────────

@pytest.mark.asyncio
async def test_openai_to_anthropic_translation(client):
    await setup_provider("anthropic-messages", "p2", ["claude-1"], "claude-1")
    upstream = FakeUpstream("anthropic")
    install_upstream(upstream)

    resp = await client.post("/v1/chat/completions", json={
        "model": "claude-1",
        "messages": [
            {"role": "system", "content": "be brief"},
            {"role": "user", "content": "hello"},
        ],
        "max_tokens": 200,
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["object"] == "chat.completion"
    assert data["choices"][0]["message"]["content"] == "bonjour from claude-1"
    assert data["choices"][0]["finish_reason"] == "stop"
    assert data["usage"]["prompt_tokens"] == 120
    # The upstream body is Anthropic-shaped
    sent = upstream.calls[0]["body"]
    assert sent["system"] == "be brief"
    assert sent["max_tokens"] == 200
    assert sent["messages"] == [{"role": "user", "content": "hello"}]
    assert sent["stream"] is False


@pytest.mark.asyncio
async def test_openai_to_anthropic_translation_stream(client):
    await setup_provider("anthropic-messages", "p2", ["claude-1"], "claude-1")
    install_upstream(FakeUpstream("anthropic"))

    resp = await client.post("/v1/chat/completions", json={
        "model": "claude-1", "stream": True,
        "messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 200
    text = resp.text
    assert "chat.completion.chunk" in text
    assert "bonjour from claude-1" in text
    assert "data: [DONE]" in text
    # Usage arrives in the final chunk
    final_line = [l for l in text.splitlines() if '"usage"' in l][-1]
    assert "prompt_tokens" in final_line


# ── Anthropic client → OpenAI provider (translation) ─────────────

@pytest.mark.asyncio
async def test_anthropic_to_openai_translation(client):
    await setup_provider("openai-completions", "p1", ["m1"], "m1")
    upstream = FakeUpstream("openai")
    install_upstream(upstream)

    resp = await client.post("/v1/messages", json={
        "model": "m1", "max_tokens": 100,
        "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["type"] == "message"
    assert data["content"][0]["type"] == "text"
    assert data["content"][0]["text"] == "hello from m1"
    assert data["stop_reason"] == "end_turn"
    assert data["usage"]["input_tokens"] == 120
    sent = upstream.calls[0]["body"]
    assert sent["model"] == "m1"
    assert sent["messages"][-1]["content"] == "hi"


@pytest.mark.asyncio
async def test_anthropic_to_openai_translation_stream(client):
    await setup_provider("openai-completions", "p1", ["m1"], "m1")
    upstream = FakeUpstream("openai")
    install_upstream(upstream)

    resp = await client.post("/v1/messages", json={
        "model": "m1", "max_tokens": 100, "stream": True,
        "messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 200
    text = resp.text
    assert "message_start" in text
    assert "hello from m1" in text
    assert "message_delta" in text
    assert "message_stop" in text
    # upstream was asked for usage in the stream
    assert upstream.calls[0]["body"]["stream_options"]["include_usage"] is True


# ── Responses API ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_responses_api_nonstream(client):
    await setup_provider("openai-completions", "p1", ["m1"], "m1")
    install_upstream(FakeUpstream("openai"))

    resp = await client.post("/v1/responses", json={
        "model": "m1",
        "input": [{"type": "message", "role": "user",
                   "content": [{"type": "input_text", "text": "hi"}]}],
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["object"] == "response"
    assert data["status"] == "completed"
    assert data["output"][0]["type"] == "message"
    assert data["output"][0]["content"][0]["text"] == "hello from m1"
    assert data["usage"]["input_tokens"] == 120


@pytest.mark.asyncio
async def test_responses_api_stream(client):
    await setup_provider("openai-completions", "p1", ["m1"], "m1")
    install_upstream(FakeUpstream("openai"))

    resp = await client.post("/v1/responses", json={
        "model": "m1", "stream": True,
        "input": "tell me a joke",
    })
    assert resp.status_code == 200
    text = resp.text
    for event_type in ("response.created", "response.in_progress",
                       "response.output_text.delta", "response.completed"):
        assert event_type in text
    assert "hello from m1" in text


@pytest.mark.asyncio
async def test_responses_api_via_anthropic(client):
    await setup_provider("anthropic-messages", "p2", ["claude-1"], "claude-1")
    upstream = FakeUpstream("anthropic")
    install_upstream(upstream)

    resp = await client.post("/v1/responses", json={
        "model": "claude-1", "max_output_tokens": 200,
        "instructions": "be helpful",
        "input": [{"type": "message", "role": "user", "content": "hi"}],
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "completed"
    assert data["output"][0]["content"][0]["text"] == "bonjour from claude-1"
    sent = upstream.calls[0]["body"]
    assert sent["system"] == "be helpful"


# ── Routing / auth / errors ──────────────────────────────────────

@pytest.mark.asyncio
async def test_unknown_model_returns_404_with_help(client):
    await setup_provider("openai-completions", "p1", ["m1"], "m1")
    install_upstream(FakeUpstream("openai"))
    resp = await client.post("/v1/chat/completions", json={
        "model": "does-not-exist", "messages": []})
    assert resp.status_code == 404
    assert "m1" in resp.json()["error"]["message"]


@pytest.mark.asyncio
async def test_auto_model_routes_to_default(client):
    await setup_provider("openai-completions", "p1", ["m1", "m2"], "m2")
    upstream = FakeUpstream("openai")
    install_upstream(upstream)
    resp = await client.post("/v1/chat/completions", json={
        "model": "auto", "messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 200
    assert upstream.calls[0]["body"]["model"] == "m2"


@pytest.mark.asyncio
async def test_no_active_credential_error(client):
    await setup_provider("openai-completions", "p1", ["m1"], "m1")
    # Deactivate the credential
    from app.storage.database import get_async_session
    from app.storage.repositories import CredentialRepository
    async with get_async_session() as session:
        cred = await CredentialRepository.get_active(session, "p1")
        await CredentialRepository.update_fields(session, cred.id, state="inactive")
        await session.commit()

    install_upstream(FakeUpstream("openai"))
    resp = await client.post("/v1/chat/completions", json={"model": "m1", "messages": []})
    assert resp.status_code == 404
    assert "credential" in resp.json()["error"]["message"].lower()


@pytest.mark.asyncio
async def test_upstream_error_forwarded(client):
    await setup_provider("openai-completions", "p1", ["m1"], "m1")
    upstream = FakeUpstream("openai")
    upstream.fail_status = 500
    install_upstream(upstream)
    resp = await client.post("/v1/chat/completions", json={"model": "m1", "messages": []})
    assert resp.status_code == 500


@pytest.mark.asyncio
async def test_auth_enforced_when_api_key_set(client, monkeypatch):
    await setup_provider("openai-completions", "p1", ["m1"], "m1")
    install_upstream(FakeUpstream("openai"))
    monkeypatch.setenv("GCC_GATEWAY_API_KEY", "correct-key")
    from app.core.config import get_settings
    get_settings.cache_clear()

    resp = await client.post("/v1/chat/completions", json={"model": "m1", "messages": []},
                             headers={"Authorization": "Bearer wrong"})
    assert resp.status_code == 401
    resp = await client.post("/v1/chat/completions", json={"model": "m1", "messages": []},
                             headers={"Authorization": "Bearer correct-key"})
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_usage_applied_to_credential(client):
    await setup_provider("openai-completions", "p1", ["m1"], "m1")
    install_upstream(FakeUpstream("openai"))

    from app.storage.database import get_async_session
    from app.storage.repositories import CredentialRepository
    async with get_async_session() as session:
        before = await CredentialRepository.get_active(session, "p1")
        assert before.usage_total == 0

    await client.post("/v1/chat/completions", json={
        "model": "m1", "messages": [{"role": "user", "content": "hi"}]})

    async with get_async_session() as session:
        after = await CredentialRepository.get_active(session, "p1")
        assert after.usage_total == 150
        assert after.usage_input == 120
        assert after.usage_output == 30


@pytest.mark.asyncio
async def test_usage_applied_from_stream(client):
    await setup_provider("openai-completions", "p1", ["m1"], "m1")
    install_upstream(FakeUpstream("openai"))

    await client.post("/v1/chat/completions", json={
        "model": "m1", "stream": True, "messages": [{"role": "user", "content": "hi"}]})

    from app.storage.database import get_async_session
    from app.storage.repositories import CredentialRepository
    async with get_async_session() as session:
        after = await CredentialRepository.get_active(session, "p1")
        assert after.usage_total == 150
