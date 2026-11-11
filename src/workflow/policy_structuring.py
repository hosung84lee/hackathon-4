"""흐름 1 — 보험약관(사고보장) 구조화. 스텝 순서·분기만 담당한다 (설계 3.3).

시작 입력: {"pdf_path": "src/data/pdf/....pdf", "product_code": "...", "product_name": "..."}
"""
from src.workflow.core import Run, workflow
from src.workflow.steps.policy_structuring.convert_schema import convert_schema
from src.workflow.steps.policy_structuring.extract_text import extract_text
from src.workflow.steps.policy_structuring.find_coverage_pages import find_coverage_pages
from src.workflow.steps.policy_structuring.generate_calc_tool import generate_calc_tool
from src.workflow.steps.policy_structuring.rebuild_pdf import rebuild_pdf
from src.workflow.steps.policy_structuring.register_product import register_product
from src.workflow.steps.policy_structuring.review_approval import review_approval


@workflow(code="policy_structuring", name="보험약관 구조화")
def policy_structuring(run: Run) -> None:
    run.step(extract_text)
    run.step(find_coverage_pages)
    if run.ctx.data["find_coverage_pages"]["pages"]:            # 사고보장 페이지가 있을 때만 변환
        run.step(rebuild_pdf)
        run.step(convert_schema)
        if run.ctx.data["convert_schema"]["complete"]:           # 변환이 끝까지 됐을 때만 계산 Tool 생성
            run.step(generate_calc_tool)
    run.step(review_approval)                                   # 사람: 검토 대기 (반려 시 여기서 종료)
    if run.ctx.data["review_approval"]["structured_policy"]:    # 반영할 구조화 결과가 있을 때만
        run.step(register_product)
