"""[예시] 데모 워크플로우 — 워크플로우 파일은 스텝 순서·분기만 담당한다.

흐름 1·2 를 만들 때 이 파일과 src/workflow/steps/demo/ 를 참고한다.
"""
from src.workflow.core import Run, workflow
from src.workflow.steps.demo.calculate import calculate
from src.workflow.steps.demo.extract_text import extract_text
from src.workflow.steps.demo.finalize import finalize
from src.workflow.steps.demo.judge import judge
from src.workflow.steps.demo.review import review


@workflow(code="demo", name="데모 워크플로우")
def demo(run: Run) -> None:
    run.step(extract_text)
    if run.ctx.data["extract_text"]["length"] > 0:  # 분기: 판단할 내용이 있을 때만 Agent 실행
        run.step(judge)
    run.step(calculate)
    run.step(review)  # human → 검토 대기
    run.step(finalize)
