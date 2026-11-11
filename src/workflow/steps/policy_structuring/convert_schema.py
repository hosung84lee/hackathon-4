"""4단계 — 표준 스키마 구조로 변환 (Agent, planning).

핵심 원칙 (설계 3.4): 표준 스키마에 없는 항목은 추측해서 채우지 않고 undefined_items 에 모은다.
변환을 멈추지 않으므로 escalate 툴을 주지 않는다 (allow_escalate=False, 수집형).
"""
import json

from src.agent.agent import Agent, run_agent
from src.schema.policy_structuring import ConversionOutput
from src.workflow.core import Ctx, step
from src.workflow.steps.policy_structuring._pages import load_pages

SYSTEM_PROMPT = """\
너는 보험약관 구조화 담당자다. 사고(상해)보장 약관 텍스트와 PDF 를 읽고 표준 스키마(출력 JSON 스키마)로 변환한다.

[변환 규칙]
- 보장(coverage)마다 지급사유, 산정 방식(benefit), 지급 조건, 면책 사유, 면책기간, 감액을 약관 문구에 근거해 채운다.
- clause 에는 조항 번호와 원본 페이지 번호(입력의 page 값)를 적는다.
- 약관에 값이 없으면 비워 둔다(null 또는 빈 목록). 값을 추정하거나 다른 상품의 일반적인 값을 넣지 않는다.
- 표준 스키마에 담을 곳이 없는 항목(예: 스키마에 없는 특약 구조, 별표 기준표, 특수한 산식)은 억지로 끼워 넣지 말고
  undefined_items 에 항목명, 약관 위치, 사유(어떤 정의가 추가로 필요한지)를 적는다. 변환은 멈추지 않고 끝까지 한다.
- 진단서나 판독지 등 의사 소견을 보고 사람이 판단해야 하는 조건은 check_type="judgment" 로 두고,
  judgment_items 에 판단 항목을 정의한다: code(영문 대문자, 예: MINOR_CANCER_YN), name,
  description(판단 기준, 필요 서류, 판단 불가 조건), clauseRef(근거 조항),
  outputSchema(판단 결과 JSON 스키마: result "Y"/"N", reason, sources 를 포함).
  해당 조건의 judgment_code 에 그 code 를 적는다.
- 청구서의 값(날짜, 입원일수, 금액 등)만으로 확인 가능한 조건은 check_type="program".

[작업 방식]
- update_plan 으로 보장별 항목("보장: <보장명> 변환")과 마지막 "판단 항목과 미정의 항목 정리"를 계획한다.
- 항목을 하나씩 처리하며 상태를 갱신한다. 모두 끝나면 최종 JSON 을 출력한다."""

AGENT = Agent(name="약관 스키마 변환 Agent", system_prompt=SYSTEM_PROMPT, output_schema=ConversionOutput,
              planning=True, max_iterations=40, allow_escalate=False)


@step(id="convert_schema", name="표준 스키마 구조로 변환", workflow="policy_structuring", actor="agent")
def convert_schema(ctx: Ctx) -> Ctx:
    pages = load_pages(ctx.data["extract_text"]["pages_path"])
    selected = ctx.data["rebuild_pdf"]["pages"]
    result = run_agent(
        AGENT,
        {
            "product_code": ctx.input["product_code"],
            "product_name": ctx.input["product_name"],
            "pages": [{"page": n, "text": pages[n]} for n in selected],
        },
        pdfs=[ctx.data["rebuild_pdf"]["pdf_path"]],
    )
    if result.status == "failed":
        raise RuntimeError(result.error)
    out = {"structured_policy": None, "undefined_items": [], "judgment_items": [], "issues": [],
           "plan": [p.model_dump() for p in result.plan], "observation_id": result.observation_id}
    if result.status == "escalated":
        ctx.add_escalation("convert_schema", f"스키마 변환 미완료: {result.escalation.reason}",
                           result.escalation.details, result.observation_id)
    else:
        conv = ConversionOutput.model_validate(result.output)
        conv.structured_policy.product_code = ctx.input["product_code"]  # 입력값 우선
        conv.structured_policy.product_name = ctx.input["product_name"]
        out.update(json.loads(conv.model_dump_json()))
        out["issues"] = check_consistency(conv, set(selected))
    ctx.data["convert_schema"] = out
    return ctx


def check_consistency(conv: ConversionOutput, selected_pages: set[int]) -> list[str]:
    """프로그램 점검: 판단 조건과 판단 항목 정의가 맞는지, 보장 코드 중복, 근거 페이지가 선별 범위 안인지."""
    issues = []
    defined = {j.code for j in conv.judgment_items}
    used = set()
    codes = [c.coverage_code for c in conv.structured_policy.coverages]
    dup = sorted({c for c in codes if codes.count(c) > 1})
    if dup:
        issues.append(f"보장 코드 중복: {dup}")
    for c in conv.structured_policy.coverages:
        outside = [p for p in c.clause.pages if p not in selected_pages]
        if outside:
            issues.append(f"{c.coverage_code}: 근거 페이지 {outside} 가 선별된 페이지에 없음")
        for cond in c.conditions:
            if cond.check_type != "judgment":
                continue
            if not cond.judgment_code:
                issues.append(f"{c.coverage_code}: 판단 조건에 judgment_code 없음 ({cond.description})")
            elif cond.judgment_code not in defined:
                issues.append(f"{c.coverage_code}: 정의되지 않은 판단 항목 {cond.judgment_code}")
            else:
                used.add(cond.judgment_code)
    for code in sorted(defined - used):
        issues.append(f"판단 항목 {code} 를 쓰는 조건이 없음")
    return issues
