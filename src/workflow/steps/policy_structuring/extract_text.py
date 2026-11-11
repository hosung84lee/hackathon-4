"""1단계 — 보험약관 PDF 텍스트 추출 (도구: PyMuPDF)."""
import fitz

from src.backend.storage import abs_path
from src.workflow.core import Ctx, step
from src.workflow.steps.policy_structuring._pages import save_pages


@step(id="extract_text", name="약관 PDF 텍스트 추출", workflow="policy_structuring", actor="tool")
def extract_text(ctx: Ctx) -> Ctx:
    pdf_path = ctx.input["pdf_path"]
    with fitz.open(abs_path(pdf_path)) as doc:
        pages = [{"page": i + 1, "text": page.get_text()} for i, page in enumerate(doc)]
    empty = [p["page"] for p in pages if not p["text"].strip()]
    ctx.data["extract_text"] = {
        "pages_path": save_pages(ctx.workflow_id, pages),
        "page_count": len(pages),
        "char_count": sum(len(p["text"]) for p in pages),
        "empty_pages": empty,  # 스캔 이미지 페이지 등 (텍스트 없음)
    }
    return ctx
