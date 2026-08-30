"""
Tests for the configuration system.
"""

import pytest
import os

from app.core.config import Settings, get_settings


def test_settings_defaults():
    """Settings should have sensible defaults."""
    # Clear cache to get fresh settings
    get_settings.cache_clear()
    os.environ.pop("GCC_DATABASE_PATH", None)
    try:
        settings = Settings()
        assert settings.app_name == "Gateway Control Center"
        assert settings.app_version == "0.1.0"
        assert settings.backend_host == "127.0.0.1"
        assert settings.backend_port == 8400
        assert settings.log_level == "INFO"
        assert settings.debug is False
    finally:
        get_settings.cache_clear()


def test_settings_from_env():
    """Settings should be overridable via environment variables."""
    get_settings.cache_clear()
    os.environ["GCC_BACKEND_PORT"] = "9999"
    os.environ["GCC_LOG_LEVEL"] = "DEBUG"
    try:
        settings = Settings()
        assert settings.backend_port == 9999
        assert settings.log_level == "DEBUG"
    finally:
        del os.environ["GCC_BACKEND_PORT"]
        del os.environ["GCC_LOG_LEVEL"]
        get_settings.cache_clear()


def test_cors_origin_list():
    """cors_origin_list should parse comma-separated origins."""
    settings = Settings(cors_origins="http://a.com, http://b.com")
    assert settings.cors_origin_list == ["http://a.com", "http://b.com"]


def test_mask_secret():
    """mask_secret should mask all but the last N characters."""
    from app.core.logging import mask_secret
    assert mask_secret("abcdefghijklmnop", show=4) == "************mnop"
    assert mask_secret("short", show=4) == "*hort"  # 5 chars > show=4, so 1 mask + 4 visible
    assert mask_secret(None, show=4) == "****"
    assert mask_secret("", show=4) == "****"
