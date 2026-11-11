# 공통 인터페이스 (고정) — 팀원 A·B 용 안내

> 설계 8.2 의 공통 인터페이스. **바꿔야 하면 팀 전체에 알리고 합의한 뒤 팀원 C 가 바꾼다.**
> 실제 동작 예시: `src/workflow/examples/demo.py` + `src/workflow/steps/demo/` (그대로 복사해서 시작하면 된다)

## 0. 실행

```sh
uv venv --python 3.12 .venv && uv pip install -r requirements.txt     # 또는 python -m venv / pip
streamlit run src/app/main.py                                         # 화면
python -m unittest discover -s tests -t .                             # 공통 기반 점검
```
- `.env` 에 `USE_MOCK_LLM=1` 이면 OpenAI 없이 가짜 Client 로 돈다.
- import 는 항상 `from src.xxx import ...` (프로젝트 루트 기준).

## 1. 컨텍스트 `Ctx` — `src/workflow/core.py`

| 필드 | 용도 |
|---|---|
| `workflow_id` / `workflow_code` | 실행 건 ID (`claim_review_000001`) / 워크플로우 코드 |
| `input` | `engine.start()` 에 넘긴 입력 |
| `data` | **스텝 결과. `ctx.data[<스텝 id>] = {...}` 로 쓴다** |
| `escalations` | Agent 에스컬레이션 목록 (`ctx.escalations.append(result.escalation)`) |
| `human_inputs` | 사람 스텝 id → `HumanInput`. 사람 스텝 안에서 `ctx.human("<스텝 id>")` |
| `completed_steps` | 엔진이 관리 (건드리지 않음) |
| `output` | 워크플로우 최종 결과 (trace output 으로 남음) |

- JSON 으로 저장할 수 있는 값만. 파일은 `src.backend.storage.save_file()` 이 돌려준 **경로**만 담는다.

## 2. `@workflow` / `@step` — `src/workflow/core.py`

```python
@workflow(code="claim_review", name="보험금 청구 심사 지원")
def claim_review(run: Run) -> None:
    run.step(upload_documents)
    run.step(classify_images)
    if run.ctx.data["identify_judgments"]["items"]:   # 분기는 여기서만, ctx 값으로만
        run.step(judge_items)
    run.step(review_approval)                         # actor="human" → 검토 대기로 멈춤
    run.step(pay_claim)

@step(id="classify_images", name="이미지 분류 및 태깅", workflow="claim_review", actor="agent")
def classify_images(ctx: Ctx) -> Ctx:
    ...
    return ctx
```
- `actor`: `tool` / `agent` / `program` / `human`.
- 스텝을 직접 호출하거나 다른 워크플로우의 스텝을 `run.step()` 하면 `StepExecutionError`.
- 워크플로우 파일은 `src/workflow/<code>.py` 에 두면 엔진이 자동으로 찾는다.

## 3. 실행·멈춤·재개 — `src/workflow/engine.py`

```python
r = engine.start("claim_review", input={...},
                 human_inputs={"upload_documents": HumanInput(decision="제출", created_by="고객")})
engine.list_runs(status=RunStatus.WAITING_REVIEW, workflow_code="claim_review")   # 검토 화면 목록
engine.get_run(workflow_id)              # RunResult(workflow_id, status, current_step, ctx, error)
engine.resume(workflow_id, HumanInput(decision="승인" | "반려" | "수정 후 승인", comment=..., data={...}, created_by=...))
engine.retry(workflow_id)                # 실패 건 재실행
```
| 상황 | 결과 |
|---|---|
| 사람 스텝 도달 (입력 없음) | `WAITING_REVIEW` 저장 후 반환 |
| `resume()` | 사람 스텝을 **실행**(ctx.human() 으로 입력 확인)한 뒤 다음 스텝부터 진행. 이미 끝난 스텝은 건너뜀 |
| 결정이 `반려` | 사람 스텝 실행 후 `REJECTED` 로 종료 |
| 스텝 예외 | `FAILED`, 마지막 성공 스텝까지의 ctx 보존 |

