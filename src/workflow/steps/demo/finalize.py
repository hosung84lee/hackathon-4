"""[예시] 프로그램 스텝 — 최종 결과를 ctx.output 에 남긴다 (trace output 으로 기록됨)."""
from src.workflow.core import Ctx, step


@step(id="finalize", name="최종 반영", workflow="demo", actor="program")
def finalize(ctx: Ctx) -> Ctx:
    ctx.output = {"amount": ctx.data["review"]["amount"]}
    ctx.data["finalize"] = {"done": True}
    return ctx
