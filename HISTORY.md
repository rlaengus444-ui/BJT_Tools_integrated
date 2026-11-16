# BJT 통합프로그램 소스 버전 이력

## 11개 도구 통합프로그램

모델·격자 단일 도구의 4.6~5.6 이력과 별도로 관리합니다.

- **1.6.2** — 팀드라이브의 최신 전체소스 ZIP을 `versions/suite-v1.6.2/`에 원본 그대로 보관했습니다. 원본 ZIP SHA-256은 `a36979a2f6b0ae43a3c8021983dfc9bc43ad239e9748ef28a975a42ac8375d46`이며, 24개 포함 소스 해시와 Python 구문을 확인했습니다. 출처는 해당 폴더의 `PROVENANCE.json`에 기록했습니다.
- **1.7.0** — 최신 작업 소스는 `suite/`에 있습니다. 이미지 변환의 픽셀·DPI 설정, 전체 도구 입력값 저장·복원, 치투 파일 끌어놓기, 오븐 시뮬레이터 이름·한국어 옵션, 산소 저장항·수치 안정성·물리 입력 검증을 반영했습니다. 변경과 검증 근거는 `verification/`에 보관합니다.
- **1.7.1** — 온도·산소 3D 탭의 재생 조작부를 그래프 위에 고정했습니다. 재생·일시정지·정지를 분리하고 작은 창에서도 시간 슬라이더와 현재 시간을 표시합니다. 1.7.0의 물리 계산식과 나머지 기능은 유지합니다.

## 12개 도구 통합프로그램

- **1.9.2** — 서로 겹친 여러 CAD 솔리드를 포함한 STEP의 가져오기 오류를 수정했습니다. 원본 CAD의 복사본에서 재료 영역을 합친 뒤 기존의 형상 검사를 수행하며, 분리된 재료·공동을 보존합니다. 뒤집힌 무한 솔리드는 거부합니다. 첨부 모델의 배치·STL/CHITUBOX 저장과 Windows 전체·frozen GUI 검증을 통과했습니다. [검증 및 배포 기록](verification/step-material-1.9.2/)을 참고하세요.

## 모델·격자 단일 도구 원본

이 문서는 **2026-10-26에 실제 보관된 소스 파일 및 배포 ZIP에서 확인한 스냅샷**만 기록합니다. ZIP 내부 파일의 시각은 파일의 보관 시각이며 실제 최초 출시일을 보증하지 않습니다. 코드는 원본 바이트 그대로 보존합니다.

| 버전 | GitHub 경로 | 최초 확인 자료 | SHA-256 |
| --- | --- | --- | --- |
| v5.6 (현재) | `BJT_model_to_lattice_STL_all_in_one.py` | 2026-09-11 개별 업로드 | `4684a0fdfd5dec10c082a658a9abae4a32018bb510388b499354e683ae4061d6` |
| v5.5-print400-verified-candidate | `versions/v5.5-print400-verified-candidate/BJT_model_to_lattice_STL_all_in_one.py` | `BJT_SG1024_400dpi_v5_5.zip` (2026-09-10) | `f812e6aa98211b131fc541b04c7cdac755b4d1d509cd440df5eb3a2291e0d5bc` |
| v4.6-cap-profiled-fast | `versions/v4.6-cap-profiled-fast/BJT_model_to_lattice_STL_all_in_one.py` | `codes.zip` 원본 및 `BJT_SG1024_400dpi_v5_5.zip`의 `original_uploaded.py` | `c34f88827e6365ece2976a6b75b68970b3193859404d718301360ab611e3c300` |

## 비교시험용 소스 (정식 이전 버전 아님)

- `versions/v4.6-cap-profiled-fast/comparison_baseline.py` — SHA-256 `29ef33a2e158787e9f6281fa1ef0ad2fb3c82e92b43bfc265ac5a436f022f98b`.
- v4.6 `original_uploaded.py`와 비교할 때, 실제 변경은 의존성 자동 설치를 호출하는 `_ensure_packages()` 한 줄을 주석으로 바꾼 것뿐입니다. `original_baseline.py`에 들어 있던 검증 환경용 소스이며 별도 릴리스라고 주장하지 않습니다.

## 발견했으나 별도 배포본이 없는 버전

- v5.3 / v5.4 등은 최신 코드의 내부 알고리즘 명칭 및 주석으로 추적되지만, **독립된 원본 소스 스냅샷**은 발견하지 못했습니다. 코드 조각을 과거 완성본으로 재구성하지 않았습니다.
- v5.5는 '출력 400 DPI 검증 후보' 명칭이지만 배포 자료에도 실제 Chitubox 환경 검증이 완료됐다고 명시되지 않았으므로 생산 품질 보증으로 해석하면 안 됩니다.

## 보안 및 검증 경계

- `codes.zip`에는 인증파일(`credentials.json`, `token.json`)도 있으므로 **원본 ZIP 전체를 업로드하지 않고 해당 Python 소스 파일만 선별**했습니다.
- 보관 시 Python AST 구문 검사와 원본 SHA-256을 확인했습니다. 장비·Windows GUI·실물 슬라이서/프린터 통합검증은 수행하지 않았습니다.
