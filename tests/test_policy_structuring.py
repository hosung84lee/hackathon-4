"""흐름 1 — 약관 구조화 점검 (가짜 LLM, 임시 DB).

실행: python -m unittest tests.test_policy_structuring -v
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="hackathon_policy_"))
os.environ.setdefault("DB_PATH", str(_TMP / "test.db"))
os.environ["USE_MOCK_LLM"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.agent import llm  # noqa: E402
from src.backend.calc_runner import run_calc_tool  # noqa: E402
from src.backend.config import DATA_DIR  # noqa: E402
from src.backend.products import get_product  # noqa: E402
from src.backend.storage import rel_path  # noqa: E402
from src.schema.common import HumanInput, RunStatus  # noqa: E402
from src.trace.tracer import get_observations  # noqa: E402
from src.workflow import engine  # noqa: E402
from src.workflow.steps.policy_structuring._sample_pdf import make_sample_policy_pdf  # noqa: E402
from src.workflow.steps.policy_structuring import _pages, generate_calc_tool  # noqa: E402
from src.workflow.steps.policy_structuring._demo_mock import CALC_CODE, CASE, CONVERSION  # noqa: E402

# 실제 실행 건의 파일과 섞이지 않도록 테스트 전용 폴더 사용 (src/data/pdf/ 아래라 git 에서 제외됨)
_TEST_DATA = DATA_DIR / "pdf" / "_test"
_pages.DATA_DIR = _TEST_DATA
generate_calc_tool.DATA_DIR = _TEST_DATA

PRODUCT = {"product_code": "TEST_ACC_01", "product_name": "무배당 안심상해보험"}

def plan(*statuses):
    return {"tool_calls": [{"name": "update_plan", "arguments": {"items": [
        {"content": f"항목 {i}", "status": s} for i, s in enumerate(statuses)]}}]}


def tearDownModule() -> None:
    shutil.rmtree(_TMP, ignore_errors=True)
    shutil.rmtree(_TEST_DATA, ignore_errors=True)


class PolicyStructuringTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.pdf = rel_path(make_sample_policy_pdf(_TEST_DATA / "sample_policy.pdf"))

    def setUp(self) -> None:
        llm.clear_mock_responses()
        llm.set_llm_client(None)

    def test_full_flow_approve(self) -> None:
        def select_pages(messages, tools):
            import json
            sent = json.loads(messages[-1]["content"])["pages"]
            self.assertNotIn(6, [p["page"] for p in sent])  # 키워드 없는 페이지(보험료 납입)는 후보에서 제외
            return {"content": {"pages": [3, 4, 5, 99], "notes": "제3~5조"}}  # 99 는 입력 밖 → 버려야 함

        llm.set_mock_responses("사고보장 페이지 선별 Agent", [select_pages])
        llm.set_mock_responses("약관 스키마 변환 Agent", [plan("진행 중", "대기"), plan("완료", "완료"), {"content": CONVERSION}])
        llm.set_mock_responses("계산 Tool 생성 Agent", [
            {"tool_calls": [{"name": "load_skill", "arguments": {"name": "policy_calc_tool_generator"}}]},
            plan("진행 중", "대기"),
            {"tool_calls": [{"name": "write_calc_tool", "arguments": {"code": "def broken(:"}}]},  # 문법 오류 → 툴 오류
            {"tool_calls": [{"name": "write_calc_tool", "arguments": {"code": CALC_CODE}}]},
            {"tool_calls": [{"name": "run_calc_tool_test", "arguments": {"payload": CASE}}]},
            plan("완료", "완료"),
            {"content": {"summary": "입원일당 + 골절진단비(감액)",
                         "test_cases": [{"name": "입원5일+골절", "input": CASE, "expected_total": 310000},
                                        {"name": "틀린 기대값", "input": CASE, "expected_total": 1}],
                         "limitations": ["별표 1 가산율 미반영"]}},
        ])

        r = engine.start("policy_structuring", {"pdf_path": self.pdf, **PRODUCT})
        self.assertEqual(r.status, RunStatus.WAITING_REVIEW, r.error)
        d = r.ctx.data
        self.assertEqual(d["extract_text"]["page_count"], 6)
        self.assertEqual(d["find_coverage_pages"]["pages"], [3, 4, 5])
        self.assertEqual(d["find_coverage_pages"]["dropped_pages"], [99])
        self.assertEqual(d["rebuild_pdf"]["pages"], [3, 4, 5])
        conv = d["convert_schema"]
        self.assertEqual(conv["structured_policy"]["product_code"], "TEST_ACC_01")  # 입력값으로 덮어씀
        self.assertEqual(len(conv["undefined_items"]), 1)
        self.assertEqual(conv["issues"], [])
        calc = d["generate_calc_tool"]
        self.assertTrue(calc["tool_path"].endswith("_calc_tool.py"))
        self.assertEqual([t["passed"] for t in calc["test_results"]], [True, False])  # 스텝이 직접 재실행해 판정
        self.assertEqual(r.ctx.escalations, [])

        obs = get_observations(r.workflow_id)
        self.assertTrue(any(o["kind"] == "skill" for o in obs))
        self.assertTrue(any(o["kind"] == "plan" for o in obs))
        self.assertTrue(any(o["name"] == "write_calc_tool" and o["level"] == "ERROR" for o in obs))

        r2 = engine.resume(r.workflow_id, HumanInput(decision="승인", comment="확인", created_by="담당자A"))
        self.assertEqual(r2.status, RunStatus.COMPLETED, r2.error)
        product = get_product("TEST_ACC_01")
        self.assertEqual([j.code for j in product.judgment_items], ["FRACTURE_YN"])
        self.assertEqual(product.source_workflow_id, r.workflow_id)
        self.assertEqual(run_calc_tool(product.calc_tool_path, CASE)["total_amount"], 310000)  # 흐름 2 가 쓰는 방식

    def test_no_coverage_pages_then_reject(self) -> None:
        llm.set_mock_responses("사고보장 페이지 선별 Agent", [{"content": {"pages": [], "notes": "없음"}}])
        r = engine.start("policy_structuring", {"pdf_path": self.pdf, "product_code": "NONE", "product_name": "없음"})
        self.assertEqual(r.status, RunStatus.WAITING_REVIEW, r.error)
        self.assertEqual(r.ctx.completed_steps, ["extract_text", "find_coverage_pages"])  # 변환 스텝 건너뜀
        self.assertEqual(len(r.ctx.escalations), 1)
        r2 = engine.resume(r.workflow_id, HumanInput(decision="반려", comment="사고보장 약관 아님"))
        self.assertEqual(r2.status, RunStatus.REJECTED)
        self.assertIsNone(get_product("NONE"))

    def test_conversion_escalation_skips_calc_tool(self) -> None:
        llm.set_mock_responses("사고보장 페이지 선별 Agent", [{"content": {"pages": [3]}}])
        llm.set_mock_responses("약관 스키마 변환 Agent", [{"content": "not json"}, {"content": "still not json"}])
        r = engine.start("policy_structuring", {"pdf_path": self.pdf, "product_code": "ESC", "product_name": "에스컬"})
        self.assertEqual(r.status, RunStatus.WAITING_REVIEW, r.error)
        self.assertNotIn("generate_calc_tool", r.ctx.completed_steps)
        self.assertEqual(r.ctx.escalations[0].step_id, "convert_schema")
        r2 = engine.resume(r.workflow_id, HumanInput(decision="승인"))  # 반영할 결과가 없으므로 반영 스텝 건너뜀
        self.assertEqual(r2.status, RunStatus.COMPLETED)
        self.assertNotIn("register_product", r2.ctx.completed_steps)
        self.assertIsNone(get_product("ESC"))


if __name__ == "__main__":
    unittest.main()
