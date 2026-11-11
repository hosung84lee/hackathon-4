"""흐름 1 스텝들이 같이 쓰는 도우미 (스텝 아님). 추출한 페이지 텍스트는 ctx 대신 파일에 둔다."""
from __future__ import annotations

import json

from src.backend.config import DATA_DIR
from src.backend.storage import abs_path, rel_path

WORKFLOW = "policy_structuring"


def run_dir(workflow_id: str):
    d = DATA_DIR / "pdf" / workflow_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_pages(workflow_id: str, pages: list[dict]) -> str:
    path = run_dir(workflow_id) / "pages.json"
    path.write_text(json.dumps(pages, ensure_ascii=False), encoding="utf-8")
    return rel_path(path)


def load_pages(pages_path: str) -> dict[int, str]:
    """{페이지 번호: 텍스트}"""
    data = json.loads(abs_path(pages_path).read_text(encoding="utf-8"))
    return {p["page"]: p["text"] for p in data}
