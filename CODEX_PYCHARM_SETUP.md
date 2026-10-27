# Codex + PyCharm: Terra XHigh 기본, Sol Max 조건부 검토

이 저장소에는 `.codex/config.toml`(기본 Terra XHigh),
`.codex/agents/sol_reviewer.toml`(Sol Max 검토자),
`AGENTS.md`(위임 조건)이 포함되어 있습니다.

## Windows / PyCharm
1. Node.js와 Git을 설치합니다. PyCharm에서 이 GitHub 저장소를 Clone하거나 기존 클론에서 `git pull --ff-only`을 수행합니다.
2. PyCharm 하단 **Terminal**에서 프로젝트 루트인지 확인합니다.
3. Codex CLI를 업데이트합니다. Terra 지원 최소 버전은 0.159.0이며 최신 버전을 권장합니다.
   ```powershell
   npm install -g @openai/codex@latest
   codex --version
   codex login
   codex
   ```
4. Codex에서 `/status` 및 `/model`로 기본 모델을 확인합니다.
5. 아래 프롬프트를 테스트합니다.
   - 가벼운 작업: `suite/run_suite.py 실행 경로를 코드 변경 없이 설명해줘.`
   - 위임 테스트: `오븐 열/산소 확산 모델의 수치 안정성을 검토해줘. sol_reviewer 하위 에이전트를 호출하고 결과를 합쳐서 보고해줘. 소스 수정은 하지 마.`
6. 활성화된 하위 에이전트는 `/subagents`에서 확인합니다.

## 동작 및 주의
- PyCharm 내 **터미널에서 Codex CLI**를 실행하는 경로가 확실합니다. IDE 플러그인에서 Terra를 선택할 수 있는지는 해당 확장의 최신 지원 여부를 확인해야 합니다.
- Codex는 *신뢰한 프로젝트*의 `.codex/config.toml`을 읽습니다. 다른 작업 폴더나 명시적인 `-m` CLI 옵션은 설정을 덮어쓸 수 있습니다.
- `AGENTS.md`에 따른 조건부 호출은 지시 기반입니다. Sol 자동 호출 여부는 `/subagents`로 확인합니다.
- 필요하면 해당 작업에서 명시적으로 `sol_reviewer를 호출해`라고 지시하세요.
- 모델 사용 가능 여부는 ChatGPT/Codex 계정 및 로그인 방식에 따라 다릅니다. Max가 지원되지 않으면 검토자 설정의 `model_reasoning_effort`를 지원되는 단계로 낮춥니다.
- 기존 파일이나 실행코드를 수정하지 않았습니다.

## 검토 원칙
변경 전 `git status`로 작업 상태를 확인하고, 변경 후 필요한 테스트를 수행합니다.
Sol은 기본적으로 리뷰만 제공하고 파일을 직접 바꾸지 않으며, Terra가 결과를 반영합니다.
