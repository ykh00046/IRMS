# formula-excel-style Completion Report

> **Feature**: 엑셀 스타일 수식 입력 방식 전환
>
> **Project**: IRMS
> **Date**: 2026-04-10
> **Match Rate**: 100% (56/56 items)
> **Status**: Completed

---

## 1. Summary

스프레드시트 에디터의 수식 입력 방식을 JSON 기반 컬럼 단위 설정에서 엑셀 스타일 셀 단위(`=A1+B1`) 입력으로 전환했다. 현장 운영자가 별도 학습 없이 익숙한 엑셀 문법으로 수식을 사용할 수 있게 되었다.

---

## 2. What Changed

### 2.1 Before → After

| 항목 | Before (JSON 방식) | After (엑셀 스타일) |
|------|-------------------|-------------------|
| 수식 범위 | 컬럼 단위 (전체 컬럼이 수식) | 셀 단위 (개별 셀에 수식) |
| 입력 방법 | 컬럼 모달 → 타입 선택 → JSON 입력 | 셀에 `=B1+C1` 직접 입력 |
| SUM | `{"sourceColumns":[3,4,5]}` | `=SUM(B1:E1)` |
| 가중합 | `{"weights":{"3":0.75,"4":0.28}}` | `=B1*0.75+C1*0.28` |
| 사용자 난이도 | JSON 문법 + 컬럼 인덱스 필요 | 엑셀 경험만으로 가능 |

### 2.2 Supported Syntax

```
=A1+B1          산술 연산
=B1*0.75        상수 곱셈
=(A1+B1)*C1     괄호
=SUM(B1:E1)     범위 합계
=ROUND(B1/3, 2) 반올림
```

---

## 3. Implementation Details

### 3.1 Files Changed (7 files)

| File | Lines | Change |
|------|-------|--------|
| `src/routers/spreadsheet_formulas.py` | 247 | 전면 재작성 — 엑셀식 AST 파서 |
| `src/routers/spreadsheet_routes.py` | 361 | 저장/로드 로직 변경, `/calculate` 삭제 |
| `src/database.py` | +12 | 마이그레이션: formula 컬럼 → numeric |
| `static/js/spreadsheet_editor.js` | 498 | formulaMap, 수식 감지/편집/저장 |
| `static/js/common.js` | -5 | `ssCalculate()` 제거 |
| `templates/management.html` | -14 | 수식 설정 UI 제거 |
| `static/css/spreadsheet_editor.css` | -12 | formula 관련 CSS 제거 |

### 3.2 Architecture

```
사용자 입력: =B1+C1
     ↓
셀 값 그대로 DB 저장 (ss_cells.value = "=B1+C1")
     ↓
로드/저장 시 서버 계산:
  spreadsheet_formulas.evaluate_row()
    → is_formula() 감지
    → _rewrite_expression() 셀 참조 변환
    → ast.parse() + _eval_node() 안전 계산
     ↓
응답: {formula: "=B1+C1", display: "300"}
     ↓
프론트: display 표시 + 배경색 + 편집 시 formula 원문
```

### 3.3 Security

- AST 기반 파서 유지 (eval 미사용)
- 연산자 화이트리스트: `+`, `-`, `*`, `/`, 단항 `-/+`
- 함수 화이트리스트: `SUM`, `ROUND` only
- 수식 길이 제한: 200자
- 0 나누기 / 문법 오류 → `#ERR`

---

## 4. PDCA Cycle Summary

| Phase | Date | Result |
|-------|------|--------|
| Plan | 2026-04-10 | 9개 FR 정의, 7개 파일 변경 범위 |
| Design | 2026-04-10 | 파서 설계, API 응답 형식, 구현 순서 |
| Do | 2026-04-10 | 6단계 구현 완료 |
| Check | 2026-04-10 | 100% (56/56 항목 일치) |
| Report | 2026-04-10 | 본 문서 |

---

## 5. Deleted/Removed

- `CalcRequest` Pydantic 모델
- `POST /spreadsheet/calculate` 엔드포인트
- `ssCalculate()` JS 함수
- `ColumnCreate.formulaType` / `formulaParams` 필드
- `colType='formula'` 허용
- 컬럼 모달 수식 설정 UI (`ss-formula-config`, `ss-formula-type`, `ss-formula-params`)
- `.ss-formula-cell`, `.ss-formula-config` CSS

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 1.0 | 2026-04-10 | Initial report |
