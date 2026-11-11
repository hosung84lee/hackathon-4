"""생성된 보험금 계산 Tool 실행기 (설계 3.5).

계산 Tool 규약 (흐름 1 의 생성 스킬이 이 규약대로 코드를 만든다)
    - 파이썬 파일 1개. 표준 라이브러리만 사용한다.
    - 표준입력으로 JSON 1개를 받는다: {"claim": {...청구정보}, "judgments": {판단코드: 판단 결과, ...}}
    - 표준출력으로 JSON 1개를 쓴다:   {"total_amount": 숫자, "breakdown": [...], ...}
    - 실패하면 0 이 아닌 종료 코드로 끝내고 오류 내용을 표준오류에 쓴다.

앱과 분리된 별도 프로세스로 실행하고 시간 제한을 건다. 생성 코드가 멈추거나 죽어도 앱에는 영향이 없다.
"""
from __future__ import annotations

import json
import subprocess
import sys
from typing import Any

from src.backend.config import get_settings
from src.backend.storage import abs_path


class CalcToolError(RuntimeError):
    pass


def run_calc_tool(tool_path: str, payload: dict[str, Any], timeout_sec: float | None = None) -> dict[str, Any]:
    path = abs_path(tool_path)
    if not path.is_file():
        raise CalcToolError(f"계산 Tool 파일이 없습니다: {tool_path}")
    timeout = timeout_sec or get_settings().calc_tool_timeout_sec
    try:
        proc = subprocess.run(
            [sys.executable, "-I", "-X", "utf8", str(path)],  # -I: 현재 폴더·환경변수의 모듈 무시, -X utf8: Windows 에서도 UTF-8 입출력
            input=json.dumps(payload, ensure_ascii=False),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
            cwd=path.parent,
        )
    except subprocess.TimeoutExpired as e:
        raise CalcToolError(f"계산 Tool 시간 초과 ({timeout}초)") from e
    if proc.returncode != 0:
        raise CalcToolError(f"계산 Tool 오류 (종료 코드 {proc.returncode}): {proc.stderr.strip()[-2000:]}")
    try:
        result = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise CalcToolError(f"계산 Tool 출력이 JSON 이 아닙니다: {proc.stdout[:500]!r}") from e
    if not isinstance(result, dict):
        raise CalcToolError("계산 Tool 출력은 JSON 객체여야 합니다")
    return result
