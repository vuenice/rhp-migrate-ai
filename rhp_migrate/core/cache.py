"""Content-hash caching for incremental RHP migration (Layer 7).

Stores SHA-256 hash of original PHP content and migration settings in
`.rhp_cache/migration_cache.json`. If a file has not changed and previously
passed verification, it is skipped on subsequent runs.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Optional, Union


def compute_content_hash(content: Union[str, bytes]) -> str:
    """Compute SHA-256 hex digest of source code content."""
    if isinstance(content, str):
        data = content.encode("utf-8")
    else:
        data = content
    return hashlib.sha256(data).hexdigest()


def compute_settings_hash(settings: dict[str, Any]) -> str:
    """Compute SHA-256 hex digest of serialized migration settings."""
    serialized = json.dumps(settings, sort_keys=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


@dataclass
class CacheEntry:
    """Represents a cached migration result for a single file."""

    content_hash: str
    settings_hash: str
    verified: bool = False
    settings: dict[str, Any] = field(default_factory=dict)
    stats: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        """Serialize cache entry to dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CacheEntry:
        """Construct CacheEntry from dictionary data."""
        return cls(
            content_hash=data["content_hash"],
            settings_hash=data.get("settings_hash", ""),
            verified=data.get("verified", False),
            settings=data.get("settings", {}),
            stats=data.get("stats", {}),
            timestamp=data.get("timestamp", ""),
        )


class MigrationCache:
    """Manages incremental file migration caching."""

    DEFAULT_CACHE_DIR = ".rhp_cache"
    DEFAULT_CACHE_FILE = "migration_cache.json"

    def __init__(
        self,
        cache_path: Optional[Union[str, Path]] = None,
        base_dir: Optional[Union[str, Path]] = None,
    ) -> None:
        self.base_dir = (
            Path(base_dir).resolve() if base_dir else Path.cwd().resolve()
        )
        if cache_path:
            self.cache_file = Path(cache_path).resolve()
        else:
            self.cache_file = (
                self.base_dir / self.DEFAULT_CACHE_DIR / self.DEFAULT_CACHE_FILE
            )

        self.entries: dict[str, CacheEntry] = {}
        self.load()

    def _normalize_key(self, file_path: Union[str, Path]) -> str:
        """Convert a file path to a canonical string key."""
        path = Path(file_path).resolve()
        try:
            return str(path.relative_to(self.base_dir))
        except ValueError:
            return str(path)

    def get_entry(self, file_path: Union[str, Path]) -> Optional[CacheEntry]:
        """Retrieve cache entry for a file if present."""
        key = self._normalize_key(file_path)
        if key in self.entries:
            return self.entries[key]
        # Also try absolute path string as fallback
        abs_key = str(Path(file_path).resolve())
        return self.entries.get(abs_key)

    def should_skip(
        self,
        file_path: Union[str, Path],
        content: Union[str, bytes],
        settings: Optional[dict[str, Any]] = None,
    ) -> bool:
        """Check if a file hasn't changed and previously passed verification.

        Args:
            file_path: Path to target file.
            content: Current raw content of the target file.
            settings: Current migration settings.

        Returns:
            True if cached, content matches, settings match, and verified=True.
        """
        entry = self.get_entry(file_path)
        if not entry:
            return False

        if not entry.verified:
            return False

        current_content_hash = compute_content_hash(content)
        if entry.content_hash != current_content_hash:
            return False

        if settings is not None:
            current_settings_hash = compute_settings_hash(settings)
            if entry.settings_hash != current_settings_hash:
                return False

        return True

    def update(
        self,
        file_path: Union[str, Path],
        content: Union[str, bytes],
        settings: Optional[dict[str, Any]] = None,
        verified: bool = True,
        stats: Optional[dict[str, Any]] = None,
    ) -> CacheEntry:
        """Record or update a cached file migration entry.

        Args:
            file_path: Target file path.
            content: Raw original file content.
            settings: Active migration settings.
            verified: True if file passed verification.
            stats: Migration stats (edits, llm count, todos).

        Returns:
            Created or updated CacheEntry.
        """
        key = self._normalize_key(file_path)
        clean_settings = settings or {}
        entry = CacheEntry(
            content_hash=compute_content_hash(content),
            settings_hash=compute_settings_hash(clean_settings),
            verified=verified,
            settings=clean_settings,
            stats=stats or {},
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        self.entries[key] = entry
        return entry

    def invalidate(self, file_path: Union[str, Path]) -> None:
        """Remove a file from the cache."""
        key = self._normalize_key(file_path)
        self.entries.pop(key, None)
        abs_key = str(Path(file_path).resolve())
        self.entries.pop(abs_key, None)

    def save(self) -> None:
        """Persist cache entries to disk atomically."""
        self.cache_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": "1.0",
            "entries": {k: e.to_dict() for k, e in self.entries.items()},
        }
        temp_file = self.cache_file.with_suffix(".tmp")
        temp_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        temp_file.replace(self.cache_file)

    def load(self) -> None:
        """Load cache entries from disk."""
        if not self.cache_file.is_file():
            self.entries = {}
            return

        try:
            data = json.loads(self.cache_file.read_text(encoding="utf-8"))
            entries_raw = data.get("entries", {})
            self.entries = {
                k: CacheEntry.from_dict(v) for k, v in entries_raw.items()
            }
        except (json.JSONDecodeError, KeyError, OSError):
            self.entries = {}

    def clear(self) -> None:
        """Clear cache entries in memory and remove cache file from disk."""
        self.entries = {}
        if self.cache_file.exists():
            try:
                self.cache_file.unlink()
            except OSError:
                pass

    def __bool__(self) -> bool:
        return True

    def __len__(self) -> int:
        return len(self.entries)
