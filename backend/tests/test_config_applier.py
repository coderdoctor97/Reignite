"""
Tests for the ConfigApplier — applying the gateway endpoint configuration
to installed apps and CLI agents (Claude Code, Claude Desktop, Codex,
Grok Build, Cline, Roo Code).

Uses a fake HOME directory; never touches the real user config.
"""

import json
import tomllib
from pathlib import Path

import pytest
from httpx import AsyncClient, ASGITransport


@pytest.fixture(autouse=True)
def _configure_for_test(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("GCC_DATABASE_PATH", db_path)
    monkeypatch.setenv("GCC_DEMO_PROVIDER_ENABLED", "false")
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("GCC_GATEWAY_PUBLIC_BASE_URL", "http://localhost:8400")
    monkeypatch.setenv("GCC_GATEWAY_AUTH_TOKEN", "gcc-local")
    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))

    from app.core.config import get_settings
    get_settings.cache_clear()

    import app.storage.database as db_mod
    db_mod._engine = None
    db_mod._session_factory = None

    import app.services.config_applier as ca
    ca._applier = None

    yield

    get_settings.cache_clear()
    db_mod._engine = None
    db_mod._session_factory = None
    ca._applier = None


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


@pytest.mark.asyncio
async def test_status_lists_targets(client):
    resp = await client.get("/api/apply-config")
    assert resp.status_code == 200
    data = resp.json()
    assert data["gateway_base_url"] == "http://localhost:8400"
    ids = {t["id"] for t in data["targets"]}
    assert {"claude-code", "claude-desktop", "codex-cli", "codex-app",
            "grok-build", "cline", "roo-code"} <= ids


@pytest.mark.asyncio
async def test_master_toggle(client):
    resp = await client.put("/api/apply-config/master", json={"enabled": True})
    assert resp.status_code == 200
    assert resp.json()["master_enabled"] is True
    assert (await client.get("/api/apply-config")).json()["master_enabled"] is True


@pytest.mark.asyncio
async def test_target_toggle(client):
    resp = await client.put("/api/apply-config/targets/claude-code", json={"enabled": True})
    assert resp.json()["enabled"] is True
    resp = await client.put("/api/apply-config/targets/nope", json={"enabled": True})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_apply_and_revert_claude_code(client, tmp_path, monkeypatch):
    """Apply writes ~/.claude/settings.json; revert restores the backup."""
    home = Path.home()
    existing = home / ".claude" / "settings.json"
    existing.parent.mkdir(parents=True)
    existing.write_text(json.dumps({"env": {"CUSTOM": "keep-me"}, "other": 1}))

    await client.put("/api/apply-config/master", json={"enabled": True})
    await client.put("/api/apply-config/targets/claude-code", json={"enabled": True})
    resp = await client.post("/api/apply-config/apply", json={"target_ids": ["claude-code"]})
    assert resp.status_code == 200
    assert resp.json()["applied"] == 1

    data = json.loads(existing.read_text())
    assert data["env"]["ANTHROPIC_BASE_URL"] == "http://localhost:8400"
    assert data["env"]["ANTHROPIC_AUTH_TOKEN"] == "gcc-local"
    assert data["env"]["CUSTOM"] == "keep-me"  # merged, not clobbered
    assert data["other"] == 1

    # Backup exists
    assert existing.with_name("settings.json.gcc-backup").exists()

    # Status detects applied
    status = (await client.get("/api/apply-config")).json()
    target = next(t for t in status["targets"] if t["id"] == "claude-code")
    assert target["applied"] is True

    # Revert restores the original content
    resp = await client.post("/api/apply-config/revert", json={"target_ids": ["claude-code"]})
    assert resp.json()["reverted"] == 1
    restored = json.loads(existing.read_text())
    assert "ANTHROPIC_BASE_URL" not in restored.get("env", {})
    assert restored["env"]["CUSTOM"] == "keep-me"
    assert not existing.with_name("settings.json.gcc-backup").exists()


