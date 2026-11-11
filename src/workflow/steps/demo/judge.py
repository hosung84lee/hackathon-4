"""[예시] Agent 스텝 — 판단 항목마다 Agent 를 따로 실행하고, 판단 불가는 에스컬레이션으로 모은다."""
from pydantic import BaseModel, Field

from src.agent.agent import Agent, run_agent
from src.agent.tools import Tool
from src.workflow.core import Ctx, step


class JudgeOutput(BaseModel):
    result: str = Field(description="판단 결과 (Y/N)")
    reason: str = Field(description="판단 근거")
    sources: list[str] = Field(default_factory=list, description="판단에 사용한 소스")


@step(id="judge", name="AI 판단", workflow="demo", actor="agent")
def judge(ctx: Ctx) -> Ctx:
    text = ctx.data["extract_text"]["text"]
    get_text = Tool(  # ctx 를 쓰는 툴은 스텝 안에서 클로저로 만든다
        name="get_text", description="판단 대상 원문을 조회한다",
        parameters={"type": "object", "properties": {}}, func=lambda: {"text": text},
    )
    agent = Agent(
        name="데모 판단 Agent",
        system_prompt="원문에 '암'이라는 단어가 있는지 판단한다. get_text 로 원문을 확인한다.",
        tools=[get_text],
        output_schema=JudgeOutput,
        max_iterations=5,
    )
    result = run_agent(agent, {"question": "원문에 '암'이 있는가?"})
    if result.status == "escalated":
        ctx.escalations.append(result.escalation)
    elif result.status == "failed":
        raise RuntimeError(result.error)
    ctx.data["judge"] = {"status": result.status, "output": result.output, "observation_id": result.observation_id}
    return ctx
