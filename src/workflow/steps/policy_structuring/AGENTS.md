# 흐름 1 — 약관 구조화 (팀원 A 영역)

- 스텝 파일 1개 = 스텝 1개. 파일명은 `@step` 의 `id` 와 같게 한다.
- 스텝 순서·분기는 `src/workflow/policy_structuring.py` 에서만 정한다.
- 스키마 변환 Agent(4단계)는 **멈추지 않고 끝까지 변환**한다. 표준 스키마에 없는 항목은 값을 비우고
  출력의 미정의 항목 목록(항목명 / 약관 위치 / 사유)에 넣는다. 추측해서 채우지 않는다.
- 계산 Tool 생성(5단계)은 `src/skills/policy_calc_tool_generator/` 스킬을 사용한다.
  생성 코드는 `src/data/generated_tools/` 에 저장하고 커밋하지 않는다.
- 검토·승인(6단계)은 `actor: human` 스텝. 여기서 "검토 대기"로 멈추고, 반려 시 시스템 반영 없이 종료한다.
- 흐름 1 전용 Pydantic 모델은 `src/schema/policy_structuring.py`, 화면은 `src/app/views/policy_structuring.py`.
- 흐름별 DB 테이블은 이름 앞에 `policy_structuring_` 을 붙인다.
