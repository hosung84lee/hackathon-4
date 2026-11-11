"""흐름 1 — 약관 구조화 전용 모델 (관리: 팀원 A).

PolicyStructure 가 사고보장 약관의 '표준 스키마'다. 변환 Agent 는 이 구조에 있는 항목만 채우고,
약관에 있지만 여기 없는 항목은 UndefinedItem 으로 모은다 (설계 3.4 — 추측 금지, 수집형 에스컬레이션).
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from src.schema.common import JudgmentItem

# ── 표준 스키마 (사고보장) ───────────────────────────────────────────


class ClauseRef(BaseModel):
    article: str = Field(description="조항 번호 (예: 제3조 2항)")
    pages: list[int] = Field(default_factory=list, description="원본 약관 페이지 번호")


class BenefitRule(BaseModel):
    """보험금 산정 방식."""

    amount_type: Literal["정액", "실손", "비율", "일당"] = Field(description="지급 방식")
    base_amount: int | None = Field(default=None, description="정액·일당 지급 기준 금액(원)")
    ratio: float | None = Field(default=None, description="비율 지급 시 비율 (0~1, 예: 장해지급률·보상비율)")
    deductible: int | None = Field(default=None, description="자기부담금(원)")
    max_amount: int | None = Field(default=None, description="지급 한도(원)")
    max_count: int | None = Field(default=None, description="지급 횟수 한도")
    max_days: int | None = Field(default=None, description="일당 지급 최대 일수")
    formula: str = Field(default="", description="약관 문구 그대로의 산식 설명")


class Condition(BaseModel):
    """지급 조건. program 은 청구서로 체크, judgment 는 AI 판단 항목(judgment_code)으로 체크."""

    description: str = Field(description="조건 내용 (약관 문구 기반)")
    check_type: Literal["program", "judgment"] = Field(description="program: 청구정보로 체크 / judgment: 진단 내용 판단 필요")
    judgment_code: str | None = Field(default=None, description="check_type=judgment 일 때 JudgmentItem.code")


class Reduction(BaseModel):
    """감액 지급 (예: 가입 후 1년 이내 50%)."""

    period_days: int = Field(description="가입일로부터 감액 적용 기간(일)")
    ratio: float = Field(description="감액 기간 지급 비율 (0~1)")


class Coverage(BaseModel):
    """보장 1개 (예: 상해사망, 상해입원일당, 골절진단비)."""

    coverage_code: str = Field(description="보장 코드 (영문 대문자·숫자·밑줄, 약관 내 유일)")
    coverage_name: str = Field(description="보장명 (약관 표기)")
    clause: ClauseRef
    insured_event: str = Field(description="보험금 지급사유 (약관 문구 기반 요약)")
    benefit: BenefitRule
    conditions: list[Condition] = Field(default_factory=list)
    exclusions: list[str] = Field(default_factory=list, description="보험금을 지급하지 않는 사유")
    waiting_period_days: int | None = Field(default=None, description="면책기간(일)")
    reduction: Reduction | None = None


class PolicyStructure(BaseModel):
    product_code: str
    product_name: str
    coverages: list[Coverage] = Field(default_factory=list)
    common_exclusions: list[str] = Field(default_factory=list, description="모든 보장에 공통인 면책 사유")


# ── 변환 Agent 출력 ────────────────────────────────────────────────


class UndefinedItem(BaseModel):
    """약관에 있지만 표준 스키마에 없는 항목 (설계 3.4)."""

    item_name: str = Field(description="항목명")
    location: str = Field(description="약관 위치 (조항 번호, 페이지)")
    reason: str = Field(description="스키마에 없는 이유, 추가 정의가 필요한 내용")


class ConversionOutput(BaseModel):
    structured_policy: PolicyStructure
    undefined_items: list[UndefinedItem] = Field(default_factory=list)
    judgment_items: list[JudgmentItem] = Field(default_factory=list,
                                               description="심사자 판단이 필요한 조건 → AI 판단 항목 정의")


# ── 페이지 선별 Agent 출력 ──────────────────────────────────────────


class PageSelection(BaseModel):
    pages: list[int] = Field(description="사고보장 관련 페이지 번호 (입력에 있는 번호만)")
    notes: str = Field(default="", description="선별 근거 요약")


# ── 계산 Tool 생성 Agent 출력 ───────────────────────────────────────


class CalcTestCase(BaseModel):
    name: str
    input: dict = Field(description="계산 Tool 입력 JSON ({claim, judgments})")
    expected_total: int = Field(description="약관 기준 기대 지급액(원)")


class CalcToolOutput(BaseModel):
    summary: str = Field(description="계산 로직 요약 (보장별)")
    test_cases: list[CalcTestCase] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list, description="계산에 반영하지 못한 부분")
