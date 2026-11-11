"""7단계 — 신상품 시스템 반영 (프로그램). 흐름 2 가 get_product() 로 쓴다."""
from src.backend.products import save_product
from src.schema.common import JudgmentItem, Product
from src.workflow.core import Ctx, step


@step(id="register_product", name="신상품 시스템 반영", workflow="policy_structuring", actor="program")
def register_product(ctx: Ctx) -> Ctx:
    review = ctx.data["review_approval"]
    if not review.get("structured_policy"):
        raise ValueError("구조화된 약관이 없어 시스템에 반영할 수 없습니다")
    calc = ctx.data.get("generate_calc_tool") or {}
    product = Product(
        product_code=ctx.input["product_code"],
        product_name=ctx.input["product_name"],
        structured_policy=review["structured_policy"],
        judgment_items=[JudgmentItem.model_validate(j) for j in review["judgment_items"]],
        calc_tool_path=calc.get("tool_path"),
        source_workflow_id=ctx.workflow_id,
        approved_by=review["reviewer"],
        approved_at=review["reviewed_at"],
    )
    save_product(product)
    ctx.data["register_product"] = {
        "product_code": product.product_code,
        "judgment_item_count": len(product.judgment_items),
        "calc_tool_path": product.calc_tool_path,
    }
    ctx.output = ctx.data["register_product"]
    return ctx
