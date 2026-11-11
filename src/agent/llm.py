"""LLM Client (설계 6.2) — OpenAI 호출 래퍼 + 가짜 Client.

메시지는 OpenAI chat 형식을 쓰되, 이미지·PDF 는 경로로 넣는다. 전송 직전에만 base64 로 바꾸므로
trace 에는 경로만 남는다.
    {"type": "image_path", "path": "src/data/image/abc_진단서.jpg"}
    {"type": "pdf_path",   "path": "src/data/pdf/abc_약관.pdf"}

가짜 Client (USE_MOCK_LLM=1)
    - set_mock_responses("판단 Agent", [응답1, 응답2, ...]) 로 Agent 별 응답 순서를 정한다.
      응답: {"content": str | dict, "tool_calls": [{"name": "...", "arguments": {...}}]}
            또는 callable(messages, tools) -> 위 형식
    - 정해진 응답이 없으면 output_schema 에 맞는 기본값 JSON 을 만들어 돌려준다.
"""
from __future__ import annotations

import base64
import json
import mimetypes
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from src.backend.config import get_settings
from src.backend.storage import abs_path
from src.trace.tracer import tracer


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str  # JSON 문자열


@dataclass
class LLMResponse:
    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0

    def to_message(self) -> dict[str, Any]:
        """대화에 추가할 assistant 메시지."""
        msg: dict[str, Any] = {"role": "assistant", "content": self.content}
        if self.tool_calls:
            msg["tool_calls"] = [{"id": tc.id, "type": "function",
                                  "function": {"name": tc.name, "arguments": tc.arguments}} for tc in self.tool_calls]
        return msg


class LLMClient(Protocol):
    def chat(self, *, agent_name: str, model: str, messages: list[dict[str, Any]],
             tools: list[dict[str, Any]] | None, response_format: dict[str, Any] | None,
             params: dict[str, Any]) -> LLMResponse: ...


# ── OpenAI ──────────────────────────────────────────────────────────


def _data_url(path: str, default_mime: str) -> tuple[str, str]:
    p = abs_path(path)
    mime = mimetypes.guess_type(p.name)[0] or default_mime
    return f"data:{mime};base64,{base64.b64encode(p.read_bytes()).decode()}", p.name


