"""4단계 — 표준 스키마 구조로 변환 (Agent, planning).

핵심 원칙 (설계 3.4): 표준 스키마에 없는 항목은 추측해서 채우지 않고 미정의 항목으로 모은다.
변환을 멈추지 않으므로 escalate 툴을 주지 않는다 (allow_escalate=False, 수집형).

실제 약관은 보장이 수십 개라 한 번의 응답으로 받으면 출력 한도에 걸린다. 그래서 Agent 가
save_coverages / save_judgment_items / add_undefined_items 툴로 조금씩 저장하고, 최종 응답은 요약만 낸다.
원문이 길면 첫 메시지에는 페이지 목차만 주고 read_pages 툴로 필요한 페이지를 읽게 한다 (토큰 절약).
"""
from __future__ import annotations

import json
from typing import Any

from src.agent.agent import Agent, run_agent
from src.agent.tools import Tool
from src.schema.common import JudgmentItem
from src.schema.policy_structuring import ConversionOutput, ConversionSummary, Coverage, PolicyStructure, UndefinedItem
from src.workflow.core import Ctx, step
from src.workflow.steps.policy_structuring._pages import load_pages

FULL_TEXT_LIMIT = 60_000  # 선별 페이지 전체 글자 수가 이보다 적으면 첫 메시지에 원문을 모두 넣는다
READ_LIMIT = 40_000  # read_pages 1회 최대 글자 수
PDF_ATTACH_MAX_PAGES = 30  # 재생성 PDF 를 LLM 에 함께 보낼 최대 페이지 수 (표·서식 확인용, 넘으면 텍스트만)

SYSTEM_PROMPT = """\
너는 보험약관 구조화 담당자다. 보장(보험사고) 약관을 표준 스키마로 변환해 툴로 저장한다.
주계약과 특약의 보장을 모두 변환한다. 특약 보장은 coverage_name 에 특약명을 함께 적는다.

[변환 규칙]
- 보장(coverage)마다 지급사유, 산정 방식(benefit), 지급 조건, 면책 사유, 면책기간, 감액을 약관 문구에 근거해 채운다.
- clause 에는 조항 번호와 원본 페이지 번호(입력의 page 값)를 적는다.
- 약관에 값이 없으면 비워 둔다(null 또는 빈 목록). 값을 추정하거나 다른 상품의 일반적인 값을 넣지 않는다.
- 표준 스키마에 담을 곳이 없는 항목(스키마에 없는 특약 구조, 별표 기준표, 특수한 산식 등)은 억지로 끼워 넣지 말고
  add_undefined_items 로 항목명, 약관 위치, 사유(어떤 정의가 추가로 필요한지)를 남긴다. 변환은 멈추지 않고 끝까지 한다.
- 진단서나 판독지 등 의사 소견을 보고 사람이 판단해야 하는 조건은 check_type="judgment" 로 두고,
  save_judgment_items 로 판단 항목을 정의한다: code(영문 대문자, 예: MINOR_CANCER_YN), name,
  description(판단 기준, 필요 서류, 판단 불가 조건), clauseRef(근거 조항),
  outputSchema(판단 결과 JSON 스키마: result "Y"/"N", reason, sources 를 포함). 조건의 judgment_code 에 그 code 를 적는다.
- 청구서의 값(날짜, 입원일수, 금액 등)만으로 확인 가능한 조건은 check_type="program".

[작업 방식]
- 먼저 update_plan 으로 계획을 세운다: 보장(또는 조항 묶음)별 항목 + 마지막 "공통 면책·판단 항목·미정의 항목 점검".
- 원문이 메시지에 없으면 read_pages 로 필요한 페이지를 읽는다.
- 보장은 save_coverages 로 저장한다. 여러 보장을 한 번에 저장해도 된다. 같은 coverage_code 로 다시 저장하면 덮어쓴다.
- 툴이 검증 오류를 돌려주면 고쳐서 다시 저장한다.
- 모든 항목이 끝나면 최종 응답으로 요약 JSON 을 낸다."""