- 사람 스텝의 결정(제출 제외)은 엔진이 **스텝 단위 Score** 로 자동 기록한다.
- 판단 항목별 승인/반려는 사람 스텝 안에서 `tracer.score(name=..., value=..., observation_id=<Agent observation_id>)`.

## 4. Agent — `src/agent/agent.py`

```python
agent = Agent(name="소액암 판단 Agent", system_prompt="...", tools=[...], skills=["claim_judgment_guide"],
              output_schema=MyPydanticModel,   # 또는 JSON 스키마 dict (판단 항목의 outputSchema)
              max_iterations=8, planning=False, allow_escalate=True, model=None)
result = run_agent(agent, input_dict_or_text, images=["src/data/image/..."], pdfs=["src/data/pdf/..."])
```
`AgentResult`: `status` (`completed` / `escalated` / `failed`), `output`, `escalation`, `error`, `plan`, `iterations`, `observation_id`

- 공통 툴 자동 추가: `escalate`(allow_escalate), `load_skill`(skills 있을 때), `update_plan`(planning).
- 흐름 1 변환 Agent 는 `allow_escalate=False` + 출력 스키마에 미정의 항목 목록 필드.
- LLM 1회 작업 = `tools=[]`, `max_iterations=1`.
- trace(LLM 호출·툴·스킬·계획·에스컬레이션)는 자동 기록 — 로그 코드 불필요.

## 5. 툴 — `src/agent/tools.py`

```python
Tool(name="get_document", description="...", parameters={JSON 스키마}, func=lambda doc_id: {...})
```
- 인자는 스키마로 검증된 뒤 키워드 인자로 들어온다. 반환은 dict 권장. 예외는 LLM 에 오류로 전달된다.

## 6. 스킬 — `src/skills/<이름>/SKILL.md`

```
---
name: policy_calc_tool_generator
description: 한 줄 설명 (시스템 프롬프트에 들어감)
---
상세 지침 (load_skill 로 불러옴). 같은 폴더의 참고 파일은 load_skill(name, file=...) 로 읽음.
```
- 폴더 이름: 흐름 1 `policy_*`, 흐름 2 `claim_*` (소유권 검사 기준).

## 7. 가짜 LLM — `src/agent/llm.py`

```python
from src.agent.llm import set_mock_responses
set_mock_responses("소액암 판단 Agent", [
    {"tool_calls": [{"name": "get_document", "arguments": {"doc_id": "d1"}}]},
    {"content": {"result": "Y", "reason": "...", "sources": ["판독지"]}},
])
```
정해진 응답이 없으면 output_schema 에 맞는 기본값을 돌려준다.

## 8. 상품 (흐름 1 → 흐름 2) — `src/backend/products.py`, `src/schema/common.py`

```python
save_product(Product(product_code=..., product_name=..., structured_policy={...},
                     judgment_items=[JudgmentItem(code=..., name=..., description=..., clauseRef=..., outputSchema={...})],
                     calc_tool_path="src/data/generated_tools/....py", source_workflow_id=ctx.workflow_id))
get_product(code) / list_products()
```

## 9. 계산 Tool 실행 — `src/backend/calc_runner.py`

```python
run_calc_tool(product.calc_tool_path, {"claim": {...}, "judgments": {"MINOR_CANCER_YN": {...}}})  # → dict
```
- 계산 Tool 규약: 파이썬 파일 1개, 표준 라이브러리만, stdin JSON → stdout JSON. 별도 프로세스 + 시간 제한(`CALC_TOOL_TIMEOUT_SEC`).

## 10. DB · 파일 — `src/backend/db.py`, `src/backend/storage.py`

```python
ensure_tables("CREATE TABLE IF NOT EXISTS claim_review_documents (...)")   # 흐름 테이블은 흐름 코드 접두어
with transaction() as conn: conn.execute(...)
connect().execute("SELECT ...").fetchall()
path = save_file(uploaded.getvalue(), uploaded.name, "image")   # → "src/data/image/xxxx_진단서.jpg"
```

## 11. Tracer — `src/trace/tracer.py`
엔진·Agent 가 자동 기록한다. 직접 남길 때만: `tracer.span(...)`, `tracer.event(...)`, `tracer.score(...)`.
조회 화면: 앱 메뉴 "실행 기록 (Trace)".
