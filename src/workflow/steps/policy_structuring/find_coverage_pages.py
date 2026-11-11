"""2단계 — 사고보장 관련 페이지 추출 (Agent).

1,000 페이지가 넘을 수 있어 키워드로 후보를 줄인 뒤, 후보를 묶음 단위로 Agent 에 보내 선별한다.
Agent 가 입력에 없는 페이지 번호를 내면 버린다 (추측 금지).
"""
import re

from src.agent.agent import Agent, run_agent
from src.schema.policy_structuring import PageSelection
from src.workflow.core import Ctx, step
from src.workflow.steps.policy_structuring._pages import load_pages

KEYWORDS = re.compile(r"상해|사고|재해|골절|화상|장해|사망|입원|수술|보험금|지급")
CHUNK_PAGES = 20
PAGE_CHARS = 1500

SYSTEM_PROMPT = """\
너는 보험약관 분석가다. 주어진 약관 페이지 중 '사고(상해)보장' 약관에 해당하는 페이지를 고른다.
- 포함: 상해·재해·사고로 인한 사망/장해/입원/수술/골절/화상 등의 보험금 지급사유, 지급금액, 지급조건, 면책(지급하지 않는 사유), 용어 정의 중 사고보장 해석에 필요한 부분.
- 제외: 질병 전용 보장, 계약 일반사항(청약 철회, 보험료 납입, 해지환급금 등), 목차·안내문.
- 입력에 있는 페이지 번호만 답한다."""

AGENT = Agent(name="사고보장 페이지 선별 Agent", system_prompt=SYSTEM_PROMPT, output_schema=PageSelection,
              max_iterations=1, allow_escalate=False)


@step(id="find_coverage_pages", name="사고보장 관련 페이지 추출", workflow="policy_structuring", actor="agent")
def find_coverage_pages(ctx: Ctx) -> Ctx:
    pages = load_pages(ctx.data["extract_text"]["pages_path"])
    candidates = [n for n, text in sorted(pages.items()) if KEYWORDS.search(text)]
    selected: set[int] = set()
    notes, dropped = [], []
    for i in range(0, len(candidates), CHUNK_PAGES):
        chunk = candidates[i:i + CHUNK_PAGES]
        result = run_agent(AGENT, {
            "product_name": ctx.input.get("product_name", ""),
            "pages": [{"page": n, "text": pages[n][:PAGE_CHARS]} for n in chunk],
        })
        if result.status == "failed":
            raise RuntimeError(result.error)
        if result.status == "escalated":
            ctx.add_escalation("find_coverage_pages", f"페이지 {chunk[0]}~{chunk[-1]} 선별 실패: {result.escalation.reason}",
                               observation_id=result.observation_id)
            continue
        picked = result.output["pages"]
        selected.update(p for p in picked if p in chunk)
        dropped += [p for p in picked if p not in chunk]
        if result.output.get("notes"):
            notes.append(result.output["notes"])
    if not selected:
        ctx.add_escalation("find_coverage_pages", "사고보장 관련 페이지를 찾지 못했습니다",
                           {"candidate_count": len(candidates)})
    ctx.data["find_coverage_pages"] = {
        "pages": sorted(selected),
        "candidate_count": len(candidates),
        "dropped_pages": dropped,  # Agent 가 입력 밖의 번호를 낸 경우 (버림)
        "notes": notes,
    }
    return ctx
