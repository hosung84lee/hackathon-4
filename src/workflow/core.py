"""워크플로우 공통 인터페이스 (설계 5장) — ctx, @workflow, @step, Run.

워크플로우 파일 작성 예 (스텝 순서·분기만 둔다)

    from src.workflow.core import Run, workflow
    from src.workflow.steps.claim_review import classify_images, judge_items, ...

    @workflow(code="claim_review", name="보험금 청구 심사 지원")
    def claim_review(run: Run) -> None:
        run.step(upload_documents.upload_documents)        # human — start() 때 입력을 함께 넘긴다
        run.step(classify_images.classify_images)
        run.step(identify_judgments.identify_judgments)
        if run.ctx.data["identify_judgments"]["items"]:   # 분기는 워크플로우에서만
            run.step(judge_items.judge_items)
        run.step(calculate_claim.calculate_claim)
        run.step(review_approval.review_approval)         # human — 여기서 "검토 대기"로 멈춘다
        run.step(pay_claim.pay_claim)

스텝 파일 작성 예 (스텝 1개 = 파일 1개)

    from src.workflow.core import Ctx, step

    @step(id="classify_images", name="이미지 분류 및 태깅", workflow="claim_review", actor="agent")
    def classify_images(ctx: Ctx) -> Ctx:
        ...
        ctx.data["classify_images"] = {...}   # 스텝 결과는 ctx.data[스텝 id] 에 둔다
        return ctx

재개 방식
    사람 스텝에서 멈췄다가 재개하면 워크플로우 함수를 처음부터 다시 실행하되,
    이미 끝난 스텝(ctx.completed_steps)은 건너뛴다. 분기 조건은 ctx 로만 판단하므로 같은 경로를 따라간다.
    → 워크플로우 함수 안에서는 ctx 외의 값(시간, 난수, 외부 조회)으로 분기하지 않는다.
"""
from __future__ import annotations

import functools
import json
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Callable, get_args

from pydantic import BaseModel, Field

from src.schema.common import Actor, Escalation, HumanInput

# ── ctx ──────────────────────────────────────────────────────────────


class Ctx(BaseModel):
    """스텝 사이에 주고받는 공통 컨텍스트. JSON 으로 저장할 수 있는 값만 담는다 (이미지·PDF 는 경로만)."""

    workflow_id: str
    workflow_code: str
    input: dict[str, Any] = Field(default_factory=dict)  # 워크플로우 시작 입력
    data: dict[str, Any] = Field(default_factory=dict)  # 스텝 결과. 키는 스텝 id 로 쓴다
    escalations: list[Escalation] = Field(default_factory=list)  # Agent 에스컬레이션 (사람 검토 필요)
    human_inputs: dict[str, HumanInput] = Field(default_factory=dict)  # 사람 스텝 id → 사람 입력
    completed_steps: list[str] = Field(default_factory=list)  # 끝난 스텝 id (엔진이 관리)
    output: dict[str, Any] | None = None  # 워크플로우 최종 결과 (마지막 스텝이 채우면 trace 에 남는다)

    def add_escalation(self, step_id: str, reason: str, details: dict[str, Any] | None = None,
                       observation_id: str | None = None) -> Escalation:
        esc = Escalation(step_id=step_id, reason=reason, details=details or {}, observation_id=observation_id)
        self.escalations.append(esc)
        return esc

    def human(self, step_id: str) -> HumanInput | None:
        return self.human_inputs.get(step_id)

    def to_json(self) -> str:
        try:
            return json.dumps(self.model_dump(mode="json"), ensure_ascii=False)
        except (TypeError, ValueError) as e:
            raise TypeError(f"ctx 에 JSON 으로 저장할 수 없는 값이 있습니다: {e}") from e


# ── 어노테이션 ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class StepMeta:
    id: str
    name: str
    workflow: str
    actor: str


@dataclass(frozen=True)
class WorkflowDef:
    code: str
    name: str
    fn: Callable[["Run"], None]


WORKFLOWS: dict[str, WorkflowDef] = {}


class StepExecutionError(RuntimeError):
    """스텝을 워크플로우 밖에서 실행했거나, 다른 워크플로우의 스텝을 실행하려 했을 때."""


_executing: ContextVar[tuple[str, str] | None] = ContextVar("executing_step", default=None)  # (workflow_code, step_id)


