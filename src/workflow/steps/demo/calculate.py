"""[예시] 프로그램 스텝 — 판단 결과로 금액을 계산한다."""
from src.workflow.core import Ctx, step


@step(id="calculate", name="금액 계산", workflow="demo", actor="program")
def calculate(ctx: Ctx) -> Ctx:
    judged = (ctx.data.get("judge") or {}).get("output") or {}
    amount = 1_000_000 if judged.get("result") == "Y" else 0
    ctx.data["calculate"] = {"amount": amount}
    return ctx
