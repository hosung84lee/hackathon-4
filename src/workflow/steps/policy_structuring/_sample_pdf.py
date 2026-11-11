"""데모·테스트용 샘플 약관 PDF 생성 (스텝 아님). 실제 약관이 아니라 구조를 흉내 낸 예시다."""
from __future__ import annotations

from pathlib import Path

import fitz

SAMPLE_PAGES = [
    "무배당 안심상해보험 약관\n\n목차\n제1관 계약의 성립\n제2관 보험금의 지급\n제3관 보험료의 납입",
    "제1관 계약의 성립\n\n제1조 (보험계약의 성립)\n계약은 보험계약자의 청약과 회사의 승낙으로 이루어집니다.\n"
    "제2조 (청약의 철회)\n보험계약자는 보험증권을 받은 날부터 15일 이내에 청약을 철회할 수 있습니다.",
    "제2관 보험금의 지급\n\n제3조 (상해입원일당 보험금의 지급사유)\n"
    "회사는 피보험자가 보험기간 중 상해사고로 그 치료를 목적으로 4일 이상 계속하여 입원한 경우\n"
    "3일을 초과하는 입원일수 1일당 30,000원을 상해입원일당으로 지급합니다. (최대 180일 한도)",
    "제4조 (골절진단비 보험금의 지급사유)\n"
    "회사는 피보험자가 보험기간 중 상해사고로 골절로 진단 확정된 경우 사고 1회당 500,000원을 지급합니다.\n"
    "골절 여부는 X-ray 또는 CT 판독 결과로 확인하며, 치아의 파절은 제외합니다.\n"
    "다만, 계약일부터 1년 이내에 발생한 사고는 50%를 지급합니다.",
    "제5조 (보험금을 지급하지 않는 사유)\n"
    "1. 피보험자가 고의로 자신을 해친 경우\n2. 전쟁, 외국의 무력행사, 혁명, 내란\n"
    "3. 피보험자의 음주운전 중 발생한 사고\n\n[별표 1] 골절 부위별 가산 지급률표 (생략)",
    "제3관 보험료의 납입\n\n제6조 (보험료의 납입)\n보험계약자는 제2회 이후 보험료를 납입기일까지 납입하여야 합니다.",
]


def make_sample_policy_pdf(path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with fitz.open() as doc:
        for text in SAMPLE_PAGES:
            page = doc.new_page()
            page.insert_textbox(fitz.Rect(50, 50, 545, 800), text, fontname="korea", fontsize=11)
        doc.save(path)
    return path
