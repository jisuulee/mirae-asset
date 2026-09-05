# Role 1 — DART Disclosure Preprocess

## Phase 5 최종 조립 및 검증

Phase 5는 Phase 2~4 canonical artifact를 수정하지 않고 4,204행 경량 index와
역할 2 인계 패키지를 만든다. 실행 중 26.6GB JSONL은 line-by-line으로 처리하며
기본값으로 merged dataset을 만들지 않는다.

```powershell
.\.venv\Scripts\python.exe scripts\assemble_final_dataset.py

.\.venv\Scripts\python.exe scripts\validate_final_artifacts.py `
  --project-root .
```

대형 통합 파일이 반드시 필요한 경우에만 `--materialize-merged`를 명시한다.
complete correction chain이 아닌 문서에는 latest/superseded boolean을 생성하지
않는다.

```text
data/final/
├── document_index.jsonl
└── artifact_manifest.json

reports/
├── final_quality_report.md
├── final_quality_summary.json
├── final_validation_failures.jsonl
└── final_known_limitations.json

handoff/role2/
├── README.md
├── data_contract.md
├── artifact_manifest.json
├── document_index.jsonl
├── sample_disclosures.jsonl
├── sample_document_index.jsonl
├── sample_selection.jsonl
├── known_limitations.md
└── integration_notes.md
```

미래에셋 AI Festival의 DART 기반 Disclosure Analyst Agent를 위한 역할 1 전처리 프로젝트다. Phase 1 코퍼스 프로파일링, Phase 2 통합 parser, Phase 3 canonical metadata, Phase 4 정정공시 계보, Phase 5 document index·품질 검증·역할 2 인계까지 구현한다. RAG, embedding, DB 적재, LLM 추출·요약은 역할 1 범위가 아니다.

## 환경

- 역할 1 개발·회귀 검증 Python **3.13.14**
- `lxml==6.1.1`: tolerant XML/HTML parsing
- `pypdf==6.16.1`: OCR 없는 PDF page text extraction
- VS Code 기본 interpreter: `.venv\Scripts\python.exe`

역할 1의 기준 환경은 Python 3.13.14다. 팀 저장소의 공용 runtime은 별도 설정을 따르며, 역할 1 의존성은 루트 `requirements.txt`의 `lxml`과 `pypdf` 항목으로 설치한다.

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe --version
```

## Phase 2 전체 실행

## Phase 4 정정공시 계보 실행

Phase 4는 Phase 2/3 파일을 다시 쓰지 않고 1,004건의 정정공시 sidecar와
chain graph를 생성한다. 기본 실행은 네트워크를 사용하지 않는 corpus-only
모드다.

```powershell
.\.venv\Scripts\python.exe scripts\resolve_corrections.py `
  --metadata '.\data\processed\metadata_normalized.jsonl' `
  --parsed '.\data\processed\parsed_disclosures.jsonl' `
  --output-dir '.\data\processed' `
  --report-dir '.\reports'

.\.venv\Scripts\python.exe scripts\validate_phase4_artifacts.py --project-root .
```

OpenDART 보조 조회는 `OPEN_DART_API_KEY`를 설정하고
`--enable-opendart`를 명시한 경우에만 동작한다. schema와 null/partial-chain
의미는 [correction_schema.md](correction_schema.md)를 참고한다.

`--data-root`는 압축을 푼 corpus 디렉터리와 원본 ZIP을 모두 지원한다. ZIP은 추출하지 않고 physical file 단위로 순회한다.

```powershell
.\.venv\Scripts\python.exe scripts\parse_corpus.py `
  --data-root '.\3.공시.zip' `
  --output-dir '.\data\processed' `
  --report-dir '.\reports' `
  --expected-document-count 4204
```

개발 중 특정 문서만 재현할 때는 `--doc-id`를 반복 지정한다. `--doc-id`와 `--limit`은 함께 사용할 수 없다.

```powershell
.\.venv\Scripts\python.exe scripts\parse_corpus.py `
  --data-root '.\3.공시.zip' `
  --output-dir "$env:TEMP\dart-parser-sample" `
  --report-dir "$env:TEMP\dart-parser-sample-reports" `
  --doc-id periodic_20260513000860
```

Phase 1 인벤토리는 기존 명령을 그대로 사용한다.

```powershell
.\.venv\Scripts\python.exe scripts\inspect_corpus.py --data-root <dataset_path>
```

## Phase 3 전체 실행

Phase 3는 26.6GB content를 재작성하지 않는다. 기존 JSONL을 binary line 단위로
한 번 스트리밍하면서 SHA-256, join, metadata completeness와 대표 샘플 offset을
함께 계산한다.

```powershell
.\.venv\Scripts\python.exe scripts\normalize_metadata.py `
  --data-root '.\3.공시.zip' `
  --parsed '.\data\processed\parsed_disclosures.jsonl' `
  --parse-warnings '.\data\processed\parse_warnings.jsonl' `
  --output-dir '.\data\processed' `
  --report-dir '.\reports' `
  --handoff-dir '.\handoff' `
  --expected-document-count 4204 `
  --expected-mismatch-count 745 `
  --sample-count 80
