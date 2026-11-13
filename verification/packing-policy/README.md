# 모델 자동 배치 통합 · 1.9.0

기준 통합판 v1.8.0(`b162078`)의 11개 도구를 보존하고 `nesting`을 추가했습니다. 전체 12개 도구가 같은 Tk/ttk 창과 테마·설정·사용설명서를 사용합니다. 별도 Qt 창을 띄우는 연결 방식은 사용하지 않습니다.

## 실행과 파일 구분

- 저장소 루트 `BJT_통합프로그램.py`: PyCharm에서 실행하는 전체 통합판. 소스를 포함하고 사용자 캐시에 검증·추출한 뒤 기존 격리 환경 설치기를 사용합니다. 첫 실행에는 패키지 다운로드 시간이 필요합니다.
- `python suite/run_suite.py`: 체크아웃 소스 실행. 기존 `.pyw` 실행기와 루트의 옛 모델·격자 단일 도구는 보존했습니다.
- `standalone/PowderNest_Embedded_Policy.py`: 요청에 따라 같은 저장소에 보관하는 단독 Qt 참고본. 통합 실행기는 위 파일입니다.
- 소스 ZIP: 통합 단일 `.py`, 전체 소스, 설명서, 단독 참고본을 포함합니다. 실행 파일(EXE) ZIP이 아닙니다.

Windows 10/11 x64, Python 3.12를 사용하세요. 기존 통합판 NumPy/SciPy/Trimesh 등의 고정 버전을 유지하고 VTK 9.3.1과 호환되는 `cadquery-ocp==7.8.1.1.post1`을 추가했습니다. 고정 목록은 `suite/배포_빌드/windows-requirements.txt`입니다. STEP·STL 원본은 보존합니다.

## 정책과 배치

제공 정책의 학습 608회·갱신 12,825회·범위 157개를 내장했습니다. 신규 학습과 정책 덮어쓰기는 없습니다. 정책 1차 탐색 후 알고리즘이 개선하며 유효한 최선의 해를 유지합니다. 정책 사용이 모든 사례에서 높이 감소를 보장하지는 않습니다.

파일 추가·드래그앤드롭 → 수량 칸 두 번 클릭 → 빌드 조건 → 계산 → 회전 가능한 3D 결과 → STL/좌표/보고서 또는 CHITUBOX 저장 순서입니다. 벽 여유는 X·Y에만 적용하며 Z 다단 배치를 허용합니다. STEP 삼각화 여유는 별도입니다. CHITUBOX 직렬화 검증에만 0.01 mm 허용 오차를 사용합니다. 빌드박스 추가·수정·저장, 원점·각 축 보기, 정밀 탐색·Rust·XY 정렬 옵션을 유지합니다.

화면은 최대 18,000개 삼각형만 표시합니다. 검증·내보내기는 원래 형상을 사용합니다. 원본 형상에 문제가 있으면 진단 미리보기만 허용하고 배치 성공으로 표시하지 않습니다. 고리 얽힘 검사는 양성 얽힘을 거부하지만 모든 분리 경로를 증명하지는 않습니다.

## 실제 검증 근거

- 통합판 전체: **421 통과·40 생략·279 하위 검사 통과, 62.60초**, `suite-tests.txt`. 생략은 선택적 BJT 네이티브 DLL 부재, Windows GDI+/파일 시스템 동작, 원본 개발 경로 부재 등 로그에 명시되어 있습니다.
- 통합판 고정 의존성에서 기존 배치 엔진 호환성: **218 통과·1 Qt 전용 검사 제외, 24.02초**, `backend-compat-tests.txt`.
- 원래 배치 저장소 전체: **391 통과, 57.33초**, 해당 저장소 `docs/export-safety-tests.txt`.
- 실제 통합 단일 파일에서 12개 탭을 모두 생성하고 사용자 STEP를 두 빌드박스에 배치·독립 재검증·두 형식으로 저장했습니다. 최신 수치·환경은 `integration-report.json`, 화면은 `integrated-packing*.png`입니다. 100개 복사본은 화면 렌더링 성능 검사이며 100개 실제 배치 최적화 측정이 아닙니다.
- `engine-provenance.json`: 17개 원래 엔진 파일 바이트 일치, 두 내보내기 모듈은 추가 원본 보호 호출을 제외한 AST 일치, 신규 공통 보호 모듈 1개. 기존 11개 도구 소스는 바이트 일치합니다.
- 작업 완료 이벤트 경합, 재읽기 실패·취소 후 낡은 모델 사용, 입력 파일과 저장 경로 충돌을 재현하고 회귀 검사를 추가했습니다. 수량 7을 입력한 직후 계산 버튼을 누르는 실제 Tk 이벤트에서도 수량 7로 요청됨을 확인했습니다.

환경은 Linux x86_64·Python 3.12.14·Xvfb의 실제 Tk 화면입니다. Linux 결과로 Windows EXE·Windows 드래그앤드롭·실제 프린터를 검증했다고 주장하지 않습니다. 기존 Windows 빌드 수집 설정과 Rust/전체 도구 배포 게이트에 배치 도구 실동작 게이트를 추가했습니다. Windows 배포 시 이 게이트를 통과한 결과가 별도로 필요합니다.

## 재현 명령

```bash
DISPLAY=:98 PYTHONPATH='suite:suite/배포_빌드' .venv/bin/python -m pytest -q suite/tests release_tools
.venv/bin/python verification/packing-policy/verify_provenance.py
.venv/bin/python BJT_통합프로그램.py --list-tools
.venv/bin/python BJT_통합프로그램.py --verify-sources
DISPLAY=:98 .venv/bin/python -I verification/packing-policy/verify_integrated_gui.py \
  --launcher BJT_통합프로그램.py --model /path/to/model.step --output /tmp/bjt-gui-proof
```

독립 검토 호출·발견 사항·수정·최종 상태는 `design-review.md`와 `implementation-review.md`에 기록합니다. 제공 모델의 신원을 독립 인증하거나 코드 검토로 실제 장비 검증을 대체했다고 주장하지 않습니다.
