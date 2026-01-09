from __future__ import annotations

from pathlib import Path

from packages.shared_db.settings import settings

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SOURCE_EXTENSIONS = {
    "pdf": ".pdf",
    "text": ".txt",
    "url": ".url",
}

def _resolve_storage_root() -> Path:
    raw = Path(settings.storage_root).expanduser()
    if raw.is_absolute():
        return raw
    return _REPO_ROOT / raw


def ensure_storage() -> Path:
    storage_root = _resolve_storage_root()
    storage_root.mkdir(parents=True, exist_ok=True)
    return storage_root


def source_path(source_id: str, source_type: str | None = None) -> Path:
    storage_root = ensure_storage()
    key = (source_type or "pdf").strip().lower()
    ext = _SOURCE_EXTENSIONS.get(key, ".dat")
    return storage_root / f"{source_id}{ext}"
