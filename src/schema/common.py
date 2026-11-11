"""여러 흐름이 같이 쓰는 공통 모델 (관리: 팀원 C).

흐름 전용 모델은 schema/policy_structuring.py, schema/claim_review.py 에 둔다.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

# ── 워크플로우 / 스텝 ────────────────────────────────────────────────

Actor = Literal["tool", "agent", "program", "human"]

ACTOR_LABELS: dict[str, str] = {
    "tool": "도구",
    "agent": "Agent",
    "program": "프로그램",
    "human": "사람",
}


class RunStatus(str, Enum):
    """workflow_runs.status / traces.status 공통 값."""

    RUNNING = "RUNNING"
    WAITING_REVIEW = "WAITING_REVIEW"
    COMPLETED = "COMPLETED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"

    @property
    def label(self) -> str:
        return RUN_STATUS_LABELS[self]


RUN_STATUS_LABELS: dict[RunStatus, str] = {
    RunStatus.RUNNING: "실행 중",
    RunStatus.WAITING_REVIEW: "검토 대기",
    RunStatus.COMPLETED: "완료",
    RunStatus.REJECTED: "반려",
    RunStatus.FAILED: "실패",
}

HumanDecisionValue = Literal["승인", "반려", "수정 후 승인", "제출"]


class HumanInput(BaseModel):
    """사람 스텝에 들어가는 입력 (승인 여부·의견·수정값).

    - "제출": 서류 업로드처럼 승인/반려가 아닌 사람 작업 (Score 를 남기지 않는다)
    - "반려": 사람 스텝을 실행한 뒤 워크플로우를 반려로 종료한다
    """

    decision: HumanDecisionValue = "승인"
    comment: str = ""
    data: dict[str, Any] = Field(default_factory=dict)  # 수정값, 항목별 판단 등
    created_by: str = "reviewer"
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))


class Escalation(BaseModel):
    """Agent 가 판단을 사람에게 넘긴 기록. ctx.escalations 에 쌓인다."""

    step_id: str
    reason: str
    details: dict[str, Any] = Field(default_factory=dict)
    observation_id: str | None = None  # Agent span id (trace 연결용)


# ── Agent ─────────────────────────────────────────────────────────

PlanStatus = Literal["대기", "진행 중", "완료", "건너뜀"]


class PlanItem(BaseModel):
    content: str
    status: PlanStatus = "대기"
    skip_reason: str | None = None


AgentStatus = Literal["completed", "escalated", "failed"]


class AgentResult(BaseModel):
    """run_agent() 반환값."""

    status: AgentStatus
    output: dict[str, Any] | None = None  # output_schema 를 통과한 최종 출력
    escalation: Escalation | None = None  # status == "escalated" 일 때
    error: str | None = None  # status == "failed" 일 때
    plan: list[PlanItem] = Field(default_factory=list)
    iterations: int = 0
    observation_id: str | None = None  # Agent span id (판단 항목별 Score 대상)


# ── 상품 (흐름 1 → 흐름 2 연결) ──────────────────────────────────────


class JudgmentItem(BaseModel):
    """AI 판단 항목 정의 (설계 4.3). 흐름 1 에서 만들고 흐름 2 에서 쓴다."""

    code: str  # 예: MINOR_CANCER_YN
    name: str  # 예: 소액암여부
    description: str  # 판단 기준, 필요한 서류, 판단 불가 조건
    clauseRef: str = ""  # 근거 약관 조항
    outputSchema: dict[str, Any] = Field(default_factory=dict)  # Agent 출력 JSON 스키마


class Product(BaseModel):
    """승인되어 시스템에 반영된 상품 (흐름 1 의 7단계 결과)."""

    product_code: str
    product_name: str
    structured_policy: dict[str, Any] = Field(default_factory=dict)  # 표준 스키마로 변환된 약관
    judgment_items: list[JudgmentItem] = Field(default_factory=list)
    calc_tool_path: str | None = None  # 승인된 계산 Tool 파일 경로 (프로젝트 루트 기준)
    source_workflow_id: str | None = None  # 이 상품을 만든 policy_structuring 실행 건
    approved_by: str | None = None
    approved_at: str | None = None
