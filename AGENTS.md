# AGENTS.md — 공통 규칙 (Codex · Claude Code 공용)

> 규칙은 이 파일 **한 곳에만** 쓴다. `CLAUDE.md` 는 이 파일을 불러오기만 한다.
> 각 영역 폴더의 `AGENTS.md` 에는 그 영역만의 추가 규칙이 있다. 작업 폴더의 것도 함께 읽는다.

## 프로젝트
신상품 보험 AI 워크플로우 프로토타입. 전체 설계는 `docs/design.md`, 공통 인터페이스 사용법은 **`docs/interfaces.md`** 를 따른다.
실행: `streamlit run src/app/main.py` · 점검: `python -m unittest discover -s tests -t .` · 예시 흐름: `src/workflow/examples/demo.py`
- 흐름 1 `policy_structuring` — 보험약관(사고보장) 구조화
- 흐름 2 `claim_review` — 보험금 청구 심사 지원
- 기술: Streamlit(UI) · Python · SQLite · OpenAI API (Agent 직접 구현, LangChain 금지)

## 영역 소유권 (`.github/CODEOWNERS` 가 기준)
| 담당 | 영역 | 소유 폴더 |
|---|---|---|
| 팀원 A | 흐름 1 약관 구조화 | `src/workflow/policy_structuring.py`, `src/workflow/steps/policy_structuring/`, `src/skills/policy_*/`, `src/schema/policy_structuring.py`, `src/app/views/policy_structuring.py` |
| 팀원 B | 흐름 2 청구 심사 | `src/workflow/claim_review.py`, `src/workflow/steps/claim_review/`, `src/skills/claim_*/`, `src/schema/claim_review.py`, `src/app/views/claim_review.py` |
| 팀원 C | 공통 기반 | `src/agent/`, `src/trace/`, `src/backend/`, `src/mcp_server/`, 공통 `src/schema/`, `src/app/main.py`, trace 조회 화면, 루트 파일 |

## 반드시 지킬 규칙
1. **담당 영역 폴더 밖의 파일은 수정하지 않는다.** 필요하면 수정하지 말고 *무엇이 왜 필요한지* 보고한다.
2. **공통 인터페이스는 바꾸지 않는다** — ctx, `workflow_runs` 저장·재개, `@workflow`/`@step`, Agent 정의, 툴 정의, Tracer, DB 접근.
3. **라이브러리를 새로 추가하지 않는다.** (`requirements.txt` 는 팀원 C 관리, 버전 고정)
4. 요청받지 않은 리팩터링, 파일 전체 포맷 변경, 파일 이름 변경을 하지 않는다.
5. workflow 구성 규칙과 스텝 작성 규칙을 따른다 (아래).
6. Agent 는 LangChain 등 프레임워크 없이 설계(6장)대로 구현한다.
7. 커밋하기 전에 실행 확인을 한다 (최소한 `python -m unittest discover -s tests -t .` 와 화면 실행).

## workflow · 스텝 규칙
- 플로우 1개 = 워크플로우 파일 1개 (`src/workflow/<code>.py`). 워크플로우 파일은 **스텝 순서·연결·분기 조건만** 담당한다.
- 스텝은 스텝마다 별도 파일 (`src/workflow/steps/<code>/<step_id>.py`). 도구·Agent·프로그램·사람 검토 모두 스텝이다.
- 모든 스텝은 ctx 를 받아 갱신된 ctx 를 돌려준다. **스텝은 다른 스텝을 호출하지 않는다.** 분기는 워크플로우에서만.
- 워크플로우에는 `@workflow(code, name)`, 스텝에는 `@step(id, name, workflow, actor)` 를 붙인다. `actor` ∈ `tool` / `agent` / `program` / `human`.
- 스텝은 상위 워크플로우를 통해서만 실행된다 (`workflow` 값 불일치 시 실행 거부).
- ctx 에는 JSON 으로 저장 가능한 값만 담는다. 이미지·PDF 는 파일 경로만.
- LLM 을 한 번만 부르는 작업도 툴 없는 `max_iterations=1` Agent 로 만든다.
- Agent 는 **추측 금지**. 판단 불가는 `escalate`(종료형), 흐름 1 스키마 미정의 항목은 출력의 미정의 항목 목록(수집형).

## 커밋 · 브랜치
- 브랜치: `product` 에서 `feat/<policy|claim|common>-<내용>` 생성 → `develop` 에 머지해 통합 테스트 → 같은 개인 브랜치를 `product` 에 PR (squash 금지).
- **금지:** `develop → product` 머지, `develop → 개인 브랜치` 머지, force push(develop 초기화 제외), 남의 커밋 되돌리기.
- 개인 브랜치 최신화는 `product` 에서만 받는다.
- 커밋 메시지: `[영역] 내용` (예: `[claim] 이미지 분류 스텝 추가`)
- 커밋하지 않는 것: `.env`, SQLite DB 파일, 생성된 계산 Tool(`src/data/generated_tools/`).
