"""3단계 — 사고보장 페이지만으로 PDF 재생성 (도구: PyMuPDF)."""
import fitz

from src.backend.storage import abs_path, rel_path
from src.workflow.core import Ctx, step
from src.workflow.steps.policy_structuring._pages import run_dir


@step(id="rebuild_pdf", name="사고보장 PDF 재생성", workflow="policy_structuring", actor="tool")
def rebuild_pdf(ctx: Ctx) -> Ctx:
    pages = ctx.data["find_coverage_pages"]["pages"]
    out = run_dir(ctx.workflow_id) / "coverage_pages.pdf"
    with fitz.open(abs_path(ctx.input["pdf_path"])) as src, fitz.open() as dst:
        for n in pages:
            dst.insert_pdf(src, from_page=n - 1, to_page=n - 1)
        dst.save(out)
    ctx.data["rebuild_pdf"] = {"pdf_path": rel_path(out), "pages": pages}
    return ctx
