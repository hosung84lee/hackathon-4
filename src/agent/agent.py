"""Agent 정의와 Agent loop (설계 6.3, 6.4).

    from src.agent.agent import Agent, run_agent

    judge = Agent(
        name="소액암 판단 Agent",
        system_prompt="너는 보험금 심사자다. ...",
        tools=[get_document_tool],
        skills=["claim_judgment_guide"],
        output_schema=JudgmentOutput,       # Pydantic 모델 또는 JSON 스키마 dict
        max_iterations=8,
    )
    result = run_agent(judge, {"item": item.model_dump(), "documents": [...]}, images=["src/data/image/a.jpg"])

    if result.status == "escalated":
        ctx.escalations.append(result.escalation)        # 다음 사람 스텝에서 검토
    elif result.status == "completed":
        ctx.data["judge_items"][item.code] = {**result.output, "observation_id": result.observation_id}
    else:  # "failed" — API 오류 등
        raise RuntimeError(result.error)

LLM 을 한 번만 부르는 작업도 tools=[] · max_iterations=1 인 Agent 로 만든다.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

import jsonschema
from pydantic import BaseModel, ValidationError

from src.agent.llm import LLMClient, get_llm_client, traced_chat
from src.agent.skills import get_skill, list_skills
from src.agent.tools import Tool
from src.backend.config import get_settings
from src.schema.common import AgentResult, Escalation, PlanItem
from src.trace.tracer import tracer

COMMON_PRINCIPLES = """\
[공통 원칙]
- 추측하지 않는다. 입력·서류·툴 결과에 근거가 없는 값은 만들어내지 않는다.
- 판단 근거와 사용한 소스(서류, 조항)를 분명히 한다."""

ESCALATE_GUIDE = """\
- 근거가 부족하거나 판단할 수 없으면 escalate 툴을 호출해 사유와 함께 사람에게 넘긴다."""

PLANNING_GUIDE = """\
[계획 수립]
- 작업을 시작하기 전에 먼저 update_plan 툴로 할 일 목록을 만든다.
- 항목을 하나씩 '진행 중'으로 바꾸고, 처리한 뒤 '완료'로 바꾼다. '진행 중'은 한 번에 1개만 둔다.
- 필요하면 항목을 추가·수정·삭제한다. 하지 않을 항목은 '건너뜀'으로 두고 사유를 적는다.
- 모든 항목이 '완료' 또는 '건너뜀'이 된 뒤에 최종 응답을 낸다. update_plan 은 매번 목록 전체를 보낸다."""

MAX_NO_PROGRESS = 3


@dataclass
class Agent:
    name: str
    system_prompt: str
    model: str | None = None  # None 이면 설정(OPENAI_MODEL)
    tools: list[Tool] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)  # src/skills 아래 스킬 이름
    output_schema: type[BaseModel] | dict[str, Any] | None = None
    max_iterations: int = 10
    planning: bool = False
    allow_escalate: bool = True  # 흐름 1 변환처럼 끝까지 진행해야 하면 False (출력의 미정의 항목 목록 사용)
    strict_schema: bool = False  # OpenAI Structured Outputs strict 모드 (스키마가 strict 규칙을 만족할 때만)
    model_params: dict[str, Any] = field(default_factory=dict)  # temperature 등

    def json_schema(self) -> dict[str, Any] | None:
        if self.output_schema is None:
            return None
        if isinstance(self.output_schema, dict):
            return self.output_schema
        return self.output_schema.model_json_schema()


# ── 공통 툴 ──────────────────────────────────────────────────────────

ESCALATE_PARAMS = {
    "type": "object",
    "properties": {
        "reason": {"type": "string", "description": "판단할 수 없는 사유"},
        "details": {"type": "object", "description": "부족한 서류, 확인이 필요한 내용 등"},
    },
    "required": ["reason"],
}

LOAD_SKILL_PARAMS = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "스킬 이름"},
        "file": {"type": "string", "description": "(선택) 스킬 폴더 안 참고 파일 경로"},
    },
    "required": ["name"],
}

UPDATE_PLAN_PARAMS = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "description": "계획 전체 목록 (매번 전체를 보낸다)",
            "items": {
                "type": "object",
                "properties": {
                    "content": {"type": "string"},
                    "status": {"type": "string", "enum": ["대기", "진행 중", "완료", "건너뜀"]},
                    "skip_reason": {"type": "string"},
                },
                "required": ["content", "status"],
            },
        }
    },
    "required": ["items"],
}


def _remaining(plan: list[PlanItem]) -> list[PlanItem]:
    return [p for p in plan if p.status in ("대기", "진행 중")]


def _validate_plan(items: list[dict[str, Any]]) -> list[PlanItem]:
    plan = [PlanItem.model_validate(i) for i in items]
    if sum(p.status == "진행 중" for p in plan) > 1:
        raise ValueError("'진행 중' 항목은 한 번에 1개만 둘 수 있습니다")
    missing = [p.content for p in plan if p.status == "건너뜀" and not (p.skip_reason or "").strip()]
    if missing:
        raise ValueError(f"'건너뜀' 항목은 사유가 필요합니다: {missing}")
    return plan


def _validate_output(agent: Agent, content: str | None) -> dict[str, Any]:
    if agent.output_schema is None:
        return {"text": content or ""}
    try:
        data = json.loads(content or "")
    except json.JSONDecodeError as e:
        raise ValueError(f"최종 응답이 JSON 이 아닙니다: {e}") from e
    if isinstance(agent.output_schema, dict):
        jsonschema.validate(data, agent.output_schema)
        return data
    return agent.output_schema.model_validate(data).model_dump(mode="json")


def _system_prompt(agent: Agent) -> str:
    parts = [agent.system_prompt.strip(), COMMON_PRINCIPLES]
    if agent.allow_escalate:
        parts[-1] += "\n" + ESCALATE_GUIDE
    if agent.skills:
        available = list_skills()
        lines = [f"- {n}: {available[n].description}" if n in available else f"- {n}: (스킬 폴더 없음)"
                 for n in agent.skills]
        parts.append("[사용 가능한 스킬]\n필요하면 load_skill 툴로 상세 지침을 불러와 따른다.\n" + "\n".join(lines))
    if agent.planning:
        parts.append(PLANNING_GUIDE)
    if agent.output_schema is not None:
        parts.append("[최종 응답]\n툴 호출 없이 최종 응답을 낼 때는 주어진 JSON 스키마에 맞는 JSON 만 출력한다.")
    return "\n\n".join(parts)


def _user_message(input: str | dict[str, Any], images: list[str], pdfs: list[str]) -> dict[str, Any]:
    text = input if isinstance(input, str) else json.dumps(input, ensure_ascii=False, indent=2)
    if not images and not pdfs:
        return {"role": "user", "content": text}
    parts: list[dict[str, Any]] = [{"type": "text", "text": text}]
    parts += [{"type": "image_path", "path": p} for p in images]
    parts += [{"type": "pdf_path", "path": p} for p in pdfs]
    return {"role": "user", "content": parts}


# ── Agent loop ───────────────────────────────────────────────────────


def run_agent(agent: Agent, input: str | dict[str, Any], images: list[str] | None = None,
              pdfs: list[str] | None = None, client: LLMClient | None = None) -> AgentResult:
    client = client or get_llm_client()
    model = agent.model or get_settings().openai_model
    tools = {t.name: t for t in agent.tools}
    builtin = []
    if agent.skills:
        builtin.append(Tool("load_skill", "스킬의 상세 지침(또는 참고 파일)을 불러온다", LOAD_SKILL_PARAMS, lambda **_: None))
    if agent.allow_escalate:
        builtin.append(Tool("escalate", "판단할 수 없을 때 사유와 함께 사람에게 넘기고 종료한다", ESCALATE_PARAMS, lambda **_: None))
    if agent.planning:
        builtin.append(Tool("update_plan", "계획을 세우거나 항목 상태를 갱신한다 (목록 전체)", UPDATE_PLAN_PARAMS, lambda **_: None))
    for b in builtin:
        if b.name in tools:
            raise ValueError(f"'{b.name}' 은 공통 툴 이름이라 쓸 수 없습니다")
        tools[b.name] = b
    openai_tools = [t.to_openai() for t in tools.values()] or None

    schema = agent.json_schema()
    response_format = ({"type": "json_schema", "json_schema": {"name": "output", "schema": schema,
                                                               "strict": agent.strict_schema}}
                       if schema is not None else None)
    messages: list[dict[str, Any]] = [{"role": "system", "content": _system_prompt(agent)},
                                      _user_message(input, images or [], pdfs or [])]

    plan: list[PlanItem] = []
    iterations = 0
    schema_retry_used = False
    no_progress = 0
    plan_at_last_nudge: list[PlanItem] | None = None

    with tracer.span(agent.name, kind="agent",
                     input={"model": model, "tools": list(tools), "skills": agent.skills, "planning": agent.planning,
                            "input": input, "images": images or [], "pdfs": pdfs or []}) as agent_obs:

        def finish(result: AgentResult) -> AgentResult:
            result.plan, result.iterations, result.observation_id = plan, iterations, agent_obs.id
            if result.status == "escalated" and result.escalation is not None:
                result.escalation.observation_id = agent_obs.id
                tracer.event("에스컬레이션", kind="escalation", output=result.escalation, level="WARNING")
                agent_obs.set_level("WARNING", result.escalation.reason)
            elif result.status == "failed":
                agent_obs.set_level("ERROR", result.error)
            agent_obs.set_output(result.model_dump(mode="json", exclude={"plan"}) | {"plan": [p.model_dump() for p in plan]})
            return result

        def escalate(reason: str, details: dict[str, Any] | None = None) -> AgentResult:
            return finish(AgentResult(status="escalated",
                                      escalation=Escalation(step_id=tracer.step_id or "", reason=reason, details=details or {})))

        while True:
            if iterations >= agent.max_iterations + (1 if schema_retry_used else 0):
                return escalate(f"max_iterations({agent.max_iterations}) 초과")
            iterations += 1
            try:
                resp = traced_chat(client, name=f"LLM 호출 #{iterations}", agent_name=agent.name, model=model,
                                   messages=messages, tools=openai_tools, response_format=response_format,
                                   params=agent.model_params)
            except Exception as e:  # noqa: BLE001 — SDK 재시도 후에도 실패
                return finish(AgentResult(status="failed", error=f"LLM 호출 실패: {type(e).__name__}: {e}"))
            messages.append(resp.to_message())

            # 1) 툴 호출 → 모두 실행하고 결과를 한꺼번에 전달
            if resp.tool_calls:
                escalation_args: dict[str, Any] | None = None
                for tc in resp.tool_calls:
                    result = _run_tool(tc.name, tc.arguments, tools, agent, plan)
                    if tc.name == "update_plan" and "plan" in result:
                        plan = result.pop("plan")
                    if tc.name == "escalate" and "error" not in result:
                        escalation_args = json.loads(tc.arguments or "{}")
                    messages.append({"role": "tool", "tool_call_id": tc.id,
                                     "content": json.dumps(result, ensure_ascii=False, default=str)})
                if escalation_args is not None:
                    return escalate(escalation_args.get("reason", ""), escalation_args.get("details"))
                continue

            # 2) 최종 응답인데 계획에 남은 항목이 있음
            remaining = _remaining(plan)
            if agent.planning and remaining:
                no_progress = no_progress + 1 if plan_at_last_nudge == plan else 1
                plan_at_last_nudge = [p.model_copy() for p in plan]
                if no_progress >= MAX_NO_PROGRESS:
                    return escalate(f"계획 진행 없음 {MAX_NO_PROGRESS}회", {"remaining": [p.content for p in remaining]})
                messages.append({"role": "user", "content": "계획에 남은 항목이 있습니다: "
                                 + ", ".join(f"[{p.status}] {p.content}" for p in remaining) + ". 계속 진행하세요."})
                continue

            # 3) 출력 스키마 검증
            try:
                output = _validate_output(agent, resp.content)
            except (ValueError, ValidationError, jsonschema.ValidationError) as e:
                msg = str(e)[:2000]
                tracer.event("출력 스키마 검증 실패", kind="validation", output={"error": msg}, level="WARNING")
                if schema_retry_used:
                    return escalate("출력 스키마 검증 2회 실패", {"error": msg})
                schema_retry_used = True
                messages.append({"role": "user", "content": f"최종 응답이 출력 스키마와 맞지 않습니다. 오류: {msg}\n스키마에 맞는 JSON 만 다시 출력하세요."})
                continue
            if agent.output_schema is not None:
                tracer.event("출력 스키마 검증 통과", kind="validation")
            return finish(AgentResult(status="completed", output=output))


def _run_tool(name: str, arguments: str, tools: dict[str, Tool], agent: Agent,
              plan: list[PlanItem]) -> dict[str, Any]:
    """툴 1개 실행. 오류는 예외 대신 {"error": ...} 로 돌려줘 LLM 이 다른 방법을 찾게 한다."""
    with tracer.span(name, kind="tool", input={"arguments": arguments}) as obs:
        try:
            if name not in tools:
                raise KeyError(f"사용할 수 없는 툴: {name}")
            t = tools[name]
            args = json.loads(arguments or "{}")
            t.validate(args)
            if name == "escalate":
                result: dict[str, Any] = {"ok": True}
            elif name == "load_skill":
                if args["name"] not in agent.skills:
                    raise KeyError(f"이 Agent 가 쓸 수 없는 스킬: {args['name']} (사용 가능: {agent.skills})")
                skill = get_skill(args["name"])
                if args.get("file"):
                    result = {"name": skill.name, "file": args["file"], "content": skill.read_file(args["file"])}
                else:
                    result = {"name": skill.name, "instructions": skill.instructions, "files": skill.files()}
                tracer.event(f"스킬 로드: {skill.name}", kind="skill", input=args)
            elif name == "update_plan":
                new_plan = _validate_plan(args["items"])
                tracer.event("계획 갱신", kind="plan", output=[p.model_dump() for p in new_plan])
                result = {"ok": True, "remaining": len(_remaining(new_plan)), "plan": new_plan}
            else:
                value = t.func(**args)
                result = value if isinstance(value, dict) else {"result": value}
        except Exception as e:  # noqa: BLE001
            obs.set_level("ERROR", f"{type(e).__name__}: {e}")
            result = {"error": f"{type(e).__name__}: {e}"}
        obs.set_output({k: v for k, v in result.items() if k != "plan"})
        return result
