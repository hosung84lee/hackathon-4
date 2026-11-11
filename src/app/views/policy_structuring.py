"""흐름 1 — 보험약관 구조화 화면 (팀원 A).

담당자는 약관을 직접 해석하지 않고, AI 결과(구조화 약관·미정의 항목·판단 항목·계산 Tool)를 검토·승인한다.
"""
from __future__ import annotations

import json

import streamlit as st

from src.backend.config import DATA_DIR, get_settings
from src.backend.products import list_products
from src.backend.storage import abs_path, rel_path, save_file
from src.schema.common import HumanInput, RunStatus
from src.workflow import engine

CODE = "policy_structuring"
MOCK = get_settings().use_mock_llm

st.title("보험약관 구조화")
st.caption("약관 PDF → 보장(보험사고) 페이지 추출 → 표준 스키마 변환 → 계산 Tool 생성 → **담당자 검토·승인** → 시스템 반영")

tab_new, tab_review, tab_products, tab_history = st.tabs(["새 약관 처리", "검토 대기", "반영된 상품", "처리 이력"])

# ── 새 약관 처리 ─────────────────────────────────────────────────────

with tab_new:
    with st.form("new_policy"):
        c1, c2 = st.columns(2)
        product_code = c1.text_input("상품 코드", "ACC_SAFE_2026")
        product_name = c2.text_input("상품명", "무배당 안심상해보험")
        uploaded = st.file_uploader("약관 PDF", type=["pdf"])
        use_sample = st.checkbox("샘플 약관 사용 (업로드 없이 시연)", value=uploaded is None)
        use_mock = st.checkbox("샘플 시나리오 가짜 응답 사용 (샘플 약관일 때만)", value=True,
                               help="OpenAI 없이 샘플 약관을 끝까지 시연한다.") if MOCK else False
        submitted = st.form_submit_button("AI 처리 시작", type="primary")
    if MOCK:
        st.warning("가짜 LLM 모드(USE_MOCK_LLM=1)입니다. 실제 약관을 처리하려면 .env 에 OPENAI_API_KEY 를 넣고 "
                   "USE_MOCK_LLM=0 으로 바꾼 뒤 앱을 다시 실행하세요.")
    elif not get_settings().openai_api_key:
        st.error(".env 에 OPENAI_API_KEY 가 없습니다. 키를 넣고 앱을 다시 실행하세요.")
    else:
        st.caption(f"LLM: {get_settings().openai_model} · 200쪽 내외 약관은 수 분이 걸릴 수 있습니다. "
                   "진행 상황은 '실행 기록 (Trace)' 메뉴에서 볼 수 있습니다.")

    if submitted:
        if not product_code.strip() or not product_name.strip():
            st.error("상품 코드와 상품명을 입력하세요.")
        elif uploaded is None and not use_sample:
            st.error("약관 PDF 를 올리거나 샘플 약관을 선택하세요.")
        else:
            if uploaded is not None and not use_sample:
                pdf_path = save_file(uploaded.getvalue(), uploaded.name, "pdf")
            else:
                from src.workflow.steps.policy_structuring._sample_pdf import make_sample_policy_pdf

                pdf_path = rel_path(make_sample_policy_pdf(DATA_DIR / "pdf" / "sample_policy.pdf"))
            if use_mock and (uploaded is None or use_sample):  # 실제 업로드 파일에는 가짜 시나리오를 쓰지 않는다
                from src.workflow.steps.policy_structuring._demo_mock import install_demo_mock

                install_demo_mock()
            with st.status("AI 가 약관을 처리하는 중...", expanded=False):
                r = engine.start(CODE, {"pdf_path": pdf_path, "product_code": product_code.strip(),
                                        "product_name": product_name.strip()})
            if r.status == RunStatus.FAILED:
                st.error(f"{r.workflow_id} 실패 ({r.current_step}): {r.error}")
            else:
                st.success(f"{r.workflow_id}: {r.status.label} — '검토 대기' 탭에서 검토하세요.")

# ── 검토 대기 ──────────────────────────────────────────────────────


