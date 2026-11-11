# 흐름 2 — 청구 심사 (팀원 B 영역)

- 스텝 파일 1개 = 스텝 1개. 파일명은 `@step` 의 `id` 와 같게 한다.
- 스텝 순서·분기("AI 판단 필요 여부")는 `src/workflow/claim_review.py` 에서만 정한다.
- AI 판단은 **판단 항목마다 Agent 를 따로 실행**한다. 출력: 판단 결과 / 판단 근거 / 판단에 사용한 소스.
- 판단할 수 없으면(필요 서류 없음 등) 추측하지 말고 `escalate` 툴로 종료한다.
- 보험금 계산은 AI 판단이 끝난 뒤 **최종 입력값으로 한 번만** 한다.
  흐름 1 에서 승인된 계산 Tool 을 **별도 프로세스 + 시간 제한**으로 실행하고, 입출력은 JSON.
- 심사자 검토·승인은 `actor: human` 스텝. 승인/반려와 의견은 Score 로 남긴다.
- 흐름 2 전용 Pydantic 모델은 `src/schema/claim_review.py`, 화면은 `src/app/views/claim_review.py`.
- 흐름별 DB 테이블은 이름 앞에 `claim_review_` 을 붙인다.
