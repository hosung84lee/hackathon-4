"""샘플 약관(_sample_pdf.py)용 가짜 LLM 시나리오 (스텝 아님).

OpenAI 키 없이(USE_MOCK_LLM=1) 흐름 1 을 끝까지 시연하거나 테스트할 때 쓴다.
"""
from __future__ import annotations

from src.agent.llm import set_mock_responses

CONVERSION = {
    "structured_policy": {
        "product_code": "x", "product_name": "x",
        "coverages": [
            {"coverage_code": "INJ_HOSP_DAILY", "coverage_name": "상해입원일당", "clause": {"article": "제3조", "pages": [3]},
             "insured_event": "상해로 4일 이상 계속 입원", "benefit": {"amount_type": "일당", "base_amount": 30000, "max_days": 180,
                                                              "formula": "3일 초과 입원일수 x 30,000원"},
             "conditions": [{"description": "4일 이상 계속 입원", "check_type": "program"}]},
            {"coverage_code": "FRACTURE_DIAG", "coverage_name": "골절진단비", "clause": {"article": "제4조", "pages": [4]},
             "insured_event": "상해로 골절 진단 확정", "benefit": {"amount_type": "정액", "base_amount": 500000},
             "conditions": [{"description": "X-ray/CT 판독상 골절 (치아 파절 제외)", "check_type": "judgment",
                             "judgment_code": "FRACTURE_YN"}],
             "reduction": {"period_days": 365, "ratio": 0.5}},
        ],
        "common_exclusions": ["고의 자해", "전쟁·내란", "음주운전 중 사고"],
    },
    "undefined_items": [{"item_name": "골절 부위별 가산 지급률표", "location": "별표 1, 5페이지",
                         "reason": "표준 스키마에 부위별 가산율 구조가 없음"}],
    "judgment_items": [{"code": "FRACTURE_YN", "name": "골절여부",
                        "description": "X-ray 또는 CT 판독지로 골절 여부를 판단한다. 치아 파절은 제외. 판독지가 없으면 판단 불가.",
                        "clauseRef": "제4조",
                        "outputSchema": {"type": "object", "properties": {"result": {"enum": ["Y", "N"]},
                                                                          "reason": {"type": "string"},
                                                                          "sources": {"type": "array", "items": {"type": "string"}}},
                                         "required": ["result", "reason", "sources"]}}],
}

CALC_CODE = '''
import json, sys
from datetime import date

def main():
    sys.stdin.reconfigure(encoding="utf-8"); sys.stdout.reconfigure(encoding="utf-8")
    p = json.load(sys.stdin); c, j = p.get("claim", {}), p.get("judgments", {})
    total, breakdown, not_paid = 0, [], []
    days = (c.get("hospitalization") or {}).get("days", 0)
    if days >= 4:
        amt = 30000 * min(days - 3, 180); breakdown.append({"coverage_code": "INJ_HOSP_DAILY", "amount": amt})
    else:
        not_paid.append({"coverage_code": "INJ_HOSP_DAILY", "reason": "입원 4일 미만"})
    fr = j.get("FRACTURE_YN")
    if fr is None:
        not_paid.append({"coverage_code": "FRACTURE_DIAG", "reason": "판단 결과 없음"})
    elif fr.get("result") == "Y":
        amt = 500000
        try:
            if (date.fromisoformat(c["accident_date"]) - date.fromisoformat(c["policy"]["contract_date"])).days < 365:
                amt = int(amt * 0.5)
        except (KeyError, TypeError, ValueError):
            pass
        breakdown.append({"coverage_code": "FRACTURE_DIAG", "amount": amt})
    print(json.dumps({"total_amount": sum(b["amount"] for b in breakdown), "breakdown": breakdown, "not_paid": not_paid}))

main()
'''

CASE = {"claim": {"policy": {"contract_date": "2026-01-01"}, "accident_date": "2026-03-01", "hospitalization": {"days": 5}},
        "judgments": {"FRACTURE_YN": {"result": "Y"}}}  # 입원 2일 x 30,000 + 골절 50% = 310,000



def _plan(*statuses):
    return {"tool_calls": [{"name": "update_plan", "arguments": {"items": [
        {"content": f"항목 {i}", "status": s} for i, s in enumerate(statuses)]}}]}


def install_demo_mock() -> None:
    """샘플 약관 1건 처리에 필요한 가짜 응답을 등록한다 (실행 1번 분량)."""
    set_mock_responses("사고보장 페이지 선별 Agent", [{"content": {"pages": [3, 4, 5], "notes": "제3~5조 사고보장"}}])
    set_mock_responses("약관 스키마 변환 Agent", [_plan("진행 중", "대기"), _plan("완료", "완료"), {"content": CONVERSION}])
    set_mock_responses("계산 Tool 생성 Agent", [
        {"tool_calls": [{"name": "load_skill", "arguments": {"name": "policy_calc_tool_generator"}}]},
        _plan("진행 중", "대기"),
        {"tool_calls": [{"name": "write_calc_tool", "arguments": {"code": CALC_CODE}}]},
        {"tool_calls": [{"name": "run_calc_tool_test", "arguments": {"payload": CASE}}]},
        _plan("완료", "완료"),
        {"content": {"summary": "상해입원일당(3일 초과분 x 30,000원) + 골절진단비(1년 이내 50% 감액)",
                     "test_cases": [{"name": "입원 5일 + 골절(가입 2개월)", "input": CASE, "expected_total": 310000}],
                     "limitations": ["별표 1 골절 부위별 가산 지급률 미반영 (스키마 미정의)"]}},
    ])