def materialize(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """경로로 넣은 이미지·PDF 를 OpenAI 형식으로 바꾼다."""
    out = []
    for m in messages:
        content = m.get("content")
        if isinstance(content, list):
            parts = []
            for part in content:
                if part.get("type") == "image_path":
                    url, _ = _data_url(part["path"], "image/png")
                    parts.append({"type": "image_url", "image_url": {"url": url}})
                elif part.get("type") == "pdf_path":
                    url, name = _data_url(part["path"], "application/pdf")
                    parts.append({"type": "file", "file": {"filename": name, "file_data": url}})
                else:
                    parts.append(part)
            m = {**m, "content": parts}
        out.append(m)
    return out


class OpenAIClient:
    def __init__(self) -> None:
        from openai import OpenAI

        s = get_settings()
        if not s.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY 가 없습니다. .env 를 확인하거나 USE_MOCK_LLM=1 로 실행하세요.")
        self._client = OpenAI(api_key=s.openai_api_key, max_retries=s.llm_max_retries)  # API 오류 재시도는 SDK 가 처리

    def chat(self, *, agent_name: str, model: str, messages: list[dict[str, Any]],
             tools: list[dict[str, Any]] | None, response_format: dict[str, Any] | None,
             params: dict[str, Any]) -> LLMResponse:
        kwargs: dict[str, Any] = {"model": model, "messages": materialize(messages), **params}
        if tools:
            kwargs["tools"] = tools
        if response_format:
            kwargs["response_format"] = response_format
        resp = self._client.chat.completions.create(**kwargs)
        msg = resp.choices[0].message
        return LLMResponse(
            content=msg.content,
            tool_calls=[ToolCall(tc.id, tc.function.name, tc.function.arguments) for tc in (msg.tool_calls or [])],
            model=resp.model,
            prompt_tokens=resp.usage.prompt_tokens if resp.usage else 0,
            completion_tokens=resp.usage.completion_tokens if resp.usage else 0,
        )


# ── 가짜 Client ──────────────────────────────────────────────────────

MockResponse = dict[str, Any] | Callable[[list[dict[str, Any]], list[dict[str, Any]] | None], dict[str, Any]]
_mock_scripts: dict[str, list[MockResponse]] = {}


def set_mock_responses(agent_name: str, responses: list[MockResponse]) -> None:
    """가짜 Client 가 agent_name 호출에 차례대로 돌려줄 응답을 정한다."""
    _mock_scripts[agent_name] = list(responses)


def clear_mock_responses() -> None:
    _mock_scripts.clear()


def fake_from_schema(schema: dict[str, Any], root: dict[str, Any] | None = None) -> Any:
    """JSON 스키마에 맞는 기본값을 만든다 (가짜 응답용)."""
    root = root or schema
    if "$ref" in schema:
        node: Any = root
        for key in schema["$ref"].lstrip("#/").split("/"):
            node = node[key]
        return fake_from_schema(node, root)
    if "const" in schema:
        return schema["const"]
    if "enum" in schema:
        return schema["enum"][0]
    if "default" in schema:
        return schema["default"]
    for key in ("anyOf", "oneOf", "allOf"):
        if key in schema:
            options = [o for o in schema[key] if o.get("type") != "null"] or schema[key]
            return fake_from_schema(options[0], root)
    t = schema.get("type")
    if isinstance(t, list):
        t = next((x for x in t if x != "null"), "null")
    if t == "object" or "properties" in schema:
        return {k: fake_from_schema(v, root) for k, v in schema.get("properties", {}).items()}
    if t == "array":
        return [fake_from_schema(schema["items"], root)] if "items" in schema else []
    return {"string": "mock", "integer": 0, "number": 0, "boolean": False, "null": None}.get(t or "string", "mock")


class MockLLMClient:
    def chat(self, *, agent_name: str, model: str, messages: list[dict[str, Any]],
             tools: list[dict[str, Any]] | None, response_format: dict[str, Any] | None,
             params: dict[str, Any]) -> LLMResponse:
        script = _mock_scripts.get(agent_name)
        if script:
            item = script.pop(0)
            spec = item(messages, tools) if callable(item) else item
        elif response_format and response_format.get("type") == "json_schema":
            spec = {"content": fake_from_schema(response_format["json_schema"]["schema"])}
        else:
            spec = {"content": "mock 응답"}
        content = spec.get("content")
        if isinstance(content, (dict, list)):
            content = json.dumps(content, ensure_ascii=False)
        calls = [ToolCall(id=f"call_{uuid.uuid4().hex[:8]}", name=c["name"],
                          arguments=c["arguments"] if isinstance(c.get("arguments"), str)
                          else json.dumps(c.get("arguments", {}), ensure_ascii=False))
                 for c in spec.get("tool_calls", [])]
        return LLMResponse(content=content if not calls else spec.get("content"), tool_calls=calls,
                           model=f"mock:{model}", prompt_tokens=0, completion_tokens=0)


# ── 공통 ─────────────────────────────────────────────────────────────

_client: LLMClient | None = None


def get_llm_client() -> LLMClient:
    global _client
    if _client is None:
        _client = MockLLMClient() if get_settings().use_mock_llm else OpenAIClient()
    return _client


def set_llm_client(client: LLMClient | None) -> None:
    """테스트·화면에서 Client 를 바꿀 때 사용 (None 이면 설정대로 다시 만든다)."""
    global _client
    _client = client


def traced_chat(client: LLMClient, *, name: str, agent_name: str, model: str, messages: list[dict[str, Any]],
                tools: list[dict[str, Any]] | None = None, response_format: dict[str, Any] | None = None,
                params: dict[str, Any] | None = None) -> LLMResponse:
    """LLM 호출 1회를 Generation 으로 기록하면서 호출한다."""
    params = params or {}
    with tracer.generation(name, model=model, model_params=params,
                           input={"messages": messages, "tools": [t["function"]["name"] for t in tools or []],
                                  "response_format": response_format}) as gen:
        resp = client.chat(agent_name=agent_name, model=model, messages=messages, tools=tools,
                           response_format=response_format, params=params)
        gen.set_usage(resp.model or model, resp.prompt_tokens, resp.completion_tokens)
        gen.set_output({"content": resp.content,
                        "tool_calls": [{"name": t.name, "arguments": t.arguments} for t in resp.tool_calls]})
    return resp
