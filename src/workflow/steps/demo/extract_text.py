"""[예시] 도구 스텝 — 입력 텍스트를 정리한다."""
from src.workflow.core import Ctx, step


@step(id="extract_text", name="텍스트 추출", workflow="demo", actor="tool")
def extract_text(ctx: Ctx) -> Ctx:
    text = str(ctx.input.get("text", "")).strip()
    ctx.data["extract_text"] = {"text": text, "length": len(text)}
    return ctx
