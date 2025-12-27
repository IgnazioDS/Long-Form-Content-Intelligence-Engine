from __future__ import annotations

from pathlib import Path

STORAGE_ROOT = Path("storage")


def ensure_storage() -> None:
    STORAGE_ROOT.mkdir(parents=True, exist_ok=True)


def source_path(source_id: str) -> Path:
    ensure_storage()
    return STORAGE_ROOT / f"{source_id}.pdf"