```

Canonical metadata source는 `manifest.jsonl`이다. `rcept_date`만 `rcept_dt`에서
엄격하게 형식 변환하며 source observation은 canonical 값을 덮어쓰지 않는다.

게시 후 artifact count·SHA-256과 sample 원본 행 일치를 독립적으로 재검증한다.

```powershell
.\.venv\Scripts\python.exe scripts\validate_phase3_artifacts.py --project-root .
```

## Phase 3 산출물

```text
data/processed/
└── metadata_normalized.jsonl

reports/
├── metadata_summary.json
├── metadata_report.md
└── metadata_mismatches.jsonl

handoff/
├── data_contract.md
├── metadata_normalized.jsonl
├── sample_disclosures.jsonl
├── sample_selection.jsonl
├── handoff_manifest.json
└── README.md
```

필드 의미, null semantics, safe filter와 join 규칙은
[data_contract.md](data_contract.md), additive 변경 내역은
[schema_changelog.md](schema_changelog.md)를 참고한다.

## Phase 2 산출물

```text
data/processed/
├── parsed_disclosures.jsonl  # manifest 1 row = JSONL 1 row
├── parse_warnings.jsonl      # logical/physical/auxiliary 구조화 경고
└── parse_failures.jsonl      # partial/failed 재현용 요약

reports/
├── parser_summary.json       # 기계 판독 품질 통계
└── parser_report.md          # 사람이 읽는 분석 및 명시적 보증/한계
```

전체 산출물은 대용량이며 `.gitignore` 대상이다. 원본 ZIP과 `data/raw/`는 읽기 전용으로 취급한다.

## 논리 문서와 schema

최종 단위는 physical file이 아니라 **manifest 한 행에 대응하는 disclosure 한 건**이다.

- `metadata`: manifest 권위값과 원본 manifest record
- `main_document`: canonical physical representation의 parse result
- `attachments`: 감사보고서·연결감사보고서 같은 실제 첨부
- `alternative_representations`: PDF+HTML 쌍의 비정본 표현. source metadata와 상태만 보존하고 content는 중복 기록하지 않음
- `source_files`: 모든 physical source의 경로, role, 실제 감지 형식, decoder, size, SHA-256, canonical 여부
- `parse_status`: `success`, `partial`, `failed`
- `warnings`: code, message, severity, provenance, details

`main_document.content`는 title, ordered block sequence, sections, tables, 파생 `body_text`를 가진다. 표는 raw cell text와 rowspan/colspan 및 cell provenance를 보존하며, 안전하게 확장 가능한 경우에만 `normalized_rows`를 만든다. 상세 schema와 provenance 의미는 [phase2_schema.md](phase2_schema.md)를 참고한다.

## Parser 원칙

- 확장자가 아니라 기존 content detector 결과로 XML/HTML/JSON/PDF parser를 선택한다.
- UTF-8 strict를 먼저 시도하고, 실패한 경우에만 선언 charset/cp949/euc-kr을 strict fallback으로 사용한다. `errors="ignore"`나 replacement decoding은 사용하지 않는다.
- `raw_*`는 decoder와 DOM을 거친 source-derived 문자열이고, 안전한 normalized view는 별도 필드다. physical byte 동일성은 `sha256`으로 검증한다.
- 숫자, 부호, 날짜, 단위, `-`, `N/A`, 빈 cell을 의미값으로 변환하거나 이전 cell로 채우지 않는다.
- heading/paragraph/list/table 순서와 table 위치를 block sequence로 보존한다.
- 불확실한 heading level은 `null`, 누락 title은 `null`과 warning으로 남긴다.
- PDF+HTML은 substantive viewer HTML을 우선하며 viewer shell이면 PDF fallback을 명시한다.
- 한 physical file 실패가 다음 파일이나 전체 corpus 실행을 중단하지 않는다.

## 추적성과 알려진 한계

title/section/block/list item/table/cell은 physical source path와 role, DOM-like path/source index 또는 PDF page locator를 가진다. 모든 physical source에는 SHA-256이 기록된다.

다만 `body_text`는 ordered blocks에서 만든 검색용 view이므로 **문자 범위 단위 역매핑은 제공하지 않는다**. 문장과 숫자는 원래 block 또는 table cell 단위로 추적한다. PDF fallback은 OCR 없이 page text만 보존하므로 PDF 표의 행·열/cell 구조는 복원하지 않는다. malformed markup의 tolerant recovery는 warning과 `partial`로 드러내며 성공으로 숨기지 않는다.

## 테스트

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

fixture와 통합 테스트는 형식 dispatch, encoding, 숫자·단위 보존, nested structure order, table spans, PDF page 경계, multi-file 조립, fallback, 원자적 게시, 실패 격리를 검증한다.

## 구조

```text
scripts/
├── inspect_corpus.py
├── parse_corpus.py
├── normalize_metadata.py
└── validate_phase3_artifacts.py

src/disclosure_preprocess/
├── models/          # 공통 IR과 provenance
├── normalizers/     # 보수적 text/table view
├── parsers/         # XML/HTML/JSON/PDF 및 content-based factory
├── pipeline.py      # manifest 단위 조립과 JSONL 생성
├── parser_reporting.py
├── metadata_normalization.py
├── metadata_pipeline.py
└── metadata_reporting.py
```
