"""
SecretStore — abstraction for secure secret storage.

The database stores only references (secret_ref) and masked values.
Actual secrets are stored/retrieved through this interface.

Design:
- Phase 1.2: File-based store (development). Secrets are written to a
  directory outside the database, keyed by reference ID. This is NOT
  production-grade encryption — it's a clean abstraction boundary.
- Future (Windows): Swap in Windows Credential Manager via `keyring`.
- Future (cross-platform): Swap in OS keyring or encrypted file store.

Why not encrypt in SQLite now?
  Adding encryption to SQLite (via sqlcipher) introduces a native
  dependency that complicates the build for no immediate benefit during
  development. The file-based store keeps the boundary clean and makes
  the future swap to OS keyring trivial.

Why not use `keyring` now?
  `keyring` works, but adding it as a hard dependency now would require
  testing on the target Windows environment. The file-based store lets
  us develop and test on any platform. When the project reaches the
  Electron phase, we'll add `keyring` as the production backend.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Optional

from app.core.config import get_settings
from app.core.logging import get_logger, mask_secret

logger = get_logger("secrets")

# Directory where secrets are stored (outside the database)
_SECRETS_DIR_NAME = "secrets"


class SecretStore:
    """Abstract interface for secret storage.

    Implementations store and retrieve secrets by reference ID.
    The database never holds the actual secret — only the reference.
    """

    def store(self, value: str) -> str:
        """Store a secret and return a reference ID.

        Args:
            value: The secret value to store (API key, session cookie, etc.)

        Returns:
            A reference ID that can be used to retrieve the secret later.
        """
        raise NotImplementedError

    def retrieve(self, ref: str) -> Optional[str]:
        """Retrieve a secret by its reference ID.

        Args:
            ref: The reference ID returned by store().

        Returns:
            The secret value, or None if not found.
        """
        raise NotImplementedError

    def delete(self, ref: str) -> bool:
        """Delete a secret by its reference ID.

        Args:
            ref: The reference ID.

        Returns:
            True if deleted, False if not found.
        """
        raise NotImplementedError

    def exists(self, ref: str) -> bool:
        """Check if a secret exists for the given reference."""
        raise NotImplementedError


class FileSecretStore(SecretStore):
    """File-based secret store for development.

    Secrets are written as individual files in a secrets directory
    adjacent to the database. Each file is named by its reference ID.

    This is NOT encrypted storage — it's a clean abstraction boundary
    that makes the future swap to OS keyring trivial.
    """

    def __init__(self, base_dir: Optional[str] = None) -> None:
        if base_dir is None:
            settings = get_settings()
            base_dir = str(Path(settings.database_path).parent / _SECRETS_DIR_NAME)
        self._dir = Path(base_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path(self, ref: str) -> Path:
        """Return the file path for a given reference ID."""
        # Sanitize: only allow hex characters in the filename
        safe = "".join(c for c in ref if c.isalnum())
        return self._dir / f"{safe}.secret"

    def store(self, value: str) -> str:
        """Write a secret to a file and return its reference ID."""
        ref = uuid.uuid4().hex[:16]
        path = self._path(ref)
        # Write with restrictive permissions (owner only)
        path.write_text(value, encoding="utf-8")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass  # Windows may not support chmod
        logger.debug("Stored secret ref=%s (masked: %s)", ref, mask_secret(value))
        return ref

    def retrieve(self, ref: str) -> Optional[str]:
        """Read a secret from its file."""
        path = self._path(ref)
        if not path.exists():
            logger.warning("Secret not found: ref=%s", ref)
            return None
        try:
            return path.read_text(encoding="utf-8").strip()
        except Exception as e:
            logger.error("Failed to read secret ref=%s: %s", ref, e)
            return None

    def delete(self, ref: str) -> bool:
        """Delete a secret file."""
        path = self._path(ref)
        if path.exists():
            path.unlink()
            logger.debug("Deleted secret ref=%s", ref)
            return True
        return False

    def exists(self, ref: str) -> bool:
        """Check if a secret file exists."""
        return self._path(ref).exists()


# Module-level singleton
_store: Optional[SecretStore] = None


def get_secret_store() -> SecretStore:
    """Return the singleton SecretStore instance."""
    global _store
    if _store is None:
        _store = FileSecretStore()
    return _store


def set_secret_store(store: SecretStore) -> None:
    """Override the secret store (for testing or alternative backends)."""
    global _store
    _store = store
