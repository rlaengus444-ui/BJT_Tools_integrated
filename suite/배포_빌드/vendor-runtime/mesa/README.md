# Windows 소프트웨어 OpenGL

EXE는 Mesa llvmpipe 26.3.0을 사용해 GPU 드라이버에 의존하지 않고 원본 3D 표면을 표시합니다. 다른 OpenGL 사용자도 같은 프로세스에서는 소프트웨어 렌더러를 사용합니다.

`manifest.json`은 검토한 x64 WGL DLL, 다운로드 아카이브와 출처·라이선스 해시를 고정합니다. DLL은 저장소에 보관하지 않습니다. `ci_windows_release.py`가 새 작업 폴더에 다운로드해 두 해시를 확인하고, CI Python 실행기 옆 및 빌드한 EXE 옆에 같은 파일을 배치합니다. 기존 파일의 해시가 다르면 중단합니다. 시스템 DLL 디렉터리는 변경하지 않습니다.

소스와 EXE 검증은 실제 로드된 모듈의 경로·해시와 llvmpipe 렌더러, 빈 경통 중심·불투명한 벽의 픽셀을 확인합니다. 패키징은 DLL, 출처 기록과 모든 라이선스 파일을 다시 검사합니다. EXE 폴더의 `opengl32.dll`, `graphics-runtime.json`, `_internal/licenses/Mesa-LLVM`을 함께 유지해야 합니다.

Mesa는 상위 소스의 라이선스 디렉터리와 원본 저작권·SPDX 주석을 보존했습니다. 주석 모음에는 선택한 WGL/llvmpipe 외의 상위 소스 구성 요소에 대한 고지도 포함됩니다. LLVM 23.1.5의 전체 LICENSE.TXT도 포함합니다. 실제 빌드 선택은 고정한 upstream recipe를 참조하세요. MSVC 런타임 관련 고지는 기존 Microsoft-VC++-2022 라이선스 폴더에 보존합니다.
