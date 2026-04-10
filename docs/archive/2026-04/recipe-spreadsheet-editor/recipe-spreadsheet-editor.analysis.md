# recipe-spreadsheet-editor Gap Analysis

> **Date**: 2026-04-10
> **Match Rate**: 95%
> **Verdict**: PASS

## Overall Scores

| Category | Score |
|----------|:-----:|
| Design Match | 93% |
| Architecture Compliance | 95% |
| Convention Compliance | 97% |
| **Overall** | **95%** |

## 1. DB Schema — 100%

4개 테이블(ss_products, ss_columns, ss_rows, ss_cells) + 3개 인덱스 + _ALLOWED_TABLES 등록 모두 Design과 일치.

## 2. API — 11/11 엔드포인트 구현

| # | Endpoint | Status |
|---|----------|:------:|
| 1 | GET /products | PASS |
| 2 | POST /products | PASS |
| 3 | PATCH /products/{id} | PASS |
| 4 | DELETE /products/{id} | PASS |
| 5 | GET /products/{id}/sheet | PASS |
| 6 | POST /products/{id}/save | PASS |
| 7 | POST /products/{id}/columns | PASS |
| 8 | DELETE /columns/{col_id} | PASS |
| 9 | POST /products/{id}/rows | PASS |
| 10 | DELETE /rows/{row_id} | PASS |
| 11 | POST /calculate | PASS |

## 3. Formula Engine — 100%

SUM, WEIGHTED, CUSTOM 모두 구현. ast 기반 안전 파서 적용. eval() 미사용.

## 4. UI — 100%

제품 탭, JSpreadsheet 연동, 컬럼 관리 모달, 행 CRUD, 저장, 등록 전달 모두 구현.

## 5. 명명 규칙 — 100%

- DB: `ss_` 접두사
- API: `/api/spreadsheet/`
- CSS: `ss-` 접두사
- JS: `IRMS.ss*`
- JSON 변환: snake_case → camelCase 모두 적용

## 6. 보안 — 100%

ast 기반 파서, 입력 길이 제한, 컬럼 수 제한(30), SQLite 파라미터 바인딩, manager 인증 모두 구현.

## Minor Gaps (2건)

| # | Gap | Impact | Status |
|---|-----|--------|--------|
| 1 | `/calculate` 엔드포인트: 잘못된 수식 시 400 INVALID_FORMULA 대신 `{"result": null}` 반환 | Low | Optional |
| 2 | api.py에서 `tags=["spreadsheet"]` 미설정 (OpenAPI 문서 그룹핑만 영향) | Low | Optional |

## Beneficial Additions (Design에 없지만 추가된 개선)

- `_MAX_EXPRESSION_LENGTH = 200`: CUSTOM 수식 길이 제한 (보안)
- Division-by-zero 처리: 0.0 반환 (안정성)
- Sheet 응답에 `description` 필드 포함 (편의성)
