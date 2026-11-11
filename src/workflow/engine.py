"""워크플로우 실행 엔진 — 시작, 사람 스텝에서 멈춤, 재개, 상태 저장 (설계 5장 사람 스텝).

화면에서 쓰는 방법
    from src.workflow import engine

    r = engine.start("claim_review", input={"product_code": "P001", "files": [...]},
                     human_inputs={"upload_documents": HumanInput(decision="제출", created_by="고객")})
    r.status            # RunStatus.WAITING_REVIEW 등
    r.workflow_id       # claim_review_000001

    engine.list_runs(status=RunStatus.WAITING_REVIEW)          # 검토 대기 목록
    run = engine.get_run("claim_review_000001")                # ctx 포함
    engine.resume("claim_review_000001", HumanInput(decision="승인", comment="...", created_by="심사자A"))
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from src.backend.db import connect, transaction
from src.schema.common import HumanInput, RunStatus
from src.trace.tracer import tracer
from src.workflow.core import (
    WORKFLOWS,
    Ctx,
    PauseForHuman,
    Rejected,
    Run,
    StepMeta,
    WorkflowDef,
    run_as_step,
)

# 엔진이 찾을 워크플로우 모듈. 없는 모듈은 건너뛴다.
WORKFLOW_MODULES = [
    "src.workflow.policy_structuring",
    "src.workflow.claim_review",
    "src.workflow.examples.demo",
]


def load_workflows() -> dict[str, WorkflowDef]:
    for mod in WORKFLOW_MODULES:
        try:
            importlib.import_module(mod)
        except ModuleNotFoundError as e:
            if e.name != mod:  # 모듈은 있는데 안에서 import 오류 → 그대로 알린다
                raise
    return WORKFLOWS


def get_workflow(code: str) -> WorkflowDef:
    if code not in WORKFLOWS:
        load_workflows()
    if code not in WORKFLOWS:
        raise KeyError(f"등록되지 않은 워크플로우: {code}")
    return WORKFLOWS[code]


@dataclass
class RunResult:
    workflow_id: str
    status: RunStatus
    current_step: str | None
    ctx: Ctx
    error: str | None = None


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ── 워크플로우 ID 발급 + 실행 상태 저장 ─────────────────────────────────


def _issue_run(code: str, ctx_input: dict[str, Any]) -> Ctx:
    """{code}_{6자리} 발급과 workflow_runs 저장을 한 트랜잭션에서 처리한다."""
    with transaction() as conn:
        row = conn.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM workflow_runs WHERE workflow_code = ?", (code,)).fetchone()
        seq = row[0]
        workflow_id = f"{code}_{seq:06d}"
        ctx = Ctx(workflow_id=workflow_id, workflow_code=code, input=ctx_input)
        now = _now()
        conn.execute(
            """INSERT INTO workflow_runs (workflow_id, workflow_code, seq, status, current_step, ctx, created_at, updated_at)
               VALUES (?, ?, ?, ?, NULL, ?, ?, ?)""",
            (workflow_id, code, seq, RunStatus.RUNNING.value, ctx.to_json(), now, now),
        )
    return ctx


def _save(ctx: Ctx, status: RunStatus, current_step: str | None, error: str | None = None) -> None:
    with transaction() as conn:
        conn.execute(
            "UPDATE workflow_runs SET status = ?, current_step = ?, ctx = ?, error = ?, updated_at = ? WHERE workflow_id = ?",
            (status.value, current_step, ctx.to_json(), error, _now(), ctx.workflow_id),
        )


def get_run(workflow_id: str) -> RunResult | None:
    row = connect().execute("SELECT * FROM workflow_runs WHERE workflow_id = ?", (workflow_id,)).fetchone()
    if row is None:
        return None
    return RunResult(
        workflow_id=row["workflow_id"],
        status=RunStatus(row["status"]),
        current_step=row["current_step"],
        ctx=Ctx.model_validate_json(row["ctx"]),
        error=row["error"],
    )


def list_runs(status: RunStatus | None = None, workflow_code: str | None = None) -> list[RunResult]:
    sql, args = "SELECT workflow_id FROM workflow_runs WHERE 1=1", []
    if status is not None:
        sql += " AND status = ?"
        args.append(status.value)
    if workflow_code is not None:
        sql += " AND workflow_code = ?"
        args.append(workflow_code)
    sql += " ORDER BY updated_at DESC"
    ids = [r[0] for r in connect().execute(sql, args).fetchall()]
    return [r for r in (get_run(i) for i in ids) if r is not None]


# ── 실행 ────────────────────────────────────────────────────────────


def _make_executor(ctx_holder: dict[str, Ctx]) -> Callable[[Callable[[Ctx], Ctx], StepMeta], Ctx]:
    def execute(fn: Callable[[Ctx], Ctx], meta: StepMeta) -> Ctx:
        ctx = ctx_holder["ctx"]
        _save(ctx, RunStatus.RUNNING, meta.id)
        with tracer.bind(ctx.workflow_id, meta.id):
            step_input = {"actor": meta.actor, "data_keys": list(ctx.data.keys())}
            if meta.actor == "human":
                step_input["human_input"] = ctx.human_inputs[meta.id].model_dump(mode="json")
            with tracer.span(meta.name, kind="step", input=step_input) as obs:
                new_ctx = run_as_step(fn, meta, ctx.model_copy(deep=True))
                obs.set_output(new_ctx.data.get(meta.id))
                if meta.actor == "human":
                    human = new_ctx.human_inputs[meta.id]
                    if human.decision != "제출":  # 승인/반려/수정 후 승인 → 스텝 단위 Score
                        tracer.score(name=meta.name, value=human.decision, comment=human.comment,
                                     created_by=human.created_by)
        new_ctx.completed_steps.append(meta.id)
        _save(new_ctx, RunStatus.RUNNING, meta.id)
        ctx_holder["ctx"] = new_ctx
        return new_ctx

    return execute


def _drive(wf: WorkflowDef, ctx: Ctx) -> RunResult:
    holder = {"ctx": ctx}
    tracer.update_trace(ctx.workflow_id, RunStatus.RUNNING.value)
    try:
        wf.fn(Run(ctx, _make_executor(holder)))
    except PauseForHuman as p:
        ctx = holder["ctx"]
        _save(ctx, RunStatus.WAITING_REVIEW, p.step.id)
        with tracer.bind(ctx.workflow_id, p.step.id):
            tracer.event(f"{p.step.name} 대기", kind="human", output={"escalations": len(ctx.escalations)},
                         level="WARNING" if ctx.escalations else "DEFAULT")
        tracer.update_trace(ctx.workflow_id, RunStatus.WAITING_REVIEW.value)
        return RunResult(ctx.workflow_id, RunStatus.WAITING_REVIEW, p.step.id, ctx)
    except Rejected as r:
        ctx = holder["ctx"]
        _save(ctx, RunStatus.REJECTED, r.step.id)
        tracer.update_trace(ctx.workflow_id, RunStatus.REJECTED.value,
                            output={"rejected_at": r.step.id, "comment": r.human.comment}, ended=True)
        return RunResult(ctx.workflow_id, RunStatus.REJECTED, r.step.id, ctx)
    except Exception as e:  # noqa: BLE001 — 실패로 저장하고 화면에 알린다
        ctx = holder["ctx"]
        msg = f"{type(e).__name__}: {e}"
        row = connect().execute("SELECT current_step FROM workflow_runs WHERE workflow_id = ?", (ctx.workflow_id,)).fetchone()
        _save(ctx, RunStatus.FAILED, row["current_step"] if row else None, error=msg)
        tracer.update_trace(ctx.workflow_id, RunStatus.FAILED.value, output={"error": msg}, ended=True)
        return RunResult(ctx.workflow_id, RunStatus.FAILED, row["current_step"] if row else None, ctx, msg)
    ctx = holder["ctx"]
    _save(ctx, RunStatus.COMPLETED, ctx.completed_steps[-1] if ctx.completed_steps else None)
    tracer.update_trace(ctx.workflow_id, RunStatus.COMPLETED.value, output=ctx.output, ended=True)
    return RunResult(ctx.workflow_id, RunStatus.COMPLETED, ctx.completed_steps[-1] if ctx.completed_steps else None, ctx)


def start(workflow_code: str, input: dict[str, Any] | None = None,
          human_inputs: dict[str, HumanInput] | None = None) -> RunResult:
    """새 실행 건을 만들고 사람 스텝·종료·실패까지 실행한다.

    human_inputs: 처음부터 사람 입력이 있는 스텝 (예: 서류 업로드 = {"upload_documents": HumanInput(decision="제출")})
    """
    wf = get_workflow(workflow_code)
    ctx = _issue_run(wf.code, input or {})
    if human_inputs:
        ctx.human_inputs.update(human_inputs)
        _save(ctx, RunStatus.RUNNING, None)
    tracer.start_trace(ctx.workflow_id, wf.code, wf.name, RunStatus.RUNNING.value, input=input)
    return _drive(wf, ctx)


def resume(workflow_id: str, human_input: HumanInput) -> RunResult:
    """'검토 대기' 건에 사람 입력을 넣고, 사람 스텝부터 이어서 실행한다."""
    run = get_run(workflow_id)
    if run is None:
        raise KeyError(f"없는 실행 건: {workflow_id}")
    if run.status != RunStatus.WAITING_REVIEW or run.current_step is None:
        raise ValueError(f"'검토 대기' 상태가 아닙니다: {workflow_id} ({run.status.label})")
    ctx = run.ctx
    ctx.human_inputs[run.current_step] = human_input
    return _drive(get_workflow(ctx.workflow_code), ctx)


def retry(workflow_id: str) -> RunResult:
    """'실패' 건을 마지막으로 성공한 스텝 다음부터 다시 실행한다."""
    run = get_run(workflow_id)
    if run is None:
        raise KeyError(f"없는 실행 건: {workflow_id}")
    if run.status != RunStatus.FAILED:
        raise ValueError(f"'실패' 상태가 아닙니다: {workflow_id} ({run.status.label})")
    return _drive(get_workflow(run.ctx.workflow_code), run.ctx)
