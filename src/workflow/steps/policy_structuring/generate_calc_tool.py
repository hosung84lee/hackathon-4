"""5단계 — 보험금 계산 Tool 생성 (Agent, '보험금계산 프로그램 생성' 스킬).

Agent 가 write_calc_tool 로 코드를 저장하고 run_calc_tool_test 로 직접 시험한다.
Agent 가 보고한 테스트 케이스는 스텝이 다시 실행해 결과를 기록한다 (LLM 보고를 그대로 믿지 않는다).
생성 파일은 src/data/generated_tools/ 에 둔다 (커밋하지 않음).
"""
from pathlib import Path

from src.agent.agent import Agent, run_agent
from src.agent.tools import Tool
from src.backend.calc_runner import CalcToolError, run_calc_tool
from src.backend.config import DATA_DIR
from src.backend.storage import rel_path
from src.schema.policy_structuring import CalcToolOutput
from src.workflow.core import Ctx, step

SKILL = "policy_calc_tool_generator"

SYSTEM_PROMPT = (
    "너는 보험금 계산 프로그램 개발자다. 구조화된 사고보장 약관(structured_policy)으로 보험금 계산 Tool 을 만든다.\n"
    f"먼저 load_skill 로 '{SKILL}' 스킬 지침을 읽고 그대로 따른다.\n"
    "약관에 없는 규칙은 만들지 않는다. 구현하지 못한 부분은 limitations 에 적는다."
)


def make_tools(tool_file: Path) -> list[Tool]:
    def write_calc_tool(code: str) -> dict:
        compile(code, str(tool_file), "exec")  # 문법 오류는 툴 오류로 Agent 에 돌려준다
        tool_file.write_text(code, encoding="utf-8")
        return {"saved": rel_path(tool_file), "lines": code.count("\n") + 1}

    def run_calc_tool_test(payload: dict) -> dict:
        if not tool_file.exists():
            return {"error": "먼저 write_calc_tool 로 코드를 저장하세요"}
        try:
            return {"output": run_calc_tool(rel_path(tool_file), payload)}
        except CalcToolError as e:
            return {"error": str(e)}

    return [
        Tool("write_calc_tool", "계산 Tool 파이썬 코드 전체를 저장한다 (덮어쓰기)",
             {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]},
             write_calc_tool),
        Tool("run_calc_tool_test", "저장된 계산 Tool 을 입력 JSON 으로 실행해 결과를 본다",
             {"type": "object",
              "properties": {"payload": {"type": "object", "description": "{claim, judgments}"}},
              "required": ["payload"]},
             run_calc_tool_test),
    ]


@step(id="generate_calc_tool", name="보험금 계산 Tool 생성", workflow="policy_structuring", actor="agent")
def generate_calc_tool(ctx: Ctx) -> Ctx:
    conv = ctx.data["convert_schema"]
    tool_dir = DATA_DIR / "generated_tools"
    tool_dir.mkdir(parents=True, exist_ok=True)
    tool_file = tool_dir / f"{ctx.workflow_id}_calc_tool.py"
    agent = Agent(name="계산 Tool 생성 Agent", system_prompt=SYSTEM_PROMPT, tools=make_tools(tool_file),
                  skills=[SKILL], output_schema=CalcToolOutput, planning=True, max_iterations=30)
    result = run_agent(agent, {"structured_policy": conv["structured_policy"],
                               "judgment_items": conv["judgment_items"]})
    if result.status == "failed":
        raise RuntimeError(result.error)

    out = {"tool_path": None, "code": None, "summary": "", "limitations": [], "test_results": [],
           "plan": [p.model_dump() for p in result.plan], "observation_id": result.observation_id}
    if tool_file.exists():
        out["tool_path"] = rel_path(tool_file)
        out["code"] = tool_file.read_text(encoding="utf-8")
    if result.status == "escalated":
        ctx.add_escalation("generate_calc_tool", f"계산 Tool 생성 미완료: {result.escalation.reason}",
                           result.escalation.details, result.observation_id)
    else:
        output = CalcToolOutput.model_validate(result.output)
        out["summary"], out["limitations"] = output.summary, output.limitations
        out["test_results"] = [rerun_test(out["tool_path"], tc.name, tc.input, tc.expected_total)
                               for tc in output.test_cases]
    if out["tool_path"] is None:
        ctx.add_escalation("generate_calc_tool", "계산 Tool 파일이 생성되지 않았습니다",
                           observation_id=result.observation_id)
    ctx.data["generate_calc_tool"] = out
    return ctx


def rerun_test(tool_path: str | None, name: str, payload: dict, expected: int) -> dict:
    base = {"name": name, "input": payload, "expected_total": expected, "actual_total": None, "passed": False}
    if tool_path is None:
        return {**base, "error": "계산 Tool 파일 없음"}
    try:
        res = run_calc_tool(tool_path, payload)
    except CalcToolError as e:
        return {**base, "error": str(e)}
    actual = res.get("total_amount")
    return {**base, "actual_total": actual, "passed": actual == expected, "output": res}
