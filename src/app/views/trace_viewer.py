"""실행 기록 조회 화면 (설계 7.5) — 화면 1: 실행 목록, 화면 2: 스텝별 추적."""
from __future__ import annotations

import json
from datetime import datetime

import streamlit as st

from src.backend.db import from_json
from src.schema.common import RUN_STATUS_LABELS, RunStatus
from src.trace.tracer import get_observations, get_scores, get_trace, list_traces

STATUS_ICON = {"RUNNING": "🔄", "WAITING_REVIEW": "⏳", "COMPLETED": "✅", "REJECTED": "⛔", "FAILED": "❌"}
LEVEL_ICON = {"ERROR": "❌", "WARNING": "⚠️"}
KIND_ICON = {"step": "▣", "agent": "🤖", "tool": "🔧", "llm": "💬", "skill": "📘", "plan": "📝",
             "escalation": "🚩", "validation": "✔", "human": "🙋"}


def _duration(start: str | None, end: str | None) -> str:
    if not start or not end:
        return "-"
    sec = (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds()
    return f"{sec:.1f}초" if sec < 60 else f"{sec / 60:.1f}분"


def _label(status: str) -> str:
    try:
        return RUN_STATUS_LABELS[RunStatus(status)]
    except ValueError:
        return status


# ── 화면 1: 실행 목록 ────────────────────────────────────────────────

st.title("실행 기록")

with st.container(border=True):
    c1, c2, c3 = st.columns([2, 2, 1])
    all_traces = list_traces(limit=1000)
    codes = sorted({t["workflow_code"] for t in all_traces})
    code = c1.selectbox("워크플로우", ["전체", *codes])
    status = c2.selectbox("상태", ["전체", *[s.value for s in RunStatus]],
                          format_func=lambda s: s if s == "전체" else _label(s))
    escalated = c3.checkbox("에스컬레이션만", value=False)

traces = list_traces(None if code == "전체" else code, None if status == "전체" else status, escalated)
if not traces:
    st.info("기록된 실행이 없습니다.")
    st.stop()

rows = [{
    "워크플로우 ID": t["workflow_id"],
    "워크플로우명": t["workflow_name"],
    "상태": f"{STATUS_ICON.get(t['status'], '')} {_label(t['status'])}",
    "시작": t["started_at"][:19].replace("T", " "),
    "소요": _duration(t["started_at"], t["ended_at"]),
    "토큰": t["total_tokens"],
    "비용($)": round(t["total_cost"] or 0, 4),
    "에스컬레이션": t["escalations"],
    "심사 결과": t["last_score"] or "",
} for t in traces]
sel = st.dataframe(rows, hide_index=True, use_container_width=True, on_select="rerun", selection_mode="single-row")
picked = sel.selection.rows[0] if sel.selection.rows else 0
wf_id = traces[picked]["workflow_id"]

# ── 화면 2: 실행 상세 ────────────────────────────────────────────────

trace = get_trace(wf_id)
obs = get_observations(wf_id)
scores = get_scores(wf_id)
st.subheader(f"{trace['workflow_id']} · {trace['workflow_name']}")
st.caption(f"{STATUS_ICON.get(trace['status'], '')} {_label(trace['status'])} · "
           f"{_duration(trace['started_at'], trace['ended_at'])} · {trace['total_tokens']:,} 토큰 · "
           f"${trace['total_cost'] or 0:.4f}")

children: dict[str | None, list[dict]] = {}
for o in obs:
    children.setdefault(o["parent_id"], []).append(o)

tree: list[tuple[int, dict]] = []


def _walk(parent: str | None, depth: int) -> None:
    for o in children.get(parent, []):
        tree.append((depth, o))
        _walk(o["observation_id"], depth + 1)


_walk(None, 0)


def _tree_label(i: int) -> str:
    depth, o = tree[i]
    icon = LEVEL_ICON.get(o["level"]) or ("⏳" if o["type"] == "SPAN" and not o["ended_at"] else KIND_ICON.get(o["kind"], "•"))
    return f"{'　' * depth}{icon} {o['name']}"


left, right = st.columns([2, 3])
with left:
    st.markdown("**스텝 트리**")
    idx = st.radio("스텝 트리", range(len(tree)), format_func=_tree_label, label_visibility="collapsed",
                   key=f"tree_{wf_id}")

with right:
    if idx is None or not tree:
        st.stop()
    o = tree[idx][1]
    st.markdown(f"**[{o['type']} · {o['kind']}] {o['name']}**")
    meta = [f"스텝 `{o['step_id']}`", f"소요 {_duration(o['started_at'], o['ended_at'])}"]
    if o["type"] == "GENERATION":
        meta += [f"모델 `{o['model']}`", f"토큰 {(o['prompt_tokens'] or 0) + (o['completion_tokens'] or 0):,}",
                 f"비용 ${o['cost'] or 0:.5f}"]
    st.caption(" · ".join(meta))
    if o["status_message"]:
        (st.error if o["level"] == "ERROR" else st.warning)(o["status_message"])

    output = from_json(o["output"])
    if o["kind"] == "agent" and isinstance(output, dict) and output.get("plan"):
        st.markdown("**최종 계획**")
        for p in output["plan"]:
            mark = {"완료": "✅", "건너뜀": "⏭️", "진행 중": "🔄"}.get(p["status"], "⬜")
            st.markdown(f"{mark} {p['content']}" + (f" — _{p['skip_reason']}_" if p.get("skip_reason") else ""))

    item_scores = [s for s in scores if s["observation_id"] == o["observation_id"]
                   or (o["kind"] == "step" and s["step_id"] == o["step_id"] and not s["observation_id"])]
    for s in item_scores:
        st.success(f"Score · {s['name']}: **{s['value']}** — {s['comment'] or ''} ({s['created_by']})")

    inp = from_json(o["input"])
    if o["type"] == "GENERATION" and isinstance(inp, dict):
        st.markdown("**입력 메시지**")
        for m in inp.get("messages", []):
            with st.expander(f"{m.get('role')}", expanded=m.get("role") != "system"):
                content = m.get("content")
                if isinstance(content, str):
                    st.text(content)
                else:
                    st.json(content if content is not None else m, expanded=True)
                if m.get("tool_calls"):
                    st.json(m["tool_calls"])
        st.markdown("**출력**")
        st.json(output or {})
    else:
        st.markdown("**입력**")
        st.json(inp if inp is not None else {}, expanded=False)
        st.markdown("**출력**")
        st.json(output if output is not None else {}, expanded=True)

with st.expander("Trace 원본 (input / output)"):
    st.code(json.dumps({"input": from_json(trace["input"]), "output": from_json(trace["output"])},
                       ensure_ascii=False, indent=2), language="json")