def _review_panel(run) -> None:
    d = run.ctx.data
    conv = d.get("convert_schema") or {}
    calc = d.get("generate_calc_tool") or {}
    policy = conv.get("structured_policy") or {}
    undefined = conv.get("undefined_items", [])
    tests = calc.get("test_results", [])

    m = st.columns(5)
    m[0].metric("전체 페이지", d["extract_text"]["page_count"])
    m[1].metric("사고보장 페이지", len(d["find_coverage_pages"]["pages"]))
    m[2].metric("보장 수", len(policy.get("coverages", [])))
    m[3].metric("스키마 미정의 항목", len(undefined))
    m[4].metric("계산 테스트 통과", f"{sum(t['passed'] for t in tests)}/{len(tests)}")

    for e in run.ctx.escalations:
        st.error(f"🚩 에스컬레이션 · {e.step_id}: {e.reason}")
    if undefined:
        st.warning(f"⚠️ 표준 스키마에 없는 항목 {len(undefined)}건 — 스키마 추가 정의가 필요한지 확인하세요.")
        st.dataframe([{"항목명": u["item_name"], "약관 위치": u["location"], "사유": u["reason"]} for u in undefined],
                     hide_index=True, use_container_width=True)
    for issue in conv.get("issues", []):
        st.warning(f"점검: {issue}")
    for f in d["find_coverage_pages"].get("failed_chunks", []):
        st.warning(f"페이지 선별 실패 p.{f['pages']}: {f['reason']}")
    if conv.get("summary"):
        st.info(f"변환 요약: {conv['summary']}")
    for u in conv.get("unconverted", []):
        st.warning(f"미변환: {u}")
    if conv and not conv.get("complete", True):
        st.error("변환이 끝까지 완료되지 않았습니다. 저장된 보장만 표시되며 계산 Tool 은 생성하지 않았습니다.")

    t1, t2, t3, t4 = st.tabs(["구조화 약관", "AI 판단 항목", "계산 Tool", "원문 페이지"])
    with t1:
        if not policy:
            st.info("구조화 결과가 없습니다.")
        for c in policy.get("coverages", []):
            b = c["benefit"]
            with st.expander(f"{c['coverage_name']} ({c['coverage_code']}) · {c['clause']['article']} · p.{c['clause']['pages']}"):
                st.markdown(f"**지급사유** {c['insured_event']}")
                st.markdown(f"**산정** {b['amount_type']} · {b.get('formula') or ''}")
                if c.get("conditions"):
                    st.markdown("**조건**\n" + "\n".join(
                        f"- [{'AI 판단: ' + (x.get('judgment_code') or '?') if x['check_type'] == 'judgment' else '프로그램'}] {x['description']}"
                        for x in c["conditions"]))
                if c.get("reduction"):
                    st.markdown(f"**감액** 가입 후 {c['reduction']['period_days']}일 이내 {c['reduction']['ratio']:.0%} 지급")
                if c.get("exclusions"):
                    st.markdown("**면책** " + ", ".join(c["exclusions"]))
        if policy.get("common_exclusions"):
            st.markdown("**공통 면책** " + ", ".join(policy["common_exclusions"]))
        with st.expander("JSON"):
            st.json(policy)
    with t2:
        st.caption("청구 심사(흐름 2)에서 AI 가 판단할 항목입니다.")
        for j in conv.get("judgment_items", []):
            with st.container(border=True):
                st.markdown(f"**{j['name']}** `{j['code']}` · {j.get('clauseRef', '')}")
                st.write(j["description"])
                with st.expander("판단 결과 출력 스키마"):
                    st.json(j.get("outputSchema", {}))
    with t3:
        if calc.get("summary"):
            st.markdown(f"**계산 로직** {calc['summary']}")
        for lim in calc.get("limitations", []):
            st.warning(f"미반영: {lim}")
        if tests:
            st.dataframe([{"테스트": t["name"], "기대 지급액": t["expected_total"], "실제 지급액": t["actual_total"],
                           "결과": "✅" if t["passed"] else "❌", "오류": t.get("error", "")} for t in tests],
                         hide_index=True, use_container_width=True)
        if calc.get("code"):
            st.code(calc["code"], language="python")
        elif d.get("convert_schema") is not None:
            st.info("생성된 계산 Tool 이 없습니다.")
    with t4:
        pdf = (d.get("rebuild_pdf") or {}).get("pdf_path")
        if pdf and abs_path(pdf).is_file():
            st.download_button("사고보장 페이지 PDF 받기", abs_path(pdf).read_bytes(),
                               file_name=f"{run.workflow_id}_coverage.pdf", mime="application/pdf")
        st.caption(f"선별 페이지: {d['find_coverage_pages']['pages']}  ·  후보 {d['find_coverage_pages']['candidate_count']}쪽")
        mode = conv.get("input_mode")
        if mode:
            st.caption(f"변환 입력: {'원문 전체' if mode['full_text'] else '목차 + read_pages'} · {mode['total_chars']:,}자 · "
                       f"PDF 첨부 {'예' if mode['pdf_attached'] else '아니오(페이지 수 초과)'}")
        for n in d["find_coverage_pages"].get("notes", []):
            st.caption(n)

    st.divider()
    with st.form(f"decide_{run.workflow_id}"):
        c1, c2 = st.columns([3, 1])
        comment = c1.text_area("검토 의견", height=80)
        reviewer = c2.text_input("검토자", "담당자")
        edited = st.text_area("구조화 약관 수정 (JSON, 고치면 '수정 후 승인')", json.dumps(policy, ensure_ascii=False, indent=2),
                              height=200) if policy else ""
        b1, b2 = st.columns(2)
        approve = b1.form_submit_button("승인 → 시스템 반영", type="primary", use_container_width=True)
        reject = b2.form_submit_button("반려", use_container_width=True)
    if approve or reject:
        human = HumanInput(decision="반려" if reject else "승인", comment=comment, created_by=reviewer)
        if approve and policy:
            try:
                new_policy = json.loads(edited)
            except json.JSONDecodeError as e:
                st.error(f"JSON 오류: {e}")
                return
            if new_policy != policy:
                human.decision, human.data = "수정 후 승인", {"structured_policy": new_policy}
        r = engine.resume(run.workflow_id, human)
        if r.status == RunStatus.FAILED:
            st.error(f"실패: {r.error}")
        else:
            st.session_state["policy_flash"] = f"{r.workflow_id}: {r.status.label}"
            st.rerun()


