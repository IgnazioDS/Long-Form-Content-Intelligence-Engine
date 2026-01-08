from __future__ import annotations

from pathlib import Path

STORAGE_ROOT = Path("storage")
_SOURCE_EXTENSIONS = {
    "pdf": ".pdf",
    "text": ".txt",
    "url": ".url",
}


def ensure_storage() -> None:
    STORAGE_ROOT.mkdir(parents=True, exist_ok=True)


def source_path(source_id: str, source_type: str | None = None) -> Path:
    ensure_storage()
    key = (source_type or "pdf").strip().lower()
    ext = _SOURCE_EXTENSIONS.get(key, ".dat")
    return STORAGE_ROOT / f"{source_id}{ext}"
