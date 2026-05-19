# report-mcp

사용자가 업로드한 문서 양식(DOCX / HWP / HWPX / PDF)에 챗봇이 생성한 내용을 채워 넣고 원본 서식·표·이미지를 그대로 보존한 채 파일로 저장하는 [Model Context Protocol](https://modelcontextprotocol.io/) 서버.

- **입력**: 사용자가 첨부한 빈 양식 파일 + 챗봇의 답변
- **출력**: 양식의 디자인을 그대로 따른 보고서 파일 (DOCX→DOCX, HWP/HWPX→HWPX)
- **핵심 가치**: "재생성"이 아니라 원본 IR을 그대로 수정. 글자 크기·색·표·이미지·페이지 레이아웃 손상 없음.

## 동작 흐름

```
사용자 첨부 양식.hwpx        챗봇이 만든 답변 텍스트
        ↓                              ↓
    describe / inspect          ←──→  목차·셀·길이 한도 파악
    list_template_targets
        ↓                              ↓
    edits 목록 (target_id, new_text)
        ↓
    fill_and_save  →  결과.hwpx (원본 서식 유지, 내용만 교체)
```

## 노출되는 도구 (7개)

| 도구 | 용도 |
|---|---|
| `register_template(template_b64, template_filename)` | 템플릿을 서버에 캐싱해서 `template_id` 받음. 같은 파일에 여러 도구 호출 시 base64 재업로드 회피 |
| `unregister_template(template_id)` | 캐시 해제 (자동 만료 1시간) |
| `describe_template(...)` | 한눈 요약 — 포맷, 페이지·표·이미지 개수, 상위 단락 |
| `inspect_template(..., start, limit)` | 페이지네이션된 단락 보기 |
| `list_template_targets(..., target_kinds, start, limit)` | 편집 가능한 모든 위치 + target_id + text_hash + 길이 정보 |
| `fill_and_save(..., edits, ...)` | 검증 → 필터 → 적용 → 저장 |
| `convert_to_hwpx(...)` | HWP/HWPX/HWTX → HWPX 변환 (편집 없이) |

## 입력·출력 모드 (파일시스템 격리 대응)

모든 도구는 **두 가지 방식** 중 하나로 템플릿을 받습니다:

- **`template_path`** — 서버가 실행되는 머신의 파일 경로 (예: `C:/Users/.../template.hwpx`). 빠르고 복사 비용 없음.
- **`template_b64` + `template_filename`** — base64 인코딩된 원본 바이트 + 원본 파일명 (예: `template_b64="UEsDBBQ...", template_filename="template.hwpx"`). 챗봇 세션과 MCP 서버가 다른 파일시스템에 있을 때 (예: Anthropic 샌드박스 ↔ 사용자 Windows) 사용.

`fill_and_save`는 추가로:

- **`output_path`** — 서버 머신에 결과 파일 저장
- **`return_output_bytes=True`** — 응답에 `output_b64` (base64 바이트) + `output_size_bytes` 포함. 챗봇이 사용자에게 직접 전달 가능.

### 챗봇 세션(샌드박스)에서 호출 예

```python
import base64
file_bytes = open("template.hwpx", "rb").read()
b64 = base64.b64encode(file_bytes).decode("ascii")

# 1. 양식 분석
desc = describe_template(template_b64=b64, template_filename="template.hwpx")

# 2. 편집 가능 위치 조회
targets = list_template_targets(template_b64=b64, template_filename="template.hwpx")

# 3. 챗봇이 edits 구성 후 적용
result = fill_and_save(
    template_b64=b64,
    template_filename="template.hwpx",
    edits=[...],
    return_output_bytes=True,
)
output_bytes = base64.b64decode(result["output_b64"])
# output_bytes를 사용자에게 첨부로 전달
```

## 설치

### 사전 요구사항

- **Python 3.13+** (document-processor 의존성)
- **JDK 11+** (HWP 또는 PDF 입력 시에만 필요. DOCX·HWPX만 쓸 거면 생략 가능)
- **git** (document-processor를 git에서 직접 받아옴)

### 프로젝트 설치

```powershell
git clone https://github.com/jaykim429/report-mcp.git
cd report-mcp
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
```

document-processor의 알려진 `_edited` 누적 버그는 `apply_library_patches()`가 import 시 자동 monkey-patch하므로 별도 조치 불필요. 더 영구적인 fix를 원하면 [`patches/document_processor_edited_cumulation.patch`](patches/document_processor_edited_cumulation.patch) 적용.

## MCP 클라이언트 등록

### Claude Code (프로젝트 단위)

[`.claude/mcp_servers.json`](.claude/mcp_servers.json)이 이미 포함돼 있어 이 폴더에서 Claude Code를 실행하면 자동 인식.

### Claude Desktop (전역)

`%APPDATA%\Claude\claude_desktop_config.json`에 추가:

```json
{
  "mcpServers": {
    "report-mcp": {
      "command": "C:\\path\\to\\report-mcp\\.venv\\Scripts\\python.exe",
      "args": ["-m", "report_mcp"],
      "env": { "PYTHONIOENCODING": "utf-8" }
    }
  }
}
```

저장 후 Claude Desktop 재시작하면 도구 메뉴에 4개 도구가 표시됨.

## 챗봇이 받는 응답 표준

모든 도구는 다음 필드를 포함한 dict를 반환:

```
status        : ok / dry_run_ok / 그 외 13종 실패 상태
recovery_hint : 실패 시 챗봇이 다음에 무엇을 할지 (실패 target_id + 현재 hash 포함)
```

### 정의된 status

| status | 상황 |
|---|---|
| `ok` | 정상 적용 |
| `dry_run_ok` | 검증만 통과 (파일 안 씀) |
| `not_found` | 템플릿 경로 없음 |
| `bad_argument` | 잘못된 인자 (음수 start, 잘못된 target_kinds, 디렉터리 경로 등) |
| `edit_parse_failed` | edits 스키마 오류 |
| `duplicate_target_id` | 같은 target_id에 두 편집 |
| `style_field_target_mismatch` | run 속성을 paragraph 타겟에 (또는 반대) |
| `validation_failed` | hash mismatch 등 (failed_targets에 상세) |
| `output_extension_mismatch` | 출력 확장자가 템플릿 포맷과 안 맞음 |
| `format_not_writable` | PDF 출력 등 |
| `format_requires_java` | PDF·binary HWP 입력 시 Java 미설치 |
| `file_error` / `permission_error` | OS-level 파일 문제 |
| `apply_failed` / `runtime_error` | 그 외 라이브러리 예외 |

## 자체 흡수한 함정 (챗봇은 신경 쓸 필요 없음)

1. **컨테이너/자식 편집 충돌 자동 해소** — 단락과 셀(또는 그 안의 run)을 동시에 편집해도 우선순위 결정 후 충돌 항목을 `skipped_redundant_edits`로 안내.
2. **중복 target_id 사전 검사** — 챗봇이 같은 위치에 두 번 보내면 즉시 `duplicate_target_id` 응답.
3. **출력 확장자 검증** — `.pdf`로 잘못 보내도 `output_extension_mismatch`로 안전 거절.
4. **방어적 타입 처리** — `edits=None` / `edits="string"` 모두 깨끗하게 처리.
5. **EAW 인지 길이 가드레일** — 한글 한 글자는 2 display cell로 계산해서 `length_warnings`에 정확히 반영.
6. **document-processor `_edited` 누적 버그 monkey-patch** — pip 재설치 후에도 idempotent하게 유지.
7. **in-place 덮어쓰기** — `template_path == output_path` 케이스에서 임시 파일 경유하여 안전 처리.

## 프로젝트 구조

```
report-mcp/
├── src/report_mcp/
│   ├── server.py        FastMCP wiring + 4개 도구 정의
│   ├── documents.py     TemplateReader 클래스 (읽기 API)
│   ├── pipeline.py      FillPipeline 클래스 (검증→필터→적용)
│   ├── length.py        LengthGuardrail 클래스 (EAW 폭 계산)
│   ├── errors.py        ExceptionClassifier 클래스
│   ├── responses.py     ok/error/not_found 팩토리
│   └── patches.py       document-processor monkey-patch
├── tests/               10개 probe·test 스위트
├── archive/             일회용 데모 스크립트
├── patches/             document-processor 패치 파일
└── .claude/             Claude Code MCP 등록 설정
```

## 테스트

```powershell
.\.venv\Scripts\Activate.ps1
foreach ($f in Get-ChildItem tests\*.py) { python $f.FullName }
```

10개 스위트 항목:

- `verify_patch_no_batching.py` — 라이브러리 패치 검증 (32 edits 단일 호출)
- `probe_edge_cases.py` — 빈 편집 / 중복 / 미지 target / 제자리 덮어쓰기 등 10건
- `probe_pdf_input.py` — Java 없을 때 PDF 입력의 깨끗한 거절
- `probe_round_two/three/four/five.py` — describe + StructuralEdit + StyleEdit + MCP transport + stdout 청정 등 누적 검증
- `probe_real_defects.py` — 6대 본질적 결함 회귀 방지
- `test_mcp_production_ready.py` — 서버 instructions / 도구 docstring / batching / cell-conflict 등 게이트
- `test_length_guardrail.py` — EAW 길이 경고

## 알려진 제약

- **HWP (binary, .hwp)**: Java 11+ 필요. 출력은 HWPX로만 가능.
- **PDF**: 입력만 가능. 출력 불가능 (라이브러리 미지원). DOCX로 저장하도록 요청 권장.
- **이미지 제거**: 현재 미지원. 라이브러리에 `remove_image` 구조 편집 없음.
- **표시 폭 휴리스틱**: `max_recommended_chars`는 EAW 기반 근사. 비례 글꼴에서는 정확도 한계.

## 라이선스 / 의존 라이브러리

이 프로젝트의 핵심 기능은 [CGINSIDE-ROOKIES/document-processor](https://github.com/CGINSIDE-ROOKIES/document-processor)에 의존합니다. 사내 레포 접근 권한이 필요합니다.

## 관련 문서

- [Model Context Protocol 공식](https://modelcontextprotocol.io/)
- [Anthropic FastMCP SDK](https://github.com/modelcontextprotocol/python-sdk)
