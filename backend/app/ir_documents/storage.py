"""Local filesystem storage for IR PDFs.

Stored under {STORAGE_ROOT}/{company_id}/{doc_id}.{ext}.
For multi-server scaling, swap this with S3/R2 — the public API stays the same.
"""
import os
from pathlib import Path
from uuid import UUID

STORAGE_ROOT = Path(os.environ.get("IR_DOCS_ROOT", "/data/ir-docs"))


def ensure_root() -> None:
    STORAGE_ROOT.mkdir(parents=True, exist_ok=True)


def storage_path_for(company_id: UUID, doc_id: UUID, original_filename: str) -> Path:
    ext = Path(original_filename).suffix.lower() or ".pdf"
    company_dir = STORAGE_ROOT / str(company_id)
    company_dir.mkdir(parents=True, exist_ok=True)
    return company_dir / f"{doc_id}{ext}"


def write_bytes(path: Path, data: bytes) -> None:
    with open(path, "wb") as f:
        f.write(data)


def read_bytes(path: Path) -> bytes:
    with open(path, "rb") as f:
        return f.read()


def delete_file(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
