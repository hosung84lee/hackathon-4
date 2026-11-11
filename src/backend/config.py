"""설정. .env 를 읽어 Settings 로 제공한다."""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[2]  # 프로젝트 루트
SRC_DIR = ROOT_DIR / "src"
DATA_DIR = SRC_DIR / "data"
SKILLS_DIR = SRC_DIR / "skills"

load_dotenv(ROOT_DIR / ".env")


def _bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(int(default))).strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    openai_api_key: str
    openai_model: str
    use_mock_llm: bool
    db_path: Path
    calc_tool_timeout_sec: float
    llm_max_retries: int


@lru_cache
def get_settings() -> Settings:
    db_path = Path(os.getenv("DB_PATH", "src/data/db/app.db"))
    if not db_path.is_absolute():
        db_path = ROOT_DIR / db_path
    return Settings(
        openai_api_key=os.getenv("OPENAI_API_KEY", ""),
        openai_model=os.getenv("OPENAI_MODEL", "gpt-4.1"),
        use_mock_llm=_bool("USE_MOCK_LLM"),
        db_path=db_path,
        calc_tool_timeout_sec=float(os.getenv("CALC_TOOL_TIMEOUT_SEC", "10")),
        llm_max_retries=int(os.getenv("LLM_MAX_RETRIES", "2")),
    )


# 모델별 대략 단가 (USD / 1M 토큰: 입력, 출력). 표에 없는 모델은 비용 0 으로 기록한다.
MODEL_PRICES: dict[str, tuple[float, float]] = {
    "gpt-4.1": (2.0, 8.0),
    "gpt-4.1-mini": (0.4, 1.6),
    "gpt-4.1-nano": (0.1, 0.4),
    "gpt-4o": (2.5, 10.0),
    "gpt-4o-mini": (0.15, 0.6),
}


def estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    price = MODEL_PRICES.get(model)
    if price is None:
        # gpt-4.1-2025-04-14 처럼 날짜가 붙은 이름은 앞부분으로 찾는다
        price = next((p for m, p in sorted(MODEL_PRICES.items(), key=lambda x: -len(x[0])) if model.startswith(m)), None)
    if price is None:
        return 0.0
    return (prompt_tokens * price[0] + completion_tokens * price[1]) / 1_000_000
