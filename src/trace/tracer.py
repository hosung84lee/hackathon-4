"""Trace 기록 (설계 7장, Langfuse 방식).

Tracer 가 현재 workflow_id · step_id · 상위 observation 을 contextvar 로 들고 있어서,
그 안에서 일어나는 LLM 호출·툴 실행은 자동으로 같은 키로 기록된다.
워크플로우 엔진과 Agent 가 알아서 기록하므로, 스텝 코드에서는 보통 직접 쓸 일이 없다.

직접 기록이 필요할 때
    from src.trace.tracer import tracer

    with tracer.span("서류 조회", kind="tool", input={...}) as obs:
        ...
        obs.set_output({...})
    tracer.event("스키마 미정의 항목 발견", kind="escalation", output={...}, level="WARNING")
    tracer.score(name="판단 항목 승인", value="승인", observation_id=agent_obs_id)

워크플로우 밖(테스트 등)에서는 모든 기록이 아무 일도 하지 않는다.
"""
from __future__ import annotations

import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from typing import Any, Iterator, Literal

from src.backend.config import estimate_cost
from src.backend.db import connect, to_json, transaction

ObsType = Literal["SPAN", "GENERATION", "EVENT"]
Level = Literal["DEBUG", "DEFAULT", "WARNING", "ERROR"]

_workflow_id: ContextVar[str | None] = ContextVar("trace_workflow_id", default=None)
_step_id: ContextVar[str | None] = ContextVar("trace_step_id", default=None)
_parent_id: ContextVar[str | None] = ContextVar("trace_parent_id", default=None)

MAX_STR = 20_000  # 기록할 문자열 최대 길이


def _now() -> str:
    return datetime.now().isoformat(timespec="milliseconds")


