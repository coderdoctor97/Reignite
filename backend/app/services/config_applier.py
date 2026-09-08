"""
ConfigApplier — applies the gateway endpoint configuration to installed
applications and CLI agents ("Apply Config").

Supported targets (per-target toggle, master toggle, Apply / Revert):
    claude-code      ~/.claude/settings.json (env: ANTHROPIC_BASE_URL/AUTH_TOKEN)
    claude-desktop   claude_desktop_config.json (platform paths)
    codex-cli        ~/.codex/config.toml + ~/.codex/auth.json
    codex-app        shares ~/.codex/config.toml (Codex desktop/IDE)
    grok-build       ~/.grok/user-settings.json (best-effort)
    cline            VS Code user settings.json (Cline extension)
    roo-code         VS Code user settings.json (Roo Code extension)

Safety:
- Every write is backed up first ({file}.gcc-backup); Revert restores it.
- JSON files are merged (existing keys preserved); TOML files are parsed
  (tomllib) and re-serialized — if parsing fails, the target is reported
  as 'error' and the file is left untouched.
- Nothing here contains provider logic; it only writes endpoint config.
- Writes happen ONLY on explicit Apply — never automatically.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import sys
import tomllib
from pathlib import Path
from typing import Any, Optional

from app.core.config import get_settings
from app.core.logging import get_logger
from app.storage.database import get_async_session
from app.storage.repositories import SettingsRepository

logger = get_logger("config_applier")

MASTER_SETTING = "apply_config_enabled"


# ── Small TOML serializer (parse → mutate → write) ───────────────

def _toml_dumps(data: dict) -> str:
    """Serialize nested dicts/lists/scalars to TOML (tables + key = value)."""
    lines: list[str] = []

    def scalar(value: Any) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (int, float)):
            return str(value)
        if isinstance(value, str):
            return json.dumps(value, ensure_ascii=False)
        if isinstance(value, list):
            if all(isinstance(v, (str, int, float, bool)) for v in value):
                return "[" + ", ".join(scalar(v) for v in value) + "]"
            return json.dumps(value)
        return json.dumps(value)

    def table(name: str, value: dict) -> None:
        lines.append(f"\n[{name}]")
        for k, v in value.items():
            if isinstance(v, dict):
                table(f"{name}.{k}", v)
            else:
                lines.append(f"{k} = {scalar(v)}")

    for k, v in data.items():
        if isinstance(v, dict):
            table(k, v)
        else:
            lines.append(f"{k} = {scalar(v)}")
    return "\n".join(lines).lstrip("\n") + "\n"


# ── Target definitions ───────────────────────────────────────────

def _code_settings_candidates() -> list[Path]:
    """Candidate VS Code user settings.json paths."""
    home = Path.home()
    system = platform.system()
    candidates = []
    if system == "Darwin":
        base = home / "Library" / "Application Support"
        for app in ("Code", "Code - Insiders", "Cursor", "VSCodium"):
            candidates.append(base / app / "User" / "settings.json")
    elif system == "Windows":
        appdata = Path(os.environ.get("APPDATA", str(home / "AppData" / "Roaming")))
        for app in ("Code", "Code - Insiders", "Cursor", "VSCodium"):
            candidates.append(appdata / app / "User" / "settings.json")
    else:
        base = home / ".config"
        for app in ("Code", "Code - Insiders", "Cursor", "VSCodium"):
            candidates.append(base / app / "User" / "settings.json")
    return candidates


def _claude_desktop_candidates() -> list[Path]:
    home = Path.home()
    system = platform.system()
    if system == "Darwin":
        return [home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"]
    if system == "Windows":
        appdata = Path(os.environ.get("APPDATA", str(home / "AppData" / "Roaming")))
        return [appdata / "Claude" / "claude_desktop_config.json"]
    return [
        home / ".config" / "Claude" / "claude_desktop_config.json",
        home / ".config" / "claude-desktop-config.json",
    ]


class ConfigTarget:
    """A single app/agent that can be configured."""

    def __init__(self, id_: str, label: str, kind: str, note: str = "", best_effort: bool = False):
        self.id = id_
        self.label = label
        self.kind = kind
        self.note = note
        self.best_effort = best_effort

    # ── Overrides per target ─────────────────────────────────────

    def files(self) -> list[Path]:
        """Config file paths this target may touch (first existing wins for apply)."""
        raise NotImplementedError

    def detect_path(self) -> Optional[Path]:
        """Path whose existence means the app is installed."""
        for path in self.files():
            if path.exists():
                return path
        return None

    def build_content(self, existing: Optional[str], ctx: dict) -> tuple[str, str]:
        """Return (new_content, content_kind) for the file."""
        raise NotImplementedError


class ClaudeCodeTarget(ConfigTarget):
    def files(self) -> list[Path]:
        return [Path.home() / ".claude" / "settings.json"]

    def build_content(self, existing, ctx):
        data = json.loads(existing) if existing else {}
        env = data.setdefault("env", {})
        env["ANTHROPIC_BASE_URL"] = ctx["base_url"]
        env["ANTHROPIC_AUTH_TOKEN"] = ctx["token"]
        env.setdefault("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", "1")
        return json.dumps(data, indent=2) + "\n", "json"


class ClaudeDesktopTarget(ConfigTarget):
    def files(self) -> list[Path]:
        return _claude_desktop_candidates()

    def build_content(self, existing, ctx):
        data = json.loads(existing) if existing else {}
        env = data.setdefault("env", {})
        env["ANTHROPIC_BASE_URL"] = ctx["base_url"]
        env["ANTHROPIC_AUTH_TOKEN"] = ctx["token"]
        return json.dumps(data, indent=2) + "\n", "json"


class CodexCliTarget(ConfigTarget):
    def files(self) -> list[Path]:
        return [Path.home() / ".codex" / "config.toml"]

    def build_content(self, existing, ctx):
        data = {}
        if existing:
            try:
                data = tomllib.loads(existing)
            except Exception as e:
                raise RuntimeError(f"Cannot parse existing config.toml: {e}")
        data["model_provider"] = "gcc"
        data["model"] = ctx["model"]
        providers = data.setdefault("model_providers", {})
        providers["gcc"] = {
            "name": "Gateway Control Center",
            "base_url": ctx["base_url"] + "/v1",
            "wire_api": "responses",
            "env_key": "GCC_API_KEY",
            "requires_openai_auth": False,
        }
        return _toml_dumps(data), "toml"


class CodexAppTarget(ConfigTarget):
    """Codex desktop/IDE — shares the CLI's ~/.codex/config.toml."""

    def files(self) -> list[Path]:
        return [Path.home() / ".codex" / "config.toml"]

    def build_content(self, existing, ctx):
        return CodexCliTarget("codex-cli", "", "").build_content(existing, ctx)


