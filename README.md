# BJT 통합도구

14개 도구를 한 창에서 실행하는 Windows 10·11 64비트 프로그램입니다. IR 피드백을 포함한 [1.12.0 평가용 사전 릴리스](https://github.com/rlaengus444-ui/BJT_Tools_integrated/releases/tag/v1.12.0)를 제공합니다. 최신 안정판은 [1.9.2](https://github.com/rlaengus444-ui/BJT_Tools_integrated/releases/tag/v1.9.2)이며, 최신 통합판 소스는 [suite](suite/)에 있습니다.

## 실행

[Windows EXE ZIP · 1.12.0 평가판 다운로드](https://github.com/rlaengus444-ui/BJT_Tools_integrated/releases/download/v1.12.0/BJT_1.12.0_Windows_x64.zip)을 새 폴더에 모두 압축 해제한 뒤 `BJT_통합프로그램.exe`를 실행하세요. Python은 따로 설치하지 않아도 됩니다. 같은 위치의 파일과 `_internal` 폴더를 모두 유지합니다. 이미지 회복제어 내장 Excel 모드에는 **Microsoft Excel 데스크톱**이 필요합니다.

EXE ZIP은 **418,480,079바이트**, SHA-256은 `c6c55c7b8e76ca92b9a011bb3a650290ab4af906f2f970fb8e49685d3775f27f`입니다. [SHA256SUMS.txt](https://github.com/rlaengus444-ui/BJT_Tools_integrated/releases/download/v1.12.0/SHA256SUMS.txt)로 다운로드 파일을 확인할 수 있습니다. 게시 후 다섯 파일 모두 비로그인 전체 다운로드와 크기·SHA-256 대조를 통과했습니다.

1.12.0 통합 소스판은 [BJT_통합프로그램.py](BJT_통합프로그램.py)를 PyCharm에서 실행하세요. Python 3.12 x64를 선택하며 첫 실행에 고정 패키지를 설치합니다. [전체 소스 ZIP](https://github.com/rlaengus444-ui/BJT_Tools_integrated/releases/download/v1.12.0/BJT_1.12.0_source.zip), [단일 소스 실행기](https://github.com/rlaengus444-ui/BJT_Tools_integrated/releases/download/v1.12.0/BJT_1.12.0_standalone.pyw), [설명서 ZIP](https://github.com/rlaengus444-ui/BJT_Tools_integrated/releases/download/v1.12.0/BJT_1.12.0_manual.zip)도 제공합니다. 단독 배치 참고본은 [standalone](standalone/)에 별도로 보관합니다.

리코터 시뮬레이터에서 피딩 방향을 고릅니다. 뒤 → 앞 피딩을 마치면 앞 → 뒤 복귀 중에 스프래딩롤러가 먼저 분말을 펴고 컴팩팅롤러가 뒤따라 누릅니다. 앞 → 뒤 피딩은 한 번의 주행에서 토출·스프래딩·컴팩팅을 순서대로 진행합니다. AOR 시험·운전 속도 220 mm/s, 토출 두께 2 mm, 분말 안식각 45°, 두 롤러 지름 20 mm, 축 간격 46 mm가 기본값입니다. 세 그래프에서 커서 좌표와 곡선값을 확인할 수 있습니다. 프로그램을 다시 열면 마지막 입력과 그래프 설정이 복원됩니다. 화면 예시는 파형을 보기 쉽게 피딩롤러를 2 RPM으로 설정했습니다. [화면 예시](verification/recoater-1.12.0/recoater-preview.png) · [Windows 검증 기록](https://github.com/rlaengus444-ui/BJT_Tools_integrated/actions/runs/45609232396)

IR의 실제 장비 적용과 최종 제어 알고리즘 승인은 보류 상태입니다.

팀드라이브에는 단일 Python 실행 소스를 보관합니다. [팀드라이브 코드 폴더](https://drive.google.com/drive/folders/1ffXj9JWRcAyPcOwZdqnlgoKYIwc7ZDvp)

소스 실행: `python suite/run_suite.py`. 필요한 Python 환경과 패키지는 [suite/suite_runtime.py](suite/suite_runtime.py)와 [빌드 안내](suite/배포_빌드/재빌드_안내.txt)를 참고하세요.

## 포함 기능

| 도구 | 기능 |
| --- | --- |
| 모델·격자·STL | 모델·슬라이싱 이미지에서 쉘·격자 STL 생성 |
| 슬라이싱 배치·합성 | 이미지·STL 이동·회전·복제, 레이어 합성 |
| 폴더 겹치기 | 여러 슬라이싱 폴더를 레이어별로 합성 |
| 슬라이싱 교체 | 번호 범위 교체·백업·복구·검사 |
| 슬라이싱 위치 검사 | 교체 경계·인접층의 위치 이동 확인 |
| 오븐 열확산 시뮬레이터 | 3D 열전달·산소 확산·열전대 보정, 시간별 3D 재생·일시정지·정지, 한국어·영어 선택 |
| 슬라이싱 일치성 검사 | 픽셀·위치·해상도·비트 수·형식 비교 |
| 번호 복제·누락 검사 | 번호 범위 복제, 누락·중복 확인 |
| 시퀀스별 Z값 | M1 레이어별 Z값 조회·CSV 저장 |
| 이미지 변환 | 출력 픽셀·X/Y DPI 설정, 4비트 반전 TIFF 변환 |
| 모델 자동 배치 | STEP/STL 수량·24개 자세·내장 정책으로 3D 배치, 검증·CHITUBOX 저장 |
| 치투 오브젝트 검사 | 파일 끌어놓기, 겹침·접촉·최소 간격 검사, 문제 쌍 3D 확인 |
| 리코터 시뮬레이터 | 피딩 방향 선택, 파형에 따른 분말 공급, 토출·더미·연속피딩 비교 그래프와 커서 좌표 |
| IR 피드백 제어 | 이미지 회복제어 Excel 내장, 수동 이미지 분석, 층별 IR 제어·그래프·CSV/PNG 저장 |

입력값과 입력 목록은 종료할 때 사용자별로 저장되고 다음 실행에서 복원됩니다. 도구별 조작법은 프로그램의 사용설명서 또는 [오프라인 설명서](suite/manuals/index.html)를 확인하세요.

## 이전 버전

- [안정판 1.9.2](https://github.com/rlaengus444-ui/BJT_Tools_integrated/releases/tag/v1.9.2): [Windows 검증·배포 기록](verification/step-material-1.9.2/).
- [통합프로그램 1.6.2](versions/suite-v1.6.2/): 팀드라이브 원본 소스와 출처·무결성 기록.
- 저장소 루트의 [모델·격자 단일 도구 v5.6](BJT_model_to_lattice_STL_all_in_one.py)은 기존 원본으로 보존했습니다. 통합프로그램 버전과 별도로 관리합니다.
- 이전 모델·격자 소스와 검증 범위: [HISTORY.md](HISTORY.md).

## 모델 자동 배치 통합 · 1.9.0

1.8.0의 기존 도구와 Rust 코드를 보존하고, 제공된 608회 학습 정책을 사용하는 모델 자동 배치를 같은 Tk/ttk 창에 추가했습니다. 신규 학습은 제공하지 않습니다. 통합 단일 실행 파일은 `BJT_통합프로그램.py`이며 단독 배치 참고본은 [standalone](standalone/)에 별도로 보관합니다. 검증 결과는 [verification/packing-policy](verification/packing-policy/)에 기록합니다.

## 모델 자동 배치 표시 개선 · 1.9.1

배치 수량·비율과 경과 시간, 정밀 탐색 추가 시간을 포함한 남은 탐색 한도를 표시합니다. 준비·XY 정렬·최종 검증은 별도 단계로 표시하며 전체 완료 시각을 예측하지 않습니다.

3D 화면은 VTK 깊이 버퍼로 원본 표면 전체를 표시해 얇은 벽의 앞뒤 면이 뒤섞이는 현상을 해결했습니다. 면을 생략하지 않고 복사본끼리 메시 데이터를 공유합니다. 빌드박스·원점·XYZ축만 남기고 숫자 눈금·격자를 없앴습니다. 화면 표시 오류가 나도 검증된 결과는 저장할 수 있습니다.

검증과 Sol 검토 기록은 [verification/preview-progress](verification/preview-progress/)에 있습니다. 추가 Windows EXE 검증과 게시 결과는 [Windows 배포 기록](verification/preview-progress/windows-release-1.9.1/)에 있습니다.

## IR 피드백 통합 · 1.10.0

1.9.2 최신 소스에 IR 모듈 `2026.11.12-layer-validity.1`과 원본 이미지 회복제어 계산시트를 통합했습니다. IR 도구의 기본 선택은 **이미지 회복제어 (내장 Excel)**이며 계산시트 파일을 따로 지정하지 않아도 됩니다. Microsoft Excel 데스크톱이 필요합니다. 기존 기본 알고리즘과 사용자 Excel 선택도 유지합니다.

슬라이싱 ZIP 또는 이미지 전체 선택 후 반경·DPI를 확인해 분석하고 연결하세요. 입력은 복원하지만 연결·장비 PID·이미지 분석 결과는 복원하지 않습니다. [조작 방법](suite/manuals/ir_feedback.json)과 [통합 검증](verification/ir-integration/README.md)을 확인하세요. 최종 제어 승인과 실제 장비 적용은 보류 중입니다.