class _Collector:
    """Agent 가 툴로 저장한 결과를 모은다."""

    def __init__(self, product_code: str, product_name: str, selected: list[int], pages: dict[int, str]):
        self.policy = PolicyStructure(product_code=product_code, product_name=product_name)
        self.coverages: dict[str, Coverage] = {}
        self.judgments: dict[str, JudgmentItem] = {}
        self.undefined: list[UndefinedItem] = []
        self.selected = selected
        self.pages = pages

    # ── 툴 함수 ──
    def read_pages(self, pages: list[int]) -> dict:
        out, total, skipped = [], 0, []
        for n in pages:
            if n not in self.selected:
                skipped.append(n)
                continue
            text = self.pages[n]
            if total + len(text) > READ_LIMIT and out:
                return {"pages": out, "truncated": True, "skipped_not_selected": skipped,
                        "message": f"글자 수 한도로 p.{n} 부터는 다시 요청하세요"}
            out.append({"page": n, "text": text})
            total += len(text)
        return {"pages": out, "truncated": False, "skipped_not_selected": skipped}

    def save_coverages(self, coverages: list[dict]) -> dict:
        saved, errors = [], []
        for raw in coverages:
            try:
                c = Coverage.model_validate(raw)
            except Exception as e:  # noqa: BLE001 — 검증 오류를 Agent 에 돌려준다
                errors.append({"coverage": raw.get("coverage_code", "?"), "error": str(e)[:1500]})
                continue
            outside = [p for p in c.clause.pages if p not in self.selected]
            if outside:
                errors.append({"coverage": c.coverage_code, "error": f"clause.pages {outside} 는 선별된 페이지가 아닙니다"})
                continue
            self.coverages[c.coverage_code] = c
            saved.append(c.coverage_code)
        return {"saved": saved, "errors": errors, "total_coverages": len(self.coverages)}

    def save_judgment_items(self, items: list[dict]) -> dict:
        saved, errors = [], []
        for raw in items:
            try:
                j = JudgmentItem.model_validate(raw)
            except Exception as e:  # noqa: BLE001
                errors.append({"item": raw.get("code", "?"), "error": str(e)[:1500]})
                continue
            self.judgments[j.code] = j
            saved.append(j.code)
        return {"saved": saved, "errors": errors, "total_judgment_items": len(self.judgments)}

    def add_undefined_items(self, items: list[dict]) -> dict:
        errors = []
        for raw in items:
            try:
                self.undefined.append(UndefinedItem.model_validate(raw))
            except Exception as e:  # noqa: BLE001
                errors.append(str(e)[:500])
        return {"total_undefined_items": len(self.undefined), "errors": errors}

    def set_common_exclusions(self, exclusions: list[str]) -> dict:
        self.policy.common_exclusions = exclusions
        return {"ok": True, "count": len(exclusions)}

    def tools(self) -> list[Tool]:
        arr = lambda desc: {"type": "array", "items": {"type": "object"}, "description": desc}  # noqa: E731
        return [
            Tool("read_pages", "선별된 약관 페이지의 원문을 읽는다",
                 {"type": "object", "properties": {"pages": {"type": "array", "items": {"type": "integer"}}},
                  "required": ["pages"]}, self.read_pages),
            Tool("save_coverages", "보장(Coverage) 여러 개를 저장한다. 같은 coverage_code 는 덮어쓴다",
                 {"type": "object", "properties": {"coverages": arr("Coverage 객체 목록")}, "required": ["coverages"]},
                 self.save_coverages),
            Tool("save_judgment_items", "AI 판단 항목(JudgmentItem) 여러 개를 저장한다. 같은 code 는 덮어쓴다",
                 {"type": "object", "properties": {"items": arr("JudgmentItem 객체 목록")}, "required": ["items"]},
                 self.save_judgment_items),
            Tool("add_undefined_items", "표준 스키마에 없는 항목을 기록한다 (item_name, location, reason)",
                 {"type": "object", "properties": {"items": arr("UndefinedItem 객체 목록")}, "required": ["items"]},
                 self.add_undefined_items),
            Tool("set_common_exclusions", "모든 보장에 공통인 면책 사유를 저장한다",
                 {"type": "object", "properties": {"exclusions": {"type": "array", "items": {"type": "string"}}},
                  "required": ["exclusions"]}, self.set_common_exclusions),
        ]

    def result(self) -> ConversionOutput:
        self.policy.coverages = list(self.coverages.values())
        return ConversionOutput(structured_policy=self.policy, undefined_items=self.undefined,
                                judgment_items=list(self.judgments.values()))


