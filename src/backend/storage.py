"""파일 저장. ctx·trace 에는 파일 내용 대신 여기서 돌려준 경로(프로젝트 루트 기준 상대 경로)만 담는다."""
from __future__ import annotations

import re
import uuid
from pathlib import Path

from src.backend.config import DATA_DIR, ROOT_DIR

_SAFE = re.compile(r"[^\w.\-가-힣]+")


def save_file(content: bytes, filename: str, subdir: str) -> str:
    """src/data/<subdir>/<uuid>_<filename> 으로 저장하고 상대 경로를 돌려준다.

    subdir 예: "pdf", "image", "generated_tools", "pdf/policy_structuring_000001"
    """
    target_dir = DATA_DIR / subdir
    target_dir.mkdir(parents=True, exist_ok=True)
    safe_name = _SAFE.sub("_", Path(filename).name) or "file"
    path = target_dir / f"{uuid.uuid4().hex[:8]}_{safe_name}"
    path.write_bytes(content)
    return rel_path(path)


def rel_path(path: str | Path) -> str:
    """절대 경로를 프로젝트 루트 기준 상대 경로(슬래시 구분)로 바꾼다."""
    p = Path(path)
    if p.is_absolute():
        p = p.relative_to(ROOT_DIR)
    return p.as_posix()


def abs_path(path: str | Path) -> Path:
    """ctx 에 담긴 상대 경로를 절대 경로로 바꾼다."""
    p = Path(path)
    return p if p.is_absolute() else ROOT_DIR / p