class GrokBuildTarget(ConfigTarget):
    def files(self) -> list[Path]:
        return [Path.home() / ".grok" / "user-settings.json"]

    def build_content(self, existing, ctx):
        data = json.loads(existing) if existing else {}
        data["apiKey"] = ctx["token"]
        data["baseURL"] = ctx["base_url"] + "/v1"
        data["defaultModel"] = ctx["model"]
        return json.dumps(data, indent=2) + "\n", "json"


class ClineTarget(ConfigTarget):
    def files(self) -> list[Path]:
        return _code_settings_candidates()

    def build_content(self, existing, ctx):
        data = json.loads(existing) if existing else {}
        data["cline.apiProvider"] = "openai-compatible"
        data["cline.openAiCompatible.baseUrl"] = ctx["base_url"] + "/v1"
        data["cline.openAiCompatible.apiKey"] = ctx["token"]
        data["cline.openAiCompatible.modelId"] = ctx["model"]
        return json.dumps(data, indent=2) + "\n", "json"


class RooCodeTarget(ConfigTarget):
    def files(self) -> list[Path]:
        return _code_settings_candidates()

    def build_content(self, existing, ctx):
        data = json.loads(existing) if existing else {}
        # Anthropic mode through the gateway (the gateway speaks the
        # Anthropic Messages API at /v1/messages regardless of backend).
        data["roo-cline.apiProvider"] = "anthropic"
        data["roo-cline.anthropicBaseUrl"] = ctx["base_url"]
        data["roo-cline.apiKey"] = ctx["token"]
        data["roo-cline.anthropicModelId"] = ctx["model"]
        return json.dumps(data, indent=2) + "\n", "json"


TARGETS: dict[str, ConfigTarget] = {
    t.id: t
    for t in (
        ClaudeCodeTarget("claude-code", "Claude Code (CLI)", "cli"),
        ClaudeDesktopTarget("claude-desktop", "Claude Desktop", "app"),
        CodexCliTarget("codex-cli", "Codex CLI", "cli"),
        CodexAppTarget("codex-app", "Codex (app / IDE)", "app",
                       note="Shares ~/.codex/config.toml with the Codex CLI."),
        GrokBuildTarget("grok-build", "Grok Build", "cli",
                        note="Best-effort: Grok Build has no documented config file; writes ~/.grok/user-settings.json.",
                        best_effort=True),
        ClineTarget("cline", "Cline (VS Code)", "vscode"),
        RooCodeTarget("roo-code", "Roo Code (VS Code)", "vscode"),
    )
}


# ── Service ──────────────────────────────────────────────────────

