# formula-excel-style Planning Document

> **Summary**: 수식 입력 방식을 JSON 기반에서 엑셀 스타일(`=A1+B1`, `=SUM(A1:C1)`)로 전환
>
> **Project**: IRMS
> **Date**: 2026-04-10
> **Status**: Draft

---

## 1. Overview

### 1.1 Purpose

현재 스프레드시트 에디터의 수식 기능은 컬럼 단위로 수식 타입(SUM/WEIGHTED/CUSTOM)을 지정하고 JSON 파라미터를 직접 입력해야 한다. 이 방식은 개발자가 아닌 현장 운영자에게 사실상 사용 불가능하다.

엑셀에서 이미 익숙한 `=셀참조+연산` 방식으로 바꾸어, 별도 학습 없이 수식을 사용할 수 있도록 한다.

### 1.2 Background

- 현장 담당자/책임자는 엑셀 사용 경험이 있으며, `=SUM(A1:C1)` 같은 문법에 익숙하다.
- 현재 방식은 컬럼 전체를 수식 타입으로 지정 → JSON 파라미터 입력 → 저장 시 서버 계산의 3단계로, 진입 장벽이 높다.
- 수식이 컬럼 단위이므로 같은 컬럼에서 행별로 다른 수식을 쓸 수 없다.

### 1.3 Related Documents

- 이전 PDCA: `docs/archive/2026-04/recipe-spreadsheet-editor/`

---

## 2. Scope

### 2.1 In Scope

- [x] 셀 단위 수식 입력 (`=`로 시작)
- [x] 엑셀식 셀 참조 (`A1`, `B3` 등)
- [x] 기본 산술 연산 (`+`, `-`, `*`, `/`, 괄호)
- [x] 기본 함수: `SUM`, `ROUND`
- [x] 수식 셀 시각적 구분 (배경색/읽기전용)
- [x] 기존 수식 컬럼 타입/모달 UI 제거
- [x] 서버 사이드 수식 계산 유지 (보안)

### 2.2 Out of Scope

- 셀 범위 참조 (`A1:A10` 같은 세로 범위) — 이 시트는 가로(행 내) 계산 중심
- 고급 함수 (VLOOKUP, IF, AVERAGE 등) — 필요 시 추후 추가
- 클라이언트 사이드 실시간 미리보기 — 저장 시 서버 계산으로 충분

---

## 3. Requirements

### 3.1 Functional Requirements

| ID | Requirement | Priority | Status |
|----|-------------|----------|--------|
| FR-01 | 셀에 `=`로 시작하는 값 입력 시 수식으로 인식 | High | Pending |
| FR-02 | 엑셀식 셀 참조 지원 (`A1` = 0행 0열, `B3` = 2행 1열) | High | Pending |
| FR-03 | 같은 행 내 셀 참조로 가로 계산 (`=A1+B1+C1`) | High | Pending |
| FR-04 | `SUM(B1:E1)` — 같은 행 내 범위 합산 | High | Pending |
| FR-05 | `ROUND(expr, digits)` — 반올림 | Medium | Pending |
| FR-06 | 수식 셀은 저장 후 계산 결과 표시 + 배경색 구분 | High | Pending |
| FR-07 | 수식 셀 편집 시 수식 원문(`=A1+B1`) 표시 | High | Pending |
| FR-08 | 기존 "수식 컬럼" 타입 및 컬럼 모달 수식 설정 UI 제거 | High | Pending |
| FR-09 | 0으로 나누기, 잘못된 참조 등 오류 시 `#ERR` 표시 | Medium | Pending |

### 3.2 Non-Functional Requirements

| Category | Criteria | Measurement |
|----------|----------|-------------|
| Performance | 100행 수식 계산 < 500ms | 서버 응답 시간 |
| Security | AST 기반 안전한 파서 유지 (eval 미사용) | 코드 리뷰 |
| UX | 엑셀 경험자가 별도 안내 없이 사용 가능 | 현장 테스트 |

---

## 4. 변경 대상 파일

### 4.1 삭제/대폭 수정

| 파일 | 변경 내용 |
|------|-----------|
| `src/routers/spreadsheet_formulas.py` | 전면 재작성 — 엑셀식 파서로 교체 |
| `src/routers/spreadsheet_routes.py` | 수식 저장/계산 로직 변경 (컬럼 단위 → 셀 단위) |

### 4.2 UI 수정

| 파일 | 변경 내용 |
|------|-----------|
| `templates/management.html` | 컬럼 모달에서 수식 타입/파라미터 UI 제거 |
| `static/js/spreadsheet_editor.js` | 수식 셀 감지, 결과 표시, 수식 원문 편집 |
| `static/css/spreadsheet_editor.css` | `#ERR` 스타일 추가 |

### 4.3 DB 스키마 변경

| 테이블 | 변경 |
|--------|------|
| `ss_columns` | `formula_type`, `formula_params` 컬럼 미사용 (호환 유지, 값 무시) |
| `ss_cells` | `value` 필드에 수식 원문(`=A1+B1`)도 저장 |

---

## 5. 수식 파서 설계

### 5.1 셀 참조 규칙

```
열: A=0, B=1, C=2, ..., Z=25, AA=26, ...
행: 1-based (엑셀과 동일)

예) B3 → colIndex=1, rowIndex=2
    같은 행 참조: =A1+B1+C1 (행 번호는 현재 행 기준으로 해석)
```

**중요**: 이 스프레드시트는 "레시피 계산"용이므로, 수식은 **같은 행 내 가로 계산**이 주 용도이다. `=A1+B1`에서 행 번호(`1`)는 "현재 행"을 의미하도록 처리한다. (실제 데이터가 몇 행이든 각 행의 수식은 자기 행 기준으로 계산)

### 5.2 지원 문법

```
리터럴:    123, 3.14
셀 참조:   A1, B1, AA1
산술:      +, -, *, /, (, )
함수:      SUM(B1:E1), ROUND(expr, n)
```

### 5.3 계산 흐름

```
1. 클라이언트: 셀에 "=B1*0.75+C1" 입력
2. 저장 시: 수식 원문 그대로 서버 전송
3. 서버: 각 행별로 수식 셀 감지 → 파싱 → 같은 행의 값으로 계산
4. 계산 결과를 DB에 별도 저장 (or 매 로드 시 재계산)
5. 클라이언트: 결과값 표시 + 셀 선택 시 수식 원문 표시
```

---

## 6. Success Criteria

### 6.1 Definition of Done

- [x] `=A1+B1` 형태의 수식을 셀에 직접 입력 가능
- [x] 저장 시 서버에서 계산, 결과값 표시
- [x] 수식 셀은 배경색으로 구분
- [x] 기존 수식 컬럼 타입/JSON 입력 UI 완전 제거
- [x] 기존 데이터 호환 (수식 컬럼이 있던 시트는 수식 제거 후 일반 값으로 전환)

---

## 7. Risks and Mitigation

| Risk | Impact | Mitigation |
|------|--------|------------|
| 순환 참조 (`=A1` → `=B1` → `=A1`) | 무한 루프 | 참조 깊이 제한 + 감지 시 `#ERR` 반환 |
| 기존 수식 컬럼 데이터 유실 | 중 | 마이그레이션: 기존 formula 타입 컬럼을 numeric으로 전환 |
| 다른 행 참조 시 복잡도 증가 | 중 | Scope 제한: 같은 행 내 참조만 지원 |

---

## 8. Next Steps

1. [ ] Design 문서 작성 (`/pdca design formula-excel-style`)
2. [ ] 구현
3. [ ] Gap 분석

---

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 0.1 | 2026-04-10 | Initial draft |
