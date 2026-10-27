# Codex + PyCharm: Terra Medium 기본, Sol Max 조건부 검토

이 저장소에는 `.codex/config.toml`(기본 Terra Medium),
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
- 모델 사용 가능 여부는 계정과 로그인 방식에 따라 다릅니다. Sol Max를 사용할 수 없으면 검토 미완료로 기록하고 임의로 강도를 낮추지 않습니다.
- 검토 지침 변경은 앱 실행코드나 EXE 변경을 포함하지 않습니다.

## 검토 원칙
변경 전 `git status`로 작업 상태를 확인하고, 변경 후 필요한 테스트를 수행합니다.
Sol은 기본적으로 리뷰만 제공하고 파일을 직접 바꾸지 않으며, Terra가 결과를 반영합니다.

## 실제 검토 완료 확인과 인증 실패

[공통 검토 절차](CODEX_REVIEW_PROTOCOL.md)를 따릅니다. /subagents의 호출 표시뿐 아니라 실제 코드·시험을 읽은 검토자의 최종 응답과 지적 사항 처리 결과를 확인합니다. 401/451·토큰 갱신 실패·모델/Max 미지원·응답 누락은 검토 미완료입니다. 로그인 상태 표시만으로 성공으로 처리하지 않습니다.

같은 Sol Max를 제공하는 정식 협업 도구로 실제 검토가 성공하면 해당 경로를 기록할 수 있지만 CLI 인증 복구와 구분합니다. 모델·추론 강도를 임의로 낮추지 않습니다. 복구되지 않으면 일반 코딩과 오프라인 모의 검증만 진행하고, 필수 검토 대상의 최종 승인·장비 적용·배포는 보류합니다. 사용자의 별도 보류 지시는 검토가 성공해도 유지합니다.