class ConfigApplier:
    """Business logic for applying gateway config to local apps/agents."""

    def _context(self) -> dict:
        settings = get_settings()
        return {
            "base_url": settings.gateway_public_base_url.rstrip("/"),
            "token": settings.gateway_auth_token,
            "model": self._default_model_id(),
        }

    def _default_model_id(self) -> str:
        return "auto"

    # ── Settings helpers ─────────────────────────────────────────

    async def is_master_enabled(self) -> bool:
        async with get_async_session() as session:
            value = await SettingsRepository.get(session, MASTER_SETTING)
        return value == "true"

    async def set_master_enabled(self, enabled: bool) -> bool:
        async with get_async_session() as session:
            await SettingsRepository.set(session, MASTER_SETTING, "true" if enabled else "false")
            await session.commit()
        return enabled

    async def is_target_enabled(self, target_id: str) -> bool:
        async with get_async_session() as session:
            value = await SettingsRepository.get(session, f"apply_config_target.{target_id}")
        return value == "true"

    async def set_target_enabled(self, target_id: str, enabled: bool) -> bool:
        if target_id not in TARGETS:
            raise ValueError(f"Unknown target: {target_id}")
        async with get_async_session() as session:
            await SettingsRepository.set(
                session, f"apply_config_target.{target_id}", "true" if enabled else "false")
            await session.commit()
        return enabled

    # ── Status / detection ───────────────────────────────────────

    async def get_status(self) -> dict:
        ctx = self._context()
        targets = []
        for target in TARGETS.values():
            targets.append(await self._target_status(target, ctx))
        return {
            "master_enabled": await self.is_master_enabled(),
            "gateway_base_url": ctx["base_url"],
            "auth_token": ctx["token"],
            "default_model": ctx["model"],
            "targets": targets,
        }

    async def _target_status(self, target: ConfigTarget, ctx: dict) -> dict:
        installed_path = target.detect_path()
        installed = installed_path is not None
        applied = False
        error = None
        if installed_path and installed_path.exists():
            try:
                content = installed_path.read_text(encoding="utf-8", errors="replace")
                applied = ctx["base_url"] in content
            except OSError:
                pass
        return {
            "id": target.id,
            "label": target.label,
            "kind": target.kind,
            "best_effort": target.best_effort,
            "note": target.note,
            "installed": installed,
            "path": str(installed_path) if installed_path else None,
            "applied": applied,
            "enabled": await self.is_target_enabled(target.id),
            "error": error,
        }

    # ── Apply / revert ───────────────────────────────────────────

    async def apply(self, target_ids: Optional[list[str]] = None) -> dict:
        """Write config for the given targets (default: all enabled targets)."""
        ctx = self._context()
        master = await self.is_master_enabled()
        results = []
        for target in TARGETS.values():
            if target_ids is not None and target.id not in target_ids:
                continue
            enabled = await self.is_target_enabled(target.id)
            if target_ids is None and not (master and enabled):
                results.append({"target": target.id, "status": "skipped", "message": "not enabled"})
                continue
            results.append(self._apply_target(target, ctx))
        applied = [r for r in results if r["status"] == "applied"]
        errors = [r for r in results if r["status"] == "error"]
        logger.info("Apply config: %d targets, %d applied, %d errors", len(results), len(applied), len(errors))
        return {"results": results, "applied": len(applied), "errors": len(errors)}

    def _apply_target(self, target: ConfigTarget, ctx: dict) -> dict:
        path = target.detect_path()
        if path is None:
            path = target.files()[0]
        backup = path.with_name(path.name + ".gcc-backup")
        try:
            existing = path.read_text(encoding="utf-8") if path.exists() else None
            new_content, _kind = target.build_content(existing, ctx)
            if existing is not None and not backup.exists():
                shutil.copy2(path, backup)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(new_content, encoding="utf-8")
            return {"target": target.id, "status": "applied", "path": str(path),
                    "message": f"Configured {target.label}"}
        except Exception as e:
            logger.warning("Apply config failed for %s: %s", target.id, e)
            return {"target": target.id, "status": "error", "path": str(path),
                    "message": f"Failed: {e}"}

    async def revert(self, target_ids: Optional[list[str]] = None) -> dict:
        """Restore backups / remove written configs."""
        results = []
        for target in TARGETS.values():
            if target_ids is not None and target.id not in target_ids:
                continue
            results.append(self._revert_target(target))
        reverted = [r for r in results if r["status"] == "reverted"]
        logger.info("Revert config: %d reverted", len(reverted))
        return {"results": results, "reverted": len(reverted)}

    def _revert_target(self, target: ConfigTarget) -> dict:
        path = target.detect_path()
        if path is None:
            path = target.files()[0]
        backup = path.with_name(path.name + ".gcc-backup")
        try:
            if backup.exists():
                shutil.copy2(backup, path)
                backup.unlink()
                return {"target": target.id, "status": "reverted", "path": str(path),
                        "message": f"Restored previous {target.label} config"}
            if path.exists():
                # No backup: remove the file only if we plausibly created it
                # (it references the gateway endpoint).
                try:
                    content = path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    content = ""
                ctx = self._context()
                if ctx["base_url"] in content:
                    path.unlink()
                    return {"target": target.id, "status": "reverted", "path": str(path),
                            "message": f"Removed generated {target.label} config"}
            return {"target": target.id, "status": "nothing", "path": str(path),
                    "message": "No backup found — nothing to revert"}
        except Exception as e:
            return {"target": target.id, "status": "error", "path": str(path),
                    "message": f"Revert failed: {e}"}


# Module-level singleton
_applier: Optional[ConfigApplier] = None


def get_config_applier() -> ConfigApplier:
    global _applier
    if _applier is None:
        _applier = ConfigApplier()
    return _applier


def set_config_applier(applier: ConfigApplier) -> None:
    global _applier
    _applier = applier
