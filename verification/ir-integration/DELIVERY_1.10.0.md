# IR 통합 1.10.0 평가판 · 검증·배포 기록

[v1.10.0](https://github.com/rlaengus444-ui/BJT_Tools_integrated/releases/tag/v1.10.0)은 공개 평가용 사전 릴리스입니다. 릴리스 ID는 `448714553`이며 최신 안정판은 계속 [v1.9.2](https://github.com/rlaengus444-ui/BJT_Tools_integrated/releases/tag/v1.9.2)입니다. 애플리케이션 소스와 `v1.10.0` 태그는 `0a48712576b9b7cbb147c03becd957eb559af4c7`을 가리킵니다. 후속 패키징·게시·문서 커밋은 별도로 관리합니다.

## 실행과 통합 범위

Windows 10·11 x64에서 EXE ZIP을 새 폴더에 모두 풀고 `BJT_통합프로그램.exe`를 실행합니다. `_internal` 폴더를 함께 유지하세요. 기존 12개 도구에 IR 피드백을 추가한 13개 도구를 같은 창과 테마로 제공합니다. IR 피드백은 M1·S3를 지원하며 M2는 지원하지 않습니다.

IR 기본 선택은 **이미지 회복제어 (내장 Excel)**입니다. 원본 계산시트는 11,523,470바이트이며 SHA-256은 `eb34cd39d364011015bee1a03cc1c024a1bb41911df35ed5aeac41b299941c20`입니다. 해당 모드에는 Microsoft Excel 데스크톱이 필요합니다. 수동 이미지·ZIP 분석, 반경·DPI 변경, 최대 IR 출력, 그래프와 CSV·PNG 저장을 제공합니다. 입력은 저장하지만 이전 연결·장비 PID·이미지 분석 결과는 복원하지 않습니다.

## 검증과 독립 검토

[Windows 검증 실행 44096041775](https://github.com/rlaengus444-ui/BJT_Tools_integrated/actions/runs/44096041775)에서 Windows 전체 소스, 원본 IR 회귀, 소스 캡슐, EXE 모의 실행·패키징 검증이 성공했습니다. 게시 검증기는 원본 CI 증거에 대해 **IR 회귀 572개·생략 0개**, **소스 캡슐 8개·생략 0개** 조건을 적용했습니다. 최종 전체 소스 JUnit 원문은 로컬에 보관하지 않았으므로 이 문서에는 그 시험 총수를 기재하지 않습니다. 실행·산출물 고정값은 [Windows 빌드 기록](windows-build-pins-44096041775.json)에 있습니다.

독립 검토는 네이티브 협업 경로에서 `gpt-6.1-sol / max`를 요청하여 완료했습니다. [소스 검토](sol-code-post.json), [누적 CI·frozen fixture 변경 검토](sol-frozen-fixture-delta.json), [최종 정리 산출물 검토](sol-clean-artifact-review.json), [초안 교체 경로 검토](sol-draft-replacement-review.json)를 구분합니다. 현재 산출물 승인은 최종 정리 산출물 검토를 기준으로 합니다. 원본 로그가 남았던 첫 산출물의 [과거 HOLD 기록](sol-final-artifact-review.json)은 별도 이력입니다.

검토에서 원본·소스 ZIP·내장 소스 캡슐, 91개 핵심 등록 항목, 원본 계산시트·브리지 DLL·설명서·PE 정보를 대조했습니다. 실제 전체 ZIP 항목은 목록화했지만 별도의 완전한 제3자 기대 파일별 릴리스 매니페스트는 제공되지 않았습니다. 이 검토를 그러한 매니페스트 전체 대조로 표현하지 않습니다.

## 합성 QA 로그 한 개 제외

원본 Windows CI ZIP은 **418,199,737바이트**, SHA-256 `7a2bcc5e23ea7ae4ab5f34d72bf901ca9d3892ffe73ba6a9e9456133336a5243`입니다. 원본을 보존하고 복사본에서 아래 합성 시험 로그만 제외했습니다.

- 항목: `BJT_통합프로그램_1.10.0_EXE/_internal/logs/errors.log`
- 원래 압축 해제 크기: 1,626바이트
- SHA-256: `c06264143b923010383965a790f0561014fa5eecc3908827f81ecea359394df1`

남은 **3,727개 항목**의 순서·메타데이터·압축 해제 바이트·raw local record·위치 오프셋을 정규화한 central record를 원본과 대조했습니다. 허용된 ZIP 위치 변경 외의 기록과 파일 바이트가 동일합니다. 소스·설명서 ZIP과 단일 소스 실행기는 그대로 유지했고 체크섬 파일의 EXE 해시 필드만 갱신했습니다. 프로그램을 재빌드하거나 정리한 ZIP의 Windows 실행을 다시 수행하지 않았습니다.

패키징 코드 커밋은 `6001a003fd9d763aad9b154162b088edbcf61a13`입니다. [정리 출처·전체 항목 증거](clean-repack-provenance.json), [정리 파일 고정값](cleaned-asset-facts.json), [애플리케이션·패키징 코드 식별](clean-code-identity-binding.json)을 보존합니다. 코드 커밋은 로컬 정리 후 생성되었고 실제 사용한 코드 바이트와 일치한다는 점도 식별 기록에 명시했습니다.

## 게시 파일

게시 후 아래 다섯 영구 URL을 비로그인 상태로 **전체 GET**하여 각각 HTTP 200, 크기와 SHA-256 일치를 확인했습니다. [공개 다운로드 검증](anonymous-download-verification.json)과 [최종 서버 읽기 결과](delivery-server-readback.json)를 보존합니다.

| 파일 | 바이트 | SHA-256 |
| --- | ---: | --- |
| [Windows EXE ZIP](https://github.com/rlaengus444-ui/BJT_Tools_integrated/releases/download/v1.10.0/BJT_1.10.0_Windows_x64.zip) | 418199188 | `e0ad1de632989578fc360bd07df3ad8d060487872f6fadbb452852a6fbcc015a` |
| [전체 소스 ZIP](https://github.com/rlaengus444-ui/BJT_Tools_integrated/releases/download/v1.10.0/BJT_1.10.0_source.zip) | 26398492 | `c324e834521f6efff320a71d662ca3fbfef4a414ff259754b23064e3015d8ba7` |
| [설명서 ZIP](https://github.com/rlaengus444-ui/BJT_Tools_integrated/releases/download/v1.10.0/BJT_1.10.0_manual.zip) | 241422 | `2e7cec3aa47df43130ba2cd1202da5a9f16a2e298487fa15f3d36aecb445993b` |
| [단일 소스 실행기](https://github.com/rlaengus444-ui/BJT_Tools_integrated/releases/download/v1.10.0/BJT_1.10.0_standalone.pyw) | 16101255 | `be921bc2625d5adb24aae61f9c7e7ebe52a127280c9e081e7e0560cb5e9e9d7a` |
| [SHA256SUMS.txt](https://github.com/rlaengus444-ui/BJT_Tools_integrated/releases/download/v1.10.0/SHA256SUMS.txt) | 361 | `f6c084a84dff73daa607886dda3a64e9d1d17c27beb1f87b8c7fd90ee3463d80` |

[초안 교체 실행 44110983133](https://github.com/rlaengus444-ui/BJT_Tools_integrated/actions/runs/44110983133)은 **실패로 보존**합니다. 두 파일 교체 후 본문만 PATCH하는 과정에서 GitHub 초안의 태그 이름이 임시 이름으로 바뀌어 마지막 태그 대조에 실패했습니다. 새 EXE·체크섬과 나머지 세 기존 자산 ID는 서버에서 일치했습니다. 이후 [별도 독립 검토](sol-private-tag-repair-review.json)를 거친 메타데이터 복구로 `v1.10.0`·소스 커밋·초안·사전 릴리스·본문 해시를 대조했습니다. [복구 완료 기록](repair-private-tag-completed.json)을 확인한 뒤 공개 평가판으로 게시했습니다. 실패한 실행을 성공으로 바꾸거나 파일을 다시 빌드하지 않았습니다.

## 배포 채널

GitHub 릴리스는 위 다섯 파일의 전체 다운로드·해시 검증을 완료했습니다. Notion BJT 안내에는 1.10.0을 최신 평가판으로 추가하고 1.9.2를 접었으며, IR 안내에는 통합판 링크를 추가했습니다. 기존 BJT 네이티브 첨부 41개·임베드 2개와 IR 첨부 9개는 편집 대상에서 제외했습니다.

초기 배포에서는 Google Drive에 소스·설명서 ZIP, 단일 소스 실행기, 체크섬을 올렸습니다. 설명서·실행기·체크섬 세 파일은 전체 재다운로드 후 SHA-256을 확인했습니다. 소스 ZIP은 크기만 확인했으며 Google의 악성 코드·스팸 분류로 원본 다운로드가 HTTP 403이어서 재다운로드 해시 대조를 완료하지 못했습니다. 제한 우회는 시도하지 않았습니다.

사용한 Drive 업로드 경로의 100MB 제한으로 EXE 업로드는 HTTP 413으로 실패했습니다. 당시에는 GitHub EXE 링크 바로가기를 제공했습니다. 이 초기 상태를 Drive 전체 EXE 업로드 완료로 표현하지 않습니다.

### 2026-11-18 · Drive에는 코드만 보관

사용자 요청에 따라 최신 1.10.0 Drive 폴더에서 설명서 ZIP·체크섬·EXE 바로가기를 제거하고, 소스 ZIP과 [단일 Python 실행 소스](https://drive.google.com/file/d/1qOaqGNx8HKHZ3IAbUjuLQ3oLuKJrdU_0/view)만 남겼습니다. 단일 실행 소스를 새로 전체 다운로드해 16,101,255바이트와 SHA-256 `be921bc2625d5adb24aae61f9c7e7ebe52a127280c9e081e7e0560cb5e9e9d7a`를 다시 확인했습니다. 이 Python 실행기에는 13개 도구의 소스와 필요한 Excel·런타임 리소스가 포함됩니다.

소스 ZIP의 Google Drive 다운로드 차단은 계속됩니다. 차단을 우회하지 않았으며 전체 소스 ZIP·Windows EXE·설명서는 GitHub 릴리스에서 제공합니다. [최신 Drive 보관 상태](drive-code-only-2026-11-18.json)는 초기 배포 기록과 별도로 보존합니다.

## 남은 보류

실제 Microsoft Excel COM 계산, 센서·PLC·램프 출력과 실제 장비는 검증하지 않았습니다. 원본 수식·구조 검사와 Windows 모의 실행은 실제 Excel 계산·장비 동작 검증과 구분합니다. **최종 제어 알고리즘 승인과 실제 장비 적용은 HOLD**입니다. 이번 공개 평가는 이 보류를 해제하지 않습니다.