@pytest.mark.asyncio
async def test_apply_codex_toml(client):
    """Apply writes a valid Codex config.toml with the gcc provider."""
    await client.put("/api/apply-config/master", json={"enabled": True})
    await client.put("/api/apply-config/targets/codex-cli", json={"enabled": True})
    resp = await client.post("/api/apply-config/apply", json={"target_ids": ["codex-cli"]})
    assert resp.json()["applied"] == 1

    config_path = Path.home() / ".codex" / "config.toml"
    assert config_path.exists()
    data = tomllib.loads(config_path.read_text())
    assert data["model_provider"] == "gcc"
    assert data["model"] == "auto"
    assert data["model_providers"]["gcc"]["base_url"] == "http://localhost:8400/v1"
    assert data["model_providers"]["gcc"]["wire_api"] == "responses"
    assert data["model_providers"]["gcc"]["env_key"] == "GCC_API_KEY"


@pytest.mark.asyncio
async def test_apply_preserves_existing_toml(client):
    """Existing TOML content survives the apply (parsed, merged, rewritten)."""
    config_path = Path.home() / ".codex" / "config.toml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text('model = "gpt-5.4"\napproval_policy = "on-request"\n')

    await client.put("/api/apply-config/targets/codex-cli", json={"enabled": True})
    await client.post("/api/apply-config/apply", json={"target_ids": ["codex-cli"]})

    data = tomllib.loads(config_path.read_text())
    assert data["approval_policy"] == "on-request"
    assert data["model_provider"] == "gcc"


@pytest.mark.asyncio
async def test_apply_cline_vscode_settings(client):
    """Apply writes Cline settings into VS Code user settings.json."""
    await client.put("/api/apply-config/targets/cline", json={"enabled": True})
    resp = await client.post("/api/apply-config/apply", json={"target_ids": ["cline"]})
    assert resp.json()["applied"] == 1

    path = Path.home() / ".config" / "Code" / "User" / "settings.json"
    assert path.exists()
    data = json.loads(path.read_text())
    assert data["cline.apiProvider"] == "openai-compatible"
    assert data["cline.openAiCompatible.baseUrl"] == "http://localhost:8400/v1"
    assert data["cline.openAiCompatible.apiKey"] == "gcc-local"
    assert data["cline.openAiCompatible.modelId"] == "auto"


@pytest.mark.asyncio
async def test_apply_roo_code_settings(client):
    await client.put("/api/apply-config/targets/roo-code", json={"enabled": True})
    resp = await client.post("/api/apply-config/apply", json={"target_ids": ["roo-code"]})
    assert resp.json()["applied"] == 1

    path = Path.home() / ".config" / "Code" / "User" / "settings.json"
    data = json.loads(path.read_text())
    assert data["roo-cline.apiProvider"] == "anthropic"
    assert data["roo-cline.anthropicBaseUrl"] == "http://localhost:8400"


@pytest.mark.asyncio
async def test_apply_master_off_skips_targets(client):
    """With the master toggle off, apply skips enabled targets."""
    await client.put("/api/apply-config/master", json={"enabled": False})
    await client.put("/api/apply-config/targets/claude-code", json={"enabled": True})
    resp = await client.post("/api/apply-config/apply")
    assert resp.json()["applied"] == 0
    results = {r["target"]: r["status"] for r in resp.json()["results"]}
    assert results.get("claude-code") == "skipped"


@pytest.mark.asyncio
async def test_apply_all_targets(client):
    """Applying with no target list configures every enabled target."""
    await client.put("/api/apply-config/master", json={"enabled": True})
    for target_id in ("claude-code", "codex-cli", "cline", "roo-code", "grok-build"):
        await client.put(f"/api/apply-config/targets/{target_id}", json={"enabled": True})
    resp = await client.post("/api/apply-config/apply")
    assert resp.json()["applied"] == 5
    assert (Path.home() / ".grok" / "user-settings.json").exists()