def _schema_guide() -> dict[str, Any]:
    return {"Coverage": Coverage.model_json_schema(), "JudgmentItem": JudgmentItem.model_json_schema(),
            "UndefinedItem": UndefinedItem.model_json_schema()}


@step(id="convert_schema", name="표준 스키마 구조로 변환", workflow="policy_structuring", actor="agent")
def convert_schema(ctx: Ctx) -> Ctx:
    pages = load_pages(ctx.data["extract_text"]["pages_path"])
    selected = ctx.data["rebuild_pdf"]["pages"]
    col = _Collector(ctx.input["product_code"], ctx.input["product_name"], selected, pages)

    total_chars = sum(len(pages[n]) for n in selected)
    full_text = total_chars <= FULL_TEXT_LIMIT
    agent_input: dict[str, Any] = {
        "product_code": ctx.input["product_code"],
        "product_name": ctx.input["product_name"],
        "standard_schema": _schema_guide(),
    }
    if full_text:
        agent_input["pages"] = [{"page": n, "text": pages[n]} for n in selected]
    else:
        agent_input["page_outline"] = [{"page": n, "head": pages[n][:300]} for n in selected]
        agent_input["note"] = f"원문이 길어({total_chars:,}자) 페이지 목차만 넣었다. read_pages 로 필요한 페이지를 읽어라."
    attach_pdf = len(selected) <= PDF_ATTACH_MAX_PAGES

    agent = Agent(name="약관 스키마 변환 Agent", system_prompt=SYSTEM_PROMPT, tools=col.tools(),
                  output_schema=ConversionSummary, planning=True, max_iterations=80, allow_escalate=False)
    result = run_agent(agent, agent_input, pdfs=[ctx.data["rebuild_pdf"]["pdf_path"]] if attach_pdf else [])
    if result.status == "failed":
        raise RuntimeError(result.error)

    conv = col.result()
    complete = result.status == "completed"
    if not complete:
        ctx.add_escalation("convert_schema", f"스키마 변환 미완료: {result.escalation.reason}",
                           {**result.escalation.details, "saved_coverages": len(conv.structured_policy.coverages)},
                           result.observation_id)
    has_policy = bool(conv.structured_policy.coverages)
    if complete and not has_policy:
        ctx.add_escalation("convert_schema", "저장된 보장이 없습니다", observation_id=result.observation_id)

    out = json.loads(conv.model_dump_json())
    if not has_policy:
        out["structured_policy"] = None
    out.update({
        "complete": complete and has_policy,
        "summary": (result.output or {}).get("summary", ""),
        "unconverted": (result.output or {}).get("unconverted", []),
        "issues": check_consistency(conv, set(selected)) if has_policy else [],
        "input_mode": {"full_text": full_text, "total_chars": total_chars, "pdf_attached": attach_pdf},
        "plan": [p.model_dump() for p in result.plan],
        "observation_id": result.observation_id,
    })
    ctx.data["convert_schema"] = out
    return ctx


def check_consistency(conv: ConversionOutput, selected_pages: set[int]) -> list[str]:
    """프로그램 점검: 판단 조건과 판단 항목 정의가 맞는지, 근거 페이지가 선별 범위 안인지."""
    issues = []
    defined = {j.code for j in conv.judgment_items}
    used = set()
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
