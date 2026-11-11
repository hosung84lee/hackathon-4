"""스킬 로더 (설계 6.6).

스킬 = src/skills/<스킬명>/ 폴더 1개. 폴더 안에 SKILL.md 를 둔다.

    ---
    name: policy_calc_tool_generator
    description: 구조화된 약관으로 보험금 계산 프로그램을 생성하는 방법
    ---
    (상세 지침 — Markdown)

시스템 프롬프트에는 name 과 description 만 들어가고, Agent 가 load_skill 툴을 부르면 상세 지침과
폴더 안의 참고 파일 목록을 돌려준다. 참고 파일 내용은 load_skill(name, file="templates/x.py") 로 읽는다.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from src.backend.config import SKILLS_DIR

MAX_FILE_CHARS = 30_000


@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    instructions: str
    path: Path

    def files(self) -> list[str]:
        return sorted(p.relative_to(self.path).as_posix() for p in self.path.rglob("*")
                      if p.is_file() and p.name != "SKILL.md" and "__pycache__" not in p.parts)

    def read_file(self, rel: str) -> str:
        target = (self.path / rel).resolve()
        if self.path.resolve() not in target.parents or not target.is_file():
            raise FileNotFoundError(f"스킬 '{self.name}' 에 없는 파일: {rel}")
        text = target.read_text(encoding="utf-8")
        return text if len(text) <= MAX_FILE_CHARS else text[:MAX_FILE_CHARS] + "\n... (생략)"


def _parse(path: Path) -> Skill:
    text = (path / "SKILL.md").read_text(encoding="utf-8")
    meta: dict[str, str] = {}
    body = text
    if text.startswith("---"):
        _, front, body = text.split("---", 2)
        for line in front.strip().splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip()
    return Skill(name=meta.get("name", path.name), description=meta.get("description", ""),
                 instructions=body.strip(), path=path)


def list_skills(skills_dir: Path = SKILLS_DIR) -> dict[str, Skill]:
    if not skills_dir.is_dir():
        return {}
    found = {}
    for d in sorted(skills_dir.iterdir()):
        if d.is_dir() and (d / "SKILL.md").is_file():
            s = _parse(d)
            found[s.name] = s
    return found


def get_skill(name: str, skills_dir: Path = SKILLS_DIR) -> Skill:
    skills = list_skills(skills_dir)
    if name not in skills:
        raise KeyError(f"없는 스킬: {name} (있는 스킬: {', '.join(skills) or '없음'})")
    return skills[name]