with tab_review:
    if msg := st.session_state.pop("policy_flash", None):
        st.success(msg)
    waiting = engine.list_runs(status=RunStatus.WAITING_REVIEW, workflow_code=CODE)
    if not waiting:
        st.info("검토 대기 건이 없습니다.")
    else:
        labels = {r.workflow_id: f"{r.workflow_id} · {r.ctx.input.get('product_name', '')}"
                  + (" 🚩" if r.ctx.escalations else "") for r in waiting}
        picked = st.selectbox("검토할 건", list(labels), format_func=labels.get)
        _review_panel(next(r for r in waiting if r.workflow_id == picked))

# ── 반영된 상품 ────────────────────────────────────────────────────

with tab_products:
    products = list_products()
    if not products:
        st.info("반영된 상품이 없습니다.")
    for p in products:
        with st.expander(f"{p.product_name} ({p.product_code}) · 승인 {p.approved_by} {p.approved_at or ''}"):
            st.caption(f"출처 {p.source_workflow_id} · 계산 Tool {p.calc_tool_path or '없음'}")
            st.markdown("**판단 항목** " + (", ".join(f"{j.name}(`{j.code}`)" for j in p.judgment_items) or "없음"))
            st.json(p.structured_policy, expanded=False)

# ── 처리 이력 ─────────────────────────────────────────────────────

with tab_history:
    runs = engine.list_runs(workflow_code=CODE)
    st.dataframe([{"워크플로우 ID": r.workflow_id, "상품": r.ctx.input.get("product_name", ""), "상태": r.status.label,
                   "현재 스텝": r.current_step, "에스컬레이션": len(r.ctx.escalations), "오류": r.error or ""}
                  for r in runs], hide_index=True, use_container_width=True)
    st.caption("스텝별 상세 기록은 '실행 기록 (Trace)' 메뉴에서 확인합니다.")
