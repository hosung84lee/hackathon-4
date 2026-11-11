"""공통 기반 점검 (가짜 LLM, 임시 DB).

실행: python -m unittest discover -s tests -v
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="hackathon_test_"))
os.environ["DB_PATH"] = str(_TMP / "test.db")
os.environ["USE_MOCK_LLM"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.agent import llm  # noqa: E402
from src.agent.agent import Agent, run_agent  # noqa: E402
from src.agent.skills import get_skill  # noqa: E402
from src.backend.calc_runner import CalcToolError, run_calc_tool  # noqa: E402
from src.schema.common import HumanInput, RunStatus  # noqa: E402
from src.trace.tracer import get_observations, get_scores, get_trace  # noqa: E402
from src.workflow import engine  # noqa: E402
from src.workflow.core import StepExecutionError  # noqa: E402
from src.workflow.steps.demo.extract_text import extract_text  # noqa: E402

FINAL_Y = {"content": {"result": "Y", "reason": "원문에 '암' 포함", "sources": ["원문"]}}
CALL_GET_TEXT = {"tool_calls": [{"name": "get_text", "arguments": {}}]}


def tearDownModule() -> None:
    shutil.rmtree(_TMP, ignore_errors=True)


class WorkflowTest(unittest.TestCase):
    def setUp(self) -> None:
        llm.clear_mock_responses()
        llm.set_llm_client(None)

    def test_pause_resume_complete(self) -> None:
        llm.set_mock_responses("데모 판단 Agent", [CALL_GET_TEXT, FINAL_Y])
        r = engine.start("demo", {"text": "갑상선암 진단"})
        self.assertEqual(r.status, RunStatus.WAITING_REVIEW, r.error)
        self.assertEqual(r.current_step, "review")
        self.assertRegex(r.workflow_id, r"^demo_\d{6}$")
        self.assertEqual(r.ctx.data["calculate"]["amount"], 1_000_000)

        r2 = engine.resume(r.workflow_id, HumanInput(decision="수정 후 승인", data={"amount": 500_000}, created_by="심사자"))
        self.assertEqual(r2.status, RunStatus.COMPLETED, r2.error)
        self.assertEqual(r2.ctx.output, {"amount": 500_000})
        self.assertEqual(r2.ctx.completed_steps, ["extract_text", "judge", "calculate", "review", "finalize"])

        trace = get_trace(r.workflow_id)
        self.assertEqual(trace["status"], "COMPLETED")
        obs = get_observations(r.workflow_id)
        kinds = [(o["type"], o["kind"]) for o in obs]
        self.assertIn(("SPAN", "agent"), kinds)
        self.assertIn(("SPAN", "tool"), kinds)
        self.assertEqual(sum(k == ("GENERATION", "llm") for k in kinds), 2)
        self.assertEqual(sum(k == ("SPAN", "step") for k in kinds), 5)
        self.assertTrue(all(o["workflow_id"] == r.workflow_id and o["step_id"] for o in obs))
        gen = next(o for o in obs if o["type"] == "GENERATION")
        agent_span = next(o for o in obs if o["kind"] == "agent")
        self.assertEqual(gen["parent_id"], agent_span["observation_id"])
        scores = get_scores(r.workflow_id)
        self.assertEqual([(s["step_id"], s["value"]) for s in scores], [("review", "수정 후 승인")])

    def test_ids_increment(self) -> None:
        a = engine.start("demo", {"text": ""})
        b = engine.start("demo", {"text": ""})
        self.assertEqual(int(b.workflow_id[-6:]), int(a.workflow_id[-6:]) + 1)
        self.assertNotIn("judge", b.ctx.completed_steps)  # 분기: 텍스트 없으면 Agent 건너뜀

    def test_escalation_then_reject(self) -> None:
        llm.set_mock_responses("데모 판단 Agent", [{"tool_calls": [{"name": "escalate", "arguments": {"reason": "서류 없음"}}]}])
        r = engine.start("demo", {"text": "판독지 없음"})
        self.assertEqual(r.status, RunStatus.WAITING_REVIEW)
        self.assertEqual(len(r.ctx.escalations), 1)
        self.assertEqual(r.ctx.escalations[0].step_id, "judge")
        self.assertIsNotNone(r.ctx.escalations[0].observation_id)
        r2 = engine.resume(r.workflow_id, HumanInput(decision="반려", comment="서류 보완 필요"))
        self.assertEqual(r2.status, RunStatus.REJECTED)
        self.assertNotIn("finalize", r2.ctx.completed_steps)
        self.assertEqual(get_trace(r.workflow_id)["status"], "REJECTED")
        with self.assertRaises(ValueError):
            engine.resume(r.workflow_id, HumanInput())

    def test_failure_and_retry(self) -> None:
        llm.set_mock_responses("데모 판단 Agent", [lambda m, t: 1 / 0])  # Client 예외 → Agent failed → 스텝 오류
        r = engine.start("demo", {"text": "암"})
        self.assertEqual(r.status, RunStatus.FAILED)
        self.assertEqual(r.current_step, "judge")
        self.assertEqual(r.ctx.completed_steps, ["extract_text"])  # 마지막 성공 스텝까지 ctx 보존
        llm.set_mock_responses("데모 판단 Agent", [FINAL_Y])
        r2 = engine.retry(r.workflow_id)
        self.assertEqual(r2.status, RunStatus.WAITING_REVIEW, r2.error)

    def test_step_cannot_run_alone(self) -> None:
        from src.workflow.core import Ctx

        with self.assertRaises(StepExecutionError):
            extract_text(Ctx(workflow_id="x", workflow_code="demo"))


class AgentTest(unittest.TestCase):
    def setUp(self) -> None:
        llm.clear_mock_responses()

    def test_schema_retry_then_escalate(self) -> None:
        schema = {"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"]}
        a = Agent(name="스키마", system_prompt="s", output_schema=schema, max_iterations=1)
        llm.set_mock_responses("스키마", [{"content": "not json"}, {"content": {"n": 3}}])
        self.assertEqual(run_agent(a, "x").output, {"n": 3})
        llm.set_mock_responses("스키마", [{"content": "x"}, {"content": {"n": "bad"}}])
        r = run_agent(a, "x")
        self.assertEqual(r.status, "escalated")
        self.assertIn("2회", r.escalation.reason)

    def test_default_mock_output(self) -> None:
        a = Agent(name="기본", system_prompt="s",
                  output_schema={"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]})
        self.assertEqual(run_agent(a, "x").output, {"ok": False})

    def test_planning_nudge(self) -> None:
        plan1 = {"items": [{"content": "조항 1", "status": "진행 중"}, {"content": "조항 2", "status": "대기"}]}
        plan2 = {"items": [{"content": "조항 1", "status": "완료"},
                           {"content": "조항 2", "status": "건너뜀", "skip_reason": "해당 없음"}]}
        a = Agent(name="계획", system_prompt="s", planning=True, max_iterations=10)
        llm.set_mock_responses("계획", [
            {"tool_calls": [{"name": "update_plan", "arguments": plan1}]},
            {"content": "끝"},  # 남은 항목 있음 → 계속 진행 메시지
            {"tool_calls": [{"name": "update_plan", "arguments": {"items": [{"content": "a", "status": "건너뜀"}]}}]},  # 사유 없음 → 툴 오류
            {"tool_calls": [{"name": "update_plan", "arguments": plan2}]},
            {"content": "완료"},
        ])
        r = run_agent(a, "x")
        self.assertEqual(r.status, "completed")
        self.assertEqual(r.iterations, 5)
        self.assertEqual([p.status for p in r.plan], ["완료", "건너뜀"])

    def test_planning_no_progress_escalates(self) -> None:
        plan = {"items": [{"content": "할 일", "status": "대기"}]}
        a = Agent(name="정체", system_prompt="s", planning=True, max_iterations=10)
        llm.set_mock_responses("정체", [{"tool_calls": [{"name": "update_plan", "arguments": plan}]}] + [{"content": "끝"}] * 3)
        r = run_agent(a, "x")
        self.assertEqual(r.status, "escalated")

    def test_max_iterations(self) -> None:
        a = Agent(name="반복", system_prompt="s", max_iterations=2,
                  tools=[__import__("src.agent.tools", fromlist=["Tool"]).Tool("noop", "d", {"type": "object"}, lambda: 1)])
        llm.set_mock_responses("반복", [{"tool_calls": [{"name": "noop", "arguments": {}}]}] * 3)
        self.assertIn("max_iterations", run_agent(a, "x").escalation.reason)

    def test_tool_error_is_returned_to_llm(self) -> None:
        seen = []
        a = Agent(name="툴오류", system_prompt="s")
        llm.set_mock_responses("툴오류", [{"tool_calls": [{"name": "nope", "arguments": {}}]},
                                       lambda m, t: seen.append(m[-1]) or {"content": "ok"}])
        self.assertEqual(run_agent(a, "x").output, {"text": "ok"})
        self.assertIn("error", seen[0]["content"])


class CommonTest(unittest.TestCase):
    def test_calc_runner(self) -> None:
        ok = _TMP / "ok_tool.py"
        ok.write_text(textwrap.dedent("""
            import json, sys
            d = json.load(sys.stdin)
            print(json.dumps({"total_amount": d["claim"]["base"] * 2}))
        """), encoding="utf-8")
        self.assertEqual(run_calc_tool(str(ok), {"claim": {"base": 5}, "judgments": {}}), {"total_amount": 10})
        korean = _TMP / "korean_tool.py"  # reconfigure 없이도 한글 입출력이 깨지지 않아야 함 (-X utf8)
        korean.write_text(textwrap.dedent("""
            import json, sys
            d = json.load(sys.stdin)
            print(json.dumps({"name": d["name"] + " 확인"}, ensure_ascii=False))
        """), encoding="utf-8")
        self.assertEqual(run_calc_tool(str(korean), {"name": "골절"}), {"name": "골절 확인"})
        hang = _TMP / "hang_tool.py"
        hang.write_text("import time\ntime.sleep(5)\n", encoding="utf-8")
        with self.assertRaises(CalcToolError):
            run_calc_tool(str(hang), {}, timeout_sec=0.5)

    def test_skill_loader(self) -> None:
        skill_dir = _TMP / "skills" / "sample"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("---\nname: sample\ndescription: 예시 스킬\n---\n지침 본문", encoding="utf-8")
        (skill_dir / "ref.txt").write_text("참고", encoding="utf-8")
        s = get_skill("sample", _TMP / "skills")
        self.assertEqual((s.description, s.instructions, s.files()), ("예시 스킬", "지침 본문", ["ref.txt"]))
        with self.assertRaises(FileNotFoundError):
            s.read_file("../../test.db")


if __name__ == "__main__":
    unittest.main()
