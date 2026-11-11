# 개발 환경 설정 (팀원 공통, 처음 1회)

## 1. clone · 의존성
```sh
git clone https://github.com/hosung84lee/hackathon-4.git
cd hackathon-4            # 기본 브랜치는 product (GitHub 설정)
python -m venv .venv      # 또는: uv venv --python 3.12 .venv && uv pip install -r requirements.txt
.venv\Scripts\activate    # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # OPENAI_API_KEY 입력
```

## 2. Git 검사 장치 켜기 (필수)
`.github/CODEOWNERS` 에 적힌 **본인 토큰**으로 등록한다.
```sh
sh scripts/setup-hooks.sh @hosung84lee   # 팀원 A
sh scripts/setup-hooks.sh @OWNER_B   # 팀원 B
sh scripts/setup-hooks.sh @OWNER_C   # 팀원 C
```
- `pre-commit`: 내 소유가 아닌 파일이 커밋에 있으면 거부. `main`/`product` 직접 커밋 거부.
  `develop` 은 검사 안 함. 머지 커밋은 product 에서 받는 경우만 허용.
- `pre-push`: `main`/`product` 로 직접 push 거부, force push 거부 (develop 초기화만 예외).
- Windows 에서도 Git for Windows 의 sh 로 동작한다.

## 3. 작업 흐름
```sh
git switch product && git pull
git switch -c feat/claim-image-classify      # feat/<policy|claim|common>-<내용>
# ... 작업 · 실행 확인 · 커밋 ([claim] 이미지 분류 스텝 추가)
git push -u origin feat/claim-image-classify

# 통합 테스트: develop 에 머지 (충돌은 develop 머지 커밋 안에서만 해결)
git switch develop && git pull && git merge feat/claim-image-classify && git push

# 테스트 통과 후: GitHub 에서 feat/... → product PR (squash 금지, "Create a merge commit")
```

## 4. Claude Code 권한 설정 (개인, 커밋 안 함)
`.claude/settings.local.json` 에 담당 영역 밖 편집을 막는다. 예: 팀원 B
```json
{
  "permissions": {
    "deny": [
      "Edit(/src/agent/**)", "Edit(/src/trace/**)", "Edit(/src/backend/**)",
      "Edit(/src/mcp_server/**)", "Edit(/src/app/main.py)",
      "Edit(/src/workflow/policy_structuring.py)",
      "Edit(/src/workflow/steps/policy_structuring/**)",
      "Edit(/src/skills/policy_*/**)",
      "Edit(/requirements.txt)", "Edit(/AGENTS.md)", "Edit(/.github/**)", "Edit(/.githooks/**)"
    ]
  }
}
```

## 5. GitHub 저장소 설정 (팀원 C, 1회)
- Settings → General → Default branch 를 `product` 로 변경.
- Settings → General → Pull Requests: "Allow squash merging" 끄기 (squash 금지 규칙).
