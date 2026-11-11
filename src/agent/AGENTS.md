# 공통 기반 — Agent (팀원 C 영역)

- 이 폴더의 공개 인터페이스(Agent 정의, 툴 정의, Agent loop 반환값)는 고정 후 **팀 합의 없이 바꾸지 않는다.**
- OpenAI 함수 호출 + Structured Outputs 사용. LangChain 등 프레임워크 금지.
- 모든 LLM 호출·툴 실행은 Tracer 를 통해 자동으로 `workflow_id` + `step_id` 로 기록되어야 한다.
- 공통 툴: `load_skill`, `escalate`, (planning 시) `update_plan`.
- `USE_MOCK_LLM=1` 이면 가짜 Client 로 동작해야 한다 (A·B 가 기반 완성 전에 개발할 수 있도록).
