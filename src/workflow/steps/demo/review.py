"""[예시] 사람 스텝 — 엔진이 여기서 '검토 대기'로 멈추고, resume() 으로 사람 입력이 들어오면 실행된다."""
from src.workflow.core import Ctx, step


@step(id="review", name="검토·승인", workflow="demo", actor="human")
def review(ctx: Ctx) -> Ctx:
    human = ctx.human("review")
    amount = human.data.get("amount", ctx.data["calculate"]["amount"])  # "수정 후 승인"이면 수정값 사용
    ctx.data["review"] = {"decision": human.decision, "amount": amount}
    return ctx
