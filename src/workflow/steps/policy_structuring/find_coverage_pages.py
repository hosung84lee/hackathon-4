"""2단계 — 보장(보험사고) 관련 페이지 추출 (Agent).

1,000 페이지가 넘을 수 있어 키워드로 후보를 줄인 뒤, 후보를 묶음 단위로 Agent 에 보내 선별한다.
묶음들은 동시에 처리한다 (trace 는 contextvar 를 복사해 같은 스텝 아래에 기록된다).
Agent 가 입력에 없는 페이지 번호를 내면 버린다 (추측 금지).
"""
import contextvars
import re
from concurrent.futures import ThreadPoolExecutor

from src.agent.agent import Agent, run_agent
from src.schema.policy_structuring import PageSelection
from src.workflow.core import Ctx, step
from src.workflow.steps.policy_structuring._pages import load_pages

KEYWORDS = re.compile(r"보험금|지급사유|지급하지|보장|진단|암|상해|사고|재해|골절|장해|사망|입원|수술|치료|정의|별표")
CHUNK_PAGES = 20
PAGE_CHARS = 1500
MAX_WORKERS = 6
SCANNED_RATIO = 0.5  # 텍스트 없는 페이지가 이 비율 이상이면 스캔 PDF 로 본다

SYSTEM_PROMPT = """\
너는 보험약관 분석가다. 주어진 약관 페이지 중 '보장(보험사고) 약관', 즉 보험금 지급과 관련된 페이지를 고른다.
주계약과 모든 특약을 대상으로 한다.
- 포함: 보험금의 지급사유, 보험금 지급에 관한 세부규정, 보험금 지급금액(표), 보험금을 지급하지 않는 사유,
  보장 해석에 필요한 용어 정의(예: 암, 소액암, 상해, 입원의 정의), 지급에 쓰이는 별표(질병·상해 분류표, 장해분류표 등).
- 제외: 약관 이용가이드, 약관 요약서, 계약 일반사항(청약 철회, 계약 성립·해지, 보험료 납입, 해지환급금, 특별계정 등),
  보험금 청구 서류·절차 안내, 목차·안내문.
- 조항이나 표가 다음 페이지로 이어지면 이어지는 페이지도 포함한다.
- 입력에 있는 페이지 번호만 답한다."""

AGENT = Agent(name="사고보장 페이지 선별 Agent", system_prompt=SYSTEM_PROMPT, output_schema=PageSelection,
              max_iterations=1, allow_escalate=False)


def _select(product_name: str, chunk: list[int], pages: dict[int, str]):
    return run_agent(AGENT, {
        "product_name": product_name,
        "pages": [{"page": n, "text": pages[n][:PAGE_CHARS]} for n in chunk],
    })


@step(id="find_coverage_pages", name="사고보장 관련 페이지 추출", workflow="policy_structuring", actor="agent")
def find_coverage_pages(ctx: Ctx) -> Ctx:
    extracted = ctx.data["extract_text"]
    pages = load_pages(extracted["pages_path"])
    candidates = [n for n, text in sorted(pages.items()) if KEYWORDS.search(text)]
    chunks = [candidates[i:i + CHUNK_PAGES] for i in range(0, len(candidates), CHUNK_PAGES)]

    product_name = ctx.input.get("product_name", "")
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = [pool.submit(contextvars.copy_context().run, _select, product_name, chunk, pages) for chunk in chunks]
        results = [f.result() for f in futures]

    selected: set[int] = set()
    notes, dropped, failed = [], [], []
    for chunk, result in zip(chunks, results):
        if result.status != "completed":
            reason = result.error if result.status == "failed" else result.escalation.reason
            failed.append({"pages": f"{chunk[0]}~{chunk[-1]}", "reason": reason})
            continue
        picked = result.output["pages"]
        selected.update(p for p in picked if p in chunk)
        dropped += [p for p in picked if p not in chunk]
        if result.output.get("notes"):
            notes.append(f"p.{chunk[0]}~{chunk[-1]}: {result.output['notes']}")

    if chunks and len(failed) == len(chunks):  # 전부 실패 (API 키 없음 등) → 실패로 처리해 재시도할 수 있게
        raise RuntimeError(f"페이지 선별 Agent 호출이 모두 실패했습니다: {failed[0]['reason']}")
    for f in failed:
        ctx.add_escalation("find_coverage_pages", f"페이지 {f['pages']} 선별 실패: {f['reason']}")

    if not selected:
        empty_ratio = len(extracted["empty_pages"]) / max(extracted["page_count"], 1)
        if empty_ratio >= SCANNED_RATIO:
            reason = (f"텍스트가 없는 페이지가 {empty_ratio:.0%} 입니다. 스캔 이미지 PDF 로 보이며 OCR 이 필요합니다.")
        else:
            reason = "사고보장 관련 페이지를 찾지 못했습니다"
        ctx.add_escalation("find_coverage_pages", reason, {"candidate_count": len(candidates)})

    ctx.data["find_coverage_pages"] = {
        "pages": sorted(selected),
        "candidate_count": len(candidates),
        "chunk_count": len(chunks),
        "dropped_pages": dropped,  # Agent 가 입력 밖의 번호를 낸 경우 (버림)
        "failed_chunks": failed,
        "notes": notes,
    }
    return ctx
