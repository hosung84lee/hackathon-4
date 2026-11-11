---
name: policy_calc_tool_generator
description: 구조화된 사고보장 약관(PolicyStructure)으로 보험금 계산 프로그램(파이썬 파일 1개)을 생성하고 테스트하는 방법
---
# 보험금계산 프로그램 생성

## 목표
구조화된 약관(`structured_policy`)의 보장별 산정 방식으로, 청구정보 + AI 판단 결과를 받아 지급액을 계산하는
**파이썬 파일 1개**를 만든다. 이 파일은 흐름 2(청구 심사)에서 별도 프로세스로 실행된다.

## 반드시 지킬 규약
1. **표준 라이브러리만** 사용한다 (`json`, `sys`, `datetime`, `math` 정도). 파일·네트워크·환경변수 접근 금지.
2. 표준입력으로 JSON 1개를 받고 표준출력으로 JSON 1개만 쓴다. 디버그 출력은 표준오류로.
   `main()` 시작에서 `sys.stdin.reconfigure(encoding="utf-8")`, `sys.stdout.reconfigure(encoding="utf-8")` 를 호출한다 (Windows 대응).
3. 입력 형식은 `claim_input_example.json` 을 따른다. 없는 값은 `.get()` 으로 안전하게 읽는다.
4. 출력 형식:
   ```json
   {"total_amount": 0,
    "breakdown": [{"coverage_code": "", "coverage_name": "", "amount": 0, "formula": "", "note": ""}],
    "not_paid":  [{"coverage_code": "", "reason": ""}]}
   ```
5. 금액은 원 단위 정수. 비율 계산 후 `int()` 로 버림.
6. 약관에 없는 규칙을 만들지 않는다. 판단이 필요한 조건(`check_type: judgment`)은 입력 `judgments[judgment_code]["result"]`
   값으로만 판단한다. 판단 결과가 없으면 해당 보장은 `not_paid` 에 "판단 결과 없음" 으로 넣는다.
7. 감액(`reduction`)·면책기간(`waiting_period_days`)은 `claim.policy.contract_date` 와 `claim.accident_date` 의 차이로 계산한다.
   날짜가 없으면 적용하지 않고 `note` 에 남긴다.
8. 구조는 `template.py` 를 따른다 (보장별 함수 + COVERAGES 표 + main).

## 작업 순서 (update_plan 으로 계획을 세운다)
1. 보장마다 "보장 <코드> 계산 로직" 항목을 계획에 넣는다. 마지막에 "테스트 케이스 작성·실행" 항목.
2. `load_skill(name="policy_calc_tool_generator", file="template.py")` 로 템플릿을 읽는다.
3. 전체 코드를 `write_calc_tool` 툴로 저장한다 (항상 파일 전체를 보낸다).
4. 보장마다 지급/부지급 테스트 입력을 만들어 `run_calc_tool_test` 로 실행한다. 기대값과 다르면 코드를 고쳐 다시 저장한다.
5. 최종 응답에 계산 로직 요약, 테스트 케이스(입력·기대 지급액), 반영하지 못한 부분(limitations)을 적는다.
   구현하지 못한 규칙을 숨기지 않는다.
