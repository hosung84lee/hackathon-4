"""SQLite 접근.

사용법
    from src.backend.db import connect, transaction, ensure_tables

    with transaction() as conn:          # 커밋/롤백 자동
        conn.execute("INSERT ...", (...))

    rows = connect().execute("SELECT ...").fetchall()   # 읽기 (sqlite3.Row)

흐름별 테이블은 이름 앞에 흐름 코드를 붙이고(예: claim_review_documents),
모듈 import 시점에 ensure_tables("CREATE TABLE IF NOT EXISTS ...") 로 만든다.
공통 테이블(workflow_runs, traces, observations, scores, products)은 이 파일에서 관리한다.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from typing import Any, Iterator

from src.backend.config import get_settings

COMMON_DDL = """
CREATE TABLE IF NOT EXISTS workflow_runs (
    workflow_id   TEXT PRIMARY KEY,
    workflow_code TEXT NOT NULL,
    seq           INTEGER NOT NULL,
    status        TEXT NOT NULL,
    current_step  TEXT,
    ctx           TEXT NOT NULL,
    error         TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    UNIQUE (workflow_code, seq)
);
CREATE INDEX IF NOT EXISTS idx_workflow_runs_status ON workflow_runs(status);

CREATE TABLE IF NOT EXISTS traces (
    workflow_id   TEXT PRIMARY KEY,
    workflow_code TEXT NOT NULL,
    workflow_name TEXT NOT NULL,
    status        TEXT NOT NULL,
    input         TEXT,
    output        TEXT,
    started_at    TEXT NOT NULL,
    ended_at      TEXT,
    total_tokens  INTEGER NOT NULL DEFAULT 0,
    total_cost    REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS observations (
    observation_id    TEXT PRIMARY KEY,
    workflow_id       TEXT NOT NULL,
    step_id           TEXT,
    parent_id         TEXT,
    seq               INTEGER NOT NULL,
    type              TEXT NOT NULL,      -- SPAN / GENERATION / EVENT
    kind              TEXT NOT NULL,      -- step / agent / tool / llm / skill / plan / escalation / ...
    name              TEXT NOT NULL,
    level             TEXT NOT NULL DEFAULT 'DEFAULT',
    status_message    TEXT,
    input             TEXT,
    output            TEXT,
    started_at        TEXT NOT NULL,
    ended_at          TEXT,
    model             TEXT,
    model_params      TEXT,
    prompt_tokens     INTEGER,
    completion_tokens INTEGER,
    cost              REAL
);
CREATE INDEX IF NOT EXISTS idx_observations_wf ON observations(workflow_id, seq);

CREATE TABLE IF NOT EXISTS scores (
    score_id       TEXT PRIMARY KEY,
    workflow_id    TEXT NOT NULL,
    step_id        TEXT,
    observation_id TEXT,
    name           TEXT NOT NULL,
    value          TEXT NOT NULL,
    comment        TEXT,
    created_by     TEXT,
    created_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_scores_wf ON scores(workflow_id);

CREATE TABLE IF NOT EXISTS products (
    product_code       TEXT PRIMARY KEY,
    product_name       TEXT NOT NULL,
    data               TEXT NOT NULL,     -- Product 전체 (JSON)
    source_workflow_id TEXT,
    updated_at         TEXT NOT NULL
);
"""

_local = threading.local()
_initialized: set[str] = set()
_init_lock = threading.Lock()


def connect() -> sqlite3.Connection:
    """스레드별 연결 1개를 재사용한다. 공통 테이블은 처음 연결할 때 만든다."""
    path = get_settings().db_path
    conn: sqlite3.Connection | None = getattr(_local, "conn", None)
    if conn is not None and getattr(_local, "path", None) == str(path):
        return conn
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30, isolation_level=None)  # autocommit; 트랜잭션은 transaction()
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    with _init_lock:
        if str(path) not in _initialized:
            conn.executescript(COMMON_DDL)
            _initialized.add(str(path))
    _local.conn, _local.path = conn, str(path)
    return conn


@contextmanager
def transaction(immediate: bool = True) -> Iterator[sqlite3.Connection]:
    """BEGIN IMMEDIATE ~ COMMIT. 예외가 나면 ROLLBACK."""
    conn = connect()
    if conn.in_transaction:  # 중첩 호출은 바깥 트랜잭션에 합친다
        yield conn
        return
    conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
    try:
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def ensure_tables(ddl: str) -> None:
    """흐름별 테이블 생성. CREATE TABLE IF NOT EXISTS 문을 넘긴다."""
    connect().executescript(ddl)


def to_json(value: Any) -> str | None:
    """DB 저장용 JSON 직렬화. 직렬화할 수 없는 값은 str() 로 바꾼다."""
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(value, ensure_ascii=False, default=str)


def from_json(text: str | None) -> Any:
    return None if text is None else json.loads(text)
