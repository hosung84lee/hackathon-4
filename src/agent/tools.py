"""툴 정의 (설계 6.5).

툴 = 이름 + 설명 + 파라미터 JSON 스키마 + 실행 함수. 실행 함수는 키워드 인자로 파라미터를 받아
JSON 으로 바꿀 수 있는 값을 돌려준다. 스텝에서 ctx 가 필요한 툴은 클로저로 만들면 된다.

    from src.agent.tools import Tool, tool

    @tool(name="get_document", description="서류 원문 텍스트를 조회한다",
          parameters={"type": "object", "properties": {"doc_id": {"type": "string"}}, "required": ["doc_id"]})
    def get_document(doc_id: str) -> dict:
        return {"text": ...}

    # ctx 를 쓰는 툴 (스텝 안에서 생성)
    def make_doc_tool(ctx):
        return Tool(name="get_document", description="...", parameters={...},
                    func=lambda doc_id: ctx.data["build_claim_info"]["docs"][doc_id])
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

import jsonschema


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON 스키마 (type: object)
    func: Callable[..., Any]

    def to_openai(self) -> dict[str, Any]:
        return {"type": "function", "function": {"name": self.name, "description": self.description,
                                                 "parameters": self.parameters}}

    def validate(self, args: dict[str, Any]) -> None:
        jsonschema.validate(args, self.parameters)

    def execute(self, raw_arguments: str | dict[str, Any]) -> Any:
        """인자를 파싱·검증한 뒤 실행한다. 오류는 예외로 올린다 (Agent loop 가 툴 결과로 바꿔 LLM 에 전달)."""
        args = json.loads(raw_arguments or "{}") if isinstance(raw_arguments, str) else raw_arguments
        if not isinstance(args, dict):
            raise ValueError("툴 인자는 JSON 객체여야 합니다")
        self.validate(args)
        return self.func(**args)


def tool(name: str, description: str, parameters: dict[str, Any]) -> Callable[[Callable[..., Any]], Tool]:
    """함수를 Tool 로 바꾸는 데코레이터."""

    def deco(fn: Callable[..., Any]) -> Tool:
        return Tool(name=name, description=description, parameters=parameters, func=fn)

    return deco


class ToolRegistry:
    """여러 Agent 가 같이 쓰는 툴을 이름으로 등록·조회한다."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, t: Tool) -> Tool:
        if t.name in self._tools and self._tools[t.name] is not t:
            raise ValueError(f"툴 이름 중복: {t.name}")
        self._tools[t.name] = t
        return t

    def get(self, name: str) -> Tool:
        if name not in self._tools:
            raise KeyError(f"등록되지 않은 툴: {name}")
        return self._tools[name]

    def names(self) -> list[str]:
        return list(self._tools)


registry = ToolRegistry()
