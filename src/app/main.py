"""Streamlit 진입점 (관리: 팀원 C).  실행: streamlit run src/app/main.py

화면 등록: 흐름 화면 파일(views/policy_structuring.py, views/claim_review.py)은 파일이 있으면 자동으로 등록된다.
새 화면을 추가해야 하면 PAGES 에 한 줄만 추가해 달라고 팀원 C 에게 요청한다.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: E402

VIEWS = Path(__file__).resolve().parent / "views"

# (파일, 제목, 아이콘, 메뉴 그룹)
PAGES = [
    ("policy_structuring.py", "약관 구조화", ":material/description:", "흐름"),
    ("claim_review.py", "청구 심사", ":material/fact_check:", "흐름"),
    ("trace_viewer.py", "실행 기록 (Trace)", ":material/account_tree:", "운영"),
    ("demo.py", "데모 워크플로우", ":material/science:", "개발"),
]

st.set_page_config(page_title="신상품 보험 AI 워크플로우", layout="wide")

groups: dict[str, list] = {}
for file, title, icon, group in PAGES:
    if (VIEWS / file).is_file():
        groups.setdefault(group, []).append(st.Page(str(VIEWS / file), title=title, icon=icon))

st.navigation(groups).run()