def _clip(value: Any) -> Any:
    """큰 문자열·base64 데이터를 잘라 trace 가 커지지 않게 한다."""
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    if isinstance(value, str):
        if value.startswith("data:") and ";base64," in value[:100]:
            return f"<base64 {value[:value.index(';')]} {len(value)} chars>"
        return value if len(value) <= MAX_STR else value[:MAX_STR] + f"... <{len(value) - MAX_STR} chars truncated>"
    if isinstance(value, dict):
        return {k: _clip(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clip(v) for v in value]
    return value


def _dump(value: Any) -> str | None:
    return to_json(_clip(value))


class Observation:
    """열려 있는 Span/Generation 핸들. with 블록이 끝나면 종료 시각과 출력이 저장된다."""

    def __init__(self, observation_id: str | None):
        self.id = observation_id
        self._fields: dict[str, Any] = {}

    @property
    def active(self) -> bool:
        return self.id is not None

    def set_output(self, output: Any) -> None:
        self._fields["output"] = _dump(output)

    def set_level(self, level: Level, status_message: str | None = None) -> None:
        self._fields["level"] = level
        if status_message is not None:
            self._fields["status_message"] = status_message

    def set_usage(self, model: str, prompt_tokens: int, completion_tokens: int, cost: float | None = None) -> None:
        self._fields.update(
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost=estimate_cost(model, prompt_tokens, completion_tokens) if cost is None else cost,
        )

    def _close(self) -> None:
        if not self.active:
            return
        self._fields["ended_at"] = _now()
        cols = ", ".join(f"{k} = ?" for k in self._fields)
        with transaction() as conn:
            conn.execute(f"UPDATE observations SET {cols} WHERE observation_id = ?", (*self._fields.values(), self.id))


class Tracer:
    # ── 컨텍스트 ──────────────────────────────────────────────────
    @property
    def workflow_id(self) -> str | None:
        return _workflow_id.get()

    @property
    def step_id(self) -> str | None:
        return _step_id.get()

    @contextmanager
    def bind(self, workflow_id: str, step_id: str | None = None) -> Iterator[None]:
        """이 블록 안의 기록을 workflow_id / step_id 에 묶는다 (워크플로우 엔진이 사용)."""
        t1, t2, t3 = _workflow_id.set(workflow_id), _step_id.set(step_id), _parent_id.set(None)
        try:
            yield
        finally:
            _parent_id.reset(t3)
            _step_id.reset(t2)
            _workflow_id.reset(t1)

    # ── Trace (워크플로우 실행 1건) ────────────────────────────────
    def start_trace(self, workflow_id: str, workflow_code: str, workflow_name: str, status: str, input: Any = None) -> None:
        with transaction() as conn:
            conn.execute(
                """INSERT INTO traces (workflow_id, workflow_code, workflow_name, status, input, started_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (workflow_id, workflow_code, workflow_name, status, _dump(input), _now()),
            )

    def update_trace(self, workflow_id: str, status: str, output: Any = None, ended: bool = False) -> None:
        """상태를 바꾸고 토큰·비용 합계를 다시 계산한다. ended=True 면 종료 시각을 남긴다."""
        with transaction() as conn:
            conn.execute(
                """UPDATE traces SET
                     status = ?,
                     output = COALESCE(?, output),
                     ended_at = CASE WHEN ? THEN ? ELSE ended_at END,
                     total_tokens = (SELECT COALESCE(SUM(COALESCE(prompt_tokens,0) + COALESCE(completion_tokens,0)), 0)
                                     FROM observations WHERE workflow_id = ?),
                     total_cost = (SELECT COALESCE(SUM(cost), 0) FROM observations WHERE workflow_id = ?)
                   WHERE workflow_id = ?""",
                (status, _dump(output), int(ended), _now(), workflow_id, workflow_id, workflow_id),
            )

    # ── Observation ──────────────────────────────────────────────
    def _insert(self, type_: ObsType, kind: str, name: str, input: Any = None, output: Any = None,
                level: Level = "DEFAULT", status_message: str | None = None, ended: bool = False,
                model: str | None = None, model_params: Any = None) -> str | None:
        wf = _workflow_id.get()
        if wf is None:
            return None
        oid = uuid.uuid4().hex
        now = _now()
        with transaction() as conn:
            conn.execute(
                """INSERT INTO observations (observation_id, workflow_id, step_id, parent_id, seq, type, kind, name,
                       level, status_message, input, output, started_at, ended_at, model, model_params)
                   VALUES (?, ?, ?, ?, (SELECT COALESCE(MAX(seq), 0) + 1 FROM observations WHERE workflow_id = ?),
                           ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (oid, wf, _step_id.get(), _parent_id.get(), wf, type_, kind, name, level, status_message,
                 _dump(input), _dump(output), now, now if ended else None, model, _dump(model_params)),
            )
        return oid

    @contextmanager
    def span(self, name: str, kind: str, input: Any = None) -> Iterator[Observation]:
        """하위 기록을 묶는 Span. kind: step / agent / tool / ... 예외가 나면 ERROR 로 남기고 다시 던진다."""
        obs = Observation(self._insert("SPAN", kind, name, input=input))
        token = _parent_id.set(obs.id) if obs.active else None
        try:
            yield obs
        except BaseException as e:
            obs.set_level("ERROR", f"{type(e).__name__}: {e}")
            raise
        finally:
            if token is not None:
                _parent_id.reset(token)
            obs._close()

    @contextmanager
    def generation(self, name: str, model: str, input: Any = None, model_params: Any = None) -> Iterator[Observation]:
        """LLM 호출 1회. 블록 안에서 obs.set_usage(...) / obs.set_output(...) 를 호출한다."""
        obs = Observation(self._insert("GENERATION", "llm", name, input=input, model=model, model_params=model_params))
        try:
            yield obs
        except BaseException as e:
            obs.set_level("ERROR", f"{type(e).__name__}: {e}")
            raise
        finally:
            obs._close()

    def event(self, name: str, kind: str, input: Any = None, output: Any = None,
              level: Level = "DEFAULT", status_message: str | None = None) -> str | None:
        """순간 기록. kind: escalation / skill / plan / validation / human / ..."""
        return self._insert("EVENT", kind, name, input=input, output=output, level=level,
                            status_message=status_message, ended=True)

    def current_parent_id(self) -> str | None:
        return _parent_id.get()

    # ── Score (사람의 평가) ────────────────────────────────────────
    def score(self, name: str, value: str, comment: str = "", created_by: str = "",
              observation_id: str | None = None, step_id: str | None = None,
              workflow_id: str | None = None) -> str | None:
        wf = workflow_id or _workflow_id.get()
        if wf is None:
            return None
        sid = uuid.uuid4().hex
        with transaction() as conn:
            conn.execute(
                """INSERT INTO scores (score_id, workflow_id, step_id, observation_id, name, value, comment,
                       created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (sid, wf, step_id or _step_id.get(), observation_id, name, value, comment, created_by, _now()),
            )
        return sid


tracer = Tracer()


# ── 조회 (trace 화면용) ──────────────────────────────────────────────

def list_traces(workflow_code: str | None = None, status: str | None = None,
                escalated_only: bool = False, limit: int = 200) -> list[dict]:
    sql = """SELECT t.*,
                    (SELECT COUNT(*) FROM observations o WHERE o.workflow_id = t.workflow_id
                       AND o.kind = 'escalation') AS escalations,
                    (SELECT value FROM scores s WHERE s.workflow_id = t.workflow_id
                       ORDER BY created_at DESC LIMIT 1) AS last_score
             FROM traces t WHERE 1=1"""
    args: list[Any] = []
    if workflow_code:
        sql += " AND t.workflow_code = ?"
        args.append(workflow_code)
    if status:
        sql += " AND t.status = ?"
        args.append(status)
    if escalated_only:
        sql += " AND EXISTS (SELECT 1 FROM observations o WHERE o.workflow_id = t.workflow_id AND o.kind = 'escalation')"
    sql += " ORDER BY t.started_at DESC LIMIT ?"
    args.append(limit)
    return [dict(r) for r in connect().execute(sql, args).fetchall()]


def get_trace(workflow_id: str) -> dict | None:
    row = connect().execute("SELECT * FROM traces WHERE workflow_id = ?", (workflow_id,)).fetchone()
    return dict(row) if row else None


def get_observations(workflow_id: str) -> list[dict]:
    rows = connect().execute("SELECT * FROM observations WHERE workflow_id = ? ORDER BY seq", (workflow_id,)).fetchall()
    return [dict(r) for r in rows]


def get_scores(workflow_id: str) -> list[dict]:
    rows = connect().execute("SELECT * FROM scores WHERE workflow_id = ? ORDER BY created_at", (workflow_id,)).fetchall()
    return [dict(r) for r in rows]
