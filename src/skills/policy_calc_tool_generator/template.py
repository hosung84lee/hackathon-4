"""보험금 계산 Tool — {product_code} {product_name}

stdin: {"claim": {...}, "judgments": {...}}  →  stdout: {"total_amount", "breakdown", "not_paid"}
표준 라이브러리만 사용한다.
"""
import json
import sys
from datetime import date


def _days_between(start, end):
    try:
        return (date.fromisoformat(end) - date.fromisoformat(start)).days
    except (TypeError, ValueError):
        return None


def _judged(judgments, code):
    """판단 결과 'Y' 여부. 결과가 없으면 None."""
    j = judgments.get(code)
    return None if j is None else str(j.get("result", "")).upper() == "Y"


# ── 보장별 계산 함수: (claim, judgments) -> (amount, formula, note) 또는 None(부지급, 사유) ──

def calc_example_coverage(claim, judgments):
    days = (claim.get("hospitalization") or {}).get("days", 0)
    if days <= 0:
        return None, "입원 없음"
    amount = 30000 * min(days, 180)
    return (amount, f"30,000원 x {min(days, 180)}일", ""), None


COVERAGES = [
    # (coverage_code, coverage_name, 함수, 면책기간일수 또는 None, 감액(기간일수, 비율) 또는 None)
    ("EXAMPLE", "예시 입원일당", calc_example_coverage, None, None),
]


def main():
    sys.stdin.reconfigure(encoding="utf-8")   # Windows 기본 인코딩(cp949) 대신 UTF-8
    sys.stdout.reconfigure(encoding="utf-8")
    payload = json.load(sys.stdin)
    claim, judgments = payload.get("claim", {}), payload.get("judgments", {})
    elapsed = _days_between((claim.get("policy") or {}).get("contract_date"), claim.get("accident_date"))
    breakdown, not_paid = [], []
    for code, name, fn, waiting, reduction in COVERAGES:
        if waiting is not None and elapsed is not None and elapsed < waiting:
            not_paid.append({"coverage_code": code, "reason": f"면책기간 {waiting}일 이내"})
            continue
        result, reason = fn(claim, judgments)
        if result is None:
            not_paid.append({"coverage_code": code, "reason": reason})
            continue
        amount, formula, note = result
        if reduction and elapsed is not None and elapsed < reduction[0]:
            amount = int(amount * reduction[1])
            formula += f" x 감액 {reduction[1]}"
        elif reduction and elapsed is None:
            note = (note + " / " if note else "") + "날짜 없음: 감액 미적용"
        breakdown.append({"coverage_code": code, "coverage_name": name, "amount": int(amount),
                          "formula": formula, "note": note})
    print(json.dumps({"total_amount": sum(b["amount"] for b in breakdown), "breakdown": breakdown,
                      "not_paid": not_paid}, ensure_ascii=False))


if __name__ == "__main__":
    main()
