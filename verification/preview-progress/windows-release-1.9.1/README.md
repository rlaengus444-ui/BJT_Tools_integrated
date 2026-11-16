# Windows EXE 1.9.1 검증·게시

- 기준 커밋: `a9153ea7bb6402d1fd6f2ab85388f5a4f8791be0`.
- [Windows CI](https://github.com/rlaengus444-ui/BJT_Tools_integrated/actions/runs/43835230743): Windows Server 2022·Python 3.12.10 x64·VTK 9.3.1, 전체 작업 12분 29초.
- [게시한 릴리스](https://github.com/rlaengus444-ui/BJT_Tools_integrated/releases/tag/v1.9.1), [EXE ZIP](https://github.com/rlaengus444-ui/BJT_Tools_integrated/releases/download/v1.9.1/BJT_1.9.1_Windows_x64.zip).

소스 검사 **451 통과·2 생략·하위 검사 313 통과**, Rust 검사 **5 통과**, 빌드·실행·출처 검사 **16개 통과**입니다. 생략은 기존 허용 목록의 조건이며 새 그래픽 검사를 생략하지 않았습니다. 실제 frozen EXE의 GUI/CLI, 입력 복원, STEP 불러오기, 배치·원본 검증·STL/CHITUBOX 저장을 합성 입력으로 확인했습니다.

첫 native 실행에서는 기본 Hyper-V 그래픽 드라이버가 WGL 픽셀 형식을 만들지 못했습니다. 격리한 9개 그래픽 진단으로 확인한 뒤 고정한 Mesa llvmpipe 26.3.0을 실행기 옆에 배치했습니다. 같은 9개 native 진단과 최종 전체 CI가 통과했습니다. 원래 VTK/OCP 버전과 계산 엔진은 보존했습니다.

EXE는 실제로 로드한 DLL 경로·해시와 OpenGL 4.6/Mesa 26.3.0/llvmpipe를 확인했습니다. 경통 화면은 **469×487**, 중심 픽셀 `[255,255,255]`, 벽 픽셀 `[144,121,132]`입니다. **100개·409,600개 면**을 모두 표시했고 최초 표시 약 **0.074초**, 카메라 변경 약 **0.087초**였습니다. 이는 해당 CI 환경의 합성 화면 실측이며 고객 모델 100개의 최적화 시간은 아닙니다.

업로드 후 EXE ZIP을 다시 다운로드해 다음을 확인했습니다.

- **408,139,852 bytes**, SHA-256 `d28cc10ceaa2252c5ae3123f77fdc860a2dc9dbfa4ec2c84618e2b6d6f7305f9`.
- GitHub 서버의 다섯 배포 파일 크기·SHA-256과 native 빌드 기록 일치.
- ZIP 3,683개 파일 CRC, GUI·CLI의 AMD64 PE 및 실제 검증된 실행파일 해시 일치.
- Mesa DLL, 출처, 10개 라이선스 고지와 고정 해시 일치. 기존 운영체제 DLL 제외 규칙 유지.
- 비공개 저장소의 릴리스 `446805923`을 게시하고 노션 팀페이지 ‘바인더젯 통합 프로그램’ 맨 위에 링크를 추가했습니다.

## 독립 검토

기존 native `collaboration.spawn_agent`의 `sol_reviewer`를 `collaboration.followup_task`로 실제 재호출했습니다. 요청 모델·강도는 **gpt-6.1-sol / max**입니다. 새 그래픽 배포 구조는 구현 전에 검토했고, 실제 변경·검사에 대한 사후 검토와 수정 후 재검토를 완료했습니다. 제공 모델 신원을 독립 인증했다는 뜻은 아닙니다.

검토에서 숨긴 창의 2×2 이미지가 미리보기 검사를 통과하던 문제를 찾아, 창을 표시하고 경통 픽셀 및 로드한 DLL을 필수 검사로 바꿨습니다. Mesa 배포는 고정 아카이브·DLL 해시, 기존 파일 보호, 라이선스·출처·중복 DLL 검사를 검토했습니다. 최종 응답은 **“No blocking code findings remain; proceed to fresh native Windows CI.”**였습니다.

검토자가 실행한 검사: 22개·하위 검사 15개, 실제 고정 DLL·고지 배치 및 변조 거부 검사, Linux Tk의 실제 경통 픽셀과 100개 표면 실측. 실제 Windows 및 frozen 실행 결과는 위 CI가 제공한 별도 근거입니다. 이 기록은 실제 생산 장비나 고객 모델의 Windows 운전 검증을 주장하지 않습니다.

`native-summary.json`, `frozen-packing.json`, `source-tests.txt`, `publication-verification.json`, `published-release.json` 및 검토 파일 해시를 함께 보존했습니다. 상위 라이선스 고지는 원본 공백·바이트를 유지하므로 코드 공백 검사는 해당 고지 파일을 제외했습니다.
