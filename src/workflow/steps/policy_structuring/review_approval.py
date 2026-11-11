"""6단계 — 담당자 검토·승인 (사람).

변환 결과, 스키마 미정의 항목, 판단 항목, 계산 Tool 을 한꺼번에 검토한다.
'수정 후 승인' 이면 HumanInput.data 의 structured_policy / judgment_items 로 덮어쓴다.
"""
from src.schema.common import JudgmentItem
from src.schema.policy_structuring import PolicyStructure
from src.workflow.core import Ctx, step


@step(id="review_approval", name="담당자 검토·승인", workflow="policy_structuring", actor="human")
def review_approval(ctx: Ctx) -> Ctx:
    human = ctx.human("review_approval")
    conv = ctx.data.get("convert_schema") or {}
    structured = conv.get("structured_policy")
    judgment_items = conv.get("judgment_items", [])
    if human.decision == "수정 후 승인":
        if "structured_policy" in human.data:
            structured = PolicyStructure.model_validate(human.data["structured_policy"]).model_dump(mode="json")
        if "judgment_items" in human.data:
            judgment_items = [JudgmentItem.model_validate(j).model_dump(mode="json")
                              for j in human.data["judgment_items"]]
    ctx.data["review_approval"] = {
        "decision": human.decision,
        "comment": human.comment,
        "reviewer": human.created_by,
        "reviewed_at": human.created_at,
        "structured_policy": structured,
        "judgment_items": judgment_items,
        "escalations_reviewed": len(ctx.escalations),
        "undefined_items_reviewed": len(conv.get("undefined_items", [])),
    }
    return ctx
