"""
LegacyCredentialAdapter — bridges the new credential system with the legacy gateway.

The legacy gateway (OpusGateway.py) reads the active API key from active_key.txt
every 3 seconds via its key_reloader() thread. When the key changes, it resets
usage counters and starts using the new key.

This adapter writes the active credential to active_key.txt using atomic
replacement, matching the legacy gateway's expectations.

Design:
- Atomic writes (write to .tmp, then os.replace) to avoid partial reads
- Never logs the credential value
- Reports failures clearly
- The legacy gateway discovers changes on its own reload cycle (no restart needed)
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from app.core.config import get_settings
from app.core.logging import get_logger, mask_secret

logger = get_logger("legacy_credential_store")

# The filename the legacy gateway watches
_ACTIVE_KEY_FILENAME = "active_key.txt"


class LegacyCredentialAdapter:
    """Writes the active credential to the legacy gateway's active_key.txt.

    This is the compatibility layer between the new CredentialManager
    and the legacy OpusGateway.py data plane.
    """

    def __init__(self, base_dir: Optional[str] = None) -> None:
        if base_dir is None:
            settings = get_settings()
            base_dir = settings.legacy_base_dir
        self._base_dir = Path(base_dir)
        self._base_dir.mkdir(parents=True, exist_ok=True)
        self._key_file = self._base_dir / _ACTIVE_KEY_FILENAME

    @property
    def key_file_path(self) -> Path:
        """Return the path to active_key.txt."""
        return self._key_file

    def write_active_credential(self, credential_value: str) -> None:
        """Write the credential to active_key.txt using atomic replacement.

        Args:
            credential_value: The raw API key to write.

        Raises:
            OSError: If the write fails.
        """
        if not credential_value:
            raise ValueError("Cannot write an empty credential")

        tmp_path = self._key_file.with_suffix(".txt.tmp")

        try:
            # Write to temporary file first
            tmp_path.write_text(credential_value, encoding="utf-8")
            try:
                os.chmod(tmp_path, 0o600)
            except OSError:
                pass  # Windows may not support chmod

            # Atomic replacement
            os.replace(str(tmp_path), str(self._key_file))

            logger.info(
                "Active credential written to %s (masked: %s)",
                self._key_file,
                mask_secret(credential_value),
            )
        except Exception as e:
            # Clean up temp file on failure
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except OSError:
                pass
            logger.error("Failed to write active credential: %s", e)
            raise

    def clear_active_credential(self) -> None:
        """Remove the active_key.txt file.

        The legacy gateway will see no key and return 503 to clients.
        """
        try:
            if self._key_file.exists():
                self._key_file.unlink()
                logger.info("Active credential cleared (%s)", self._key_file)
        except OSError as e:
            logger.error("Failed to clear active credential: %s", e)
            raise

    def read_active_key_prefix(self) -> Optional[str]:
        """Read the first few characters of the active key for display.

        Returns:
            A masked prefix like "sk-abcd..." or None if no key exists.
        """
        try:
            if not self._key_file.exists():
                return None
            content = self._key_file.read_text(encoding="utf-8").strip()
            if not content:
                return None
            return mask_secret(content)
        except OSError:
            return None


# Module-level singleton
_adapter: Optional[LegacyCredentialAdapter] = None


def get_legacy_credential_adapter() -> LegacyCredentialAdapter:
    """Return the singleton LegacyCredentialAdapter instance."""
    global _adapter
    if _adapter is None:
        _adapter = LegacyCredentialAdapter()
    return _adapter


def set_legacy_credential_adapter(adapter: LegacyCredentialAdapter) -> None:
    """Override the adapter (for testing)."""
    global _adapter
    _adapter = adapter