def workflow(code: str, name: str) -> Callable[[Callable[["Run"], None]], Callable[["Run"], None]]:
    """워크플로우 함수에 붙인다. 함수는 Run 을 받아 run.step(...) 으로 스텝 순서·분기를 적는다."""

    def deco(fn: Callable[["Run"], None]) -> Callable[["Run"], None]:
        if code in WORKFLOWS and WORKFLOWS[code].fn.__qualname__ != fn.__qualname__:
            raise ValueError(f"워크플로우 코드 중복: {code}")
        WORKFLOWS[code] = WorkflowDef(code=code, name=name, fn=fn)
        fn.workflow_meta = WORKFLOWS[code]  # type: ignore[attr-defined]
        return fn

    return deco


def step(id: str, name: str, workflow: str, actor: Actor) -> Callable[[Callable[[Ctx], Ctx]], Callable[[Ctx], Ctx]]:
    """스텝 함수에 붙인다. 함수는 Ctx 를 받아 갱신된 Ctx 를 돌려준다. 상위 워크플로우의 run.step() 으로만 실행된다."""
    if actor not in get_args(Actor):
        raise ValueError(f"actor 는 {get_args(Actor)} 중 하나여야 합니다: {actor}")
    meta = StepMeta(id=id, name=name, workflow=workflow, actor=actor)

    def deco(fn: Callable[[Ctx], Ctx]) -> Callable[[Ctx], Ctx]:
        @functools.wraps(fn)
        def wrapper(ctx: Ctx) -> Ctx:
            if _executing.get() != (meta.workflow, meta.id):
                raise StepExecutionError(
                    f"스텝 '{meta.id}' 는 단독으로 실행할 수 없습니다. 상위 워크플로우 '{meta.workflow}' 의 run.step() 으로 실행하세요."
                )
            result = fn(ctx)
            if not isinstance(result, Ctx):
                raise TypeError(f"스텝 '{meta.id}' 는 Ctx 를 돌려줘야 합니다 (받은 값: {type(result).__name__})")
            return result

        wrapper.step_meta = meta  # type: ignore[attr-defined]
        return wrapper

    return deco


def step_meta(fn: Callable) -> StepMeta:
    meta = getattr(fn, "step_meta", None)
    if meta is None:
        raise StepExecutionError(f"{getattr(fn, '__name__', fn)} 에 @step 어노테이션이 없습니다")
    return meta


# ── Run (워크플로우 함수에 넘어가는 실행 핸들) ─────────────────────────────


class PauseForHuman(Exception):
    """사람 스텝에 도달 — 엔진이 받아서 '검토 대기'로 저장한다."""

    def __init__(self, step: StepMeta):
        super().__init__(step.id)
        self.step = step


class Rejected(Exception):
    """사람이 반려 — 엔진이 받아서 '반려'로 저장한다."""

    def __init__(self, step: StepMeta, human: HumanInput):
        super().__init__(step.id)
        self.step = step
        self.human = human


class Run:
    """워크플로우 함수 안에서 쓰는 핸들. 실제 실행(저장·trace)은 엔진이 넘겨준 executor 가 한다."""

    def __init__(self, ctx: Ctx, executor: Callable[[Callable[[Ctx], Ctx], StepMeta], Ctx]):
        self.ctx = ctx
        self._executor = executor

    @property
    def workflow_id(self) -> str:
        return self.ctx.workflow_id

    def step(self, fn: Callable[[Ctx], Ctx]) -> Ctx:
        meta = step_meta(fn)
        if meta.workflow != self.ctx.workflow_code:
            raise StepExecutionError(
                f"스텝 '{meta.id}' 는 워크플로우 '{meta.workflow}' 소속이라 '{self.ctx.workflow_code}' 에서 실행할 수 없습니다"
            )
        if meta.id in self.ctx.completed_steps:  # 재개 시 이미 끝난 스텝은 건너뜀
            return self.ctx
        if meta.actor == "human" and meta.id not in self.ctx.human_inputs:
            raise PauseForHuman(meta)
        self.ctx = self._executor(fn, meta)
        human = self.ctx.human_inputs.get(meta.id) if meta.actor == "human" else None
        if human is not None and human.decision == "반려":
            raise Rejected(meta, human)
        return self.ctx


def run_as_step(fn: Callable[[Ctx], Ctx], meta: StepMeta, ctx: Ctx) -> Ctx:
    """엔진 전용: 실행 허가를 걸고 스텝을 호출한다."""
    token = _executing.set((meta.workflow, meta.id))
    try:
        return fn(ctx)
    finally:
        _executing.reset(token)
