"""데모 워크플로우 화면 — 공통 기반(시작 → 검토 대기 → 승인/반려 → trace) 동작 확인용."""
from __future__ import annotations

import streamlit as st

from src.backend.config import get_settings
from src.schema.common import HumanInput, RunStatus
from src.workflow import engine

st.title("데모 워크플로우")
st.caption(f"LLM: {'가짜 Client (USE_MOCK_LLM=1)' if get_settings().use_mock_llm else get_settings().openai_model}")

with st.form("start"):
    text = st.text_area("입력 텍스트", "갑상선암 진단, 종양 크기 0.8cm")
    if st.form_submit_button("실행"):
        r = engine.start("demo", {"text": text})
        st.session_state["demo_last"] = r.workflow_id
        (st.error if r.status == RunStatus.FAILED else st.success)(f"{r.workflow_id}: {r.status.label} {r.error or ''}")

st.subheader("검토 대기")
waiting = engine.list_runs(status=RunStatus.WAITING_REVIEW, workflow_code="demo")
if not waiting:
    st.info("검토 대기 건이 없습니다.")
for run in waiting:
    with st.container(border=True):
        st.markdown(f"**{run.workflow_id}** · 대기 스텝 `{run.current_step}`")
        if run.ctx.escalations:
            for e in run.ctx.escalations:
                st.warning(f"에스컬레이션 ({e.step_id}): {e.reason}")
        st.json({k: v for k, v in run.ctx.data.items()}, expanded=False)
        c1, c2, c3 = st.columns([3, 1, 1])
        comment = c1.text_input("의견", key=f"c_{run.workflow_id}")
        if c2.button("승인", key=f"a_{run.workflow_id}", type="primary"):
            engine.resume(run.workflow_id, HumanInput(decision="승인", comment=comment, created_by="데모 심사자"))
            st.rerun()
        if c3.button("반려", key=f"r_{run.workflow_id}"):
            engine.resume(run.workflow_id, HumanInput(decision="반려", comment=comment, created_by="데모 심사자"))
            st.rerun()
