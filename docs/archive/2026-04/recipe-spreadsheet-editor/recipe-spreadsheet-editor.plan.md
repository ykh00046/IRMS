# Recipe Spreadsheet Editor Plan

> 엑셀 기반 레시피 관리를 앱 내 스프레드시트 에디터로 대체하는 실험적 기능

## 1. Overview

| Item | Detail |
|------|--------|
| Feature | recipe-spreadsheet-editor |
| Priority | Medium |
| Base | Management 페이지 (JSpreadsheet CE 이미 통합됨) |
| Goal | 엑셀 파일 없이 앱 내에서 제품별 레시피를 시트 형태로 생성·편집·관리 |
| Approach | 실험적 도입 — 기존 클립보드 Import 기능 유지 |

## 2. Problem Statement

현재 레시피 등록 워크플로우:
1. **엑셀에서 레시피 작성** — 제품/위치/잉크 + 재료별 목표량, 수식(TOTAL, BINDER 등)
2. **영역 복사 → 앱에 붙여넣기** — JSpreadsheet에 Ctrl+V
3. **Validate → Register** — 서버에 등록

### Pain Points

1. **엑셀 의존** — 레시피 원본이 로컬 엑셀 파일에만 존재, 버전 관리 불가
2. **수식 유실** — 복사·붙여넣기 시 수식이 값으로 변환되어 재계산 불가
3. **제품 관리 분산** — 제품별 시트가 엑셀 파일에 흩어져 있음
4. **협업 불가** — 엑셀 파일을 공유해야 다른 PC에서 접근 가능

### 현재 엑셀 구조 분석

**powder.xlsx**: 단순 수치형
- 컬럼: 제품명, 위치, 잉크명 + 재료(RAVEN, BLACK, RED 등) 목표량(g)
- 수식 없음, 순수 데이터

**solution.xlsx**: 수식 포함
- 기본 컬럼 동일 + 계산 컬럼:
  - `TOTAL` = SUM(재료량)
  - `BINDER` = (D×0.75) + (E×0.28) + ... 가중합
  - `반사량`, `안료량`, `APB`, `Glycerol` = 각각 고유 수식
- 수식이 다른 재료량 셀을 참조

## 3. Feature Items

### 3.1 제품별 탭(시트) 관리

| Item | Detail |
|------|--------|
| 구현 | 제품별 탭으로 시트 전환 |
| 동작 | 탭 추가(제품 생성), 탭 삭제, 탭 이름 변경 |
| 저장 | 서버 DB에 제품 단위로 저장 |

### 3.2 행 편집 (테스트·레시피 항목)

| Item | Detail |
|------|--------|
| 구현 | 행 추가/삭제로 테스트(Test-1, Test-2 등) 관리 |
| 컬럼 | 위치(Position), 잉크명(Ink) + 재료 컬럼들 |
| 유효성 | 음수 불가, 재료명 자동 완성 |

### 3.3 서버 사이드 수식 계산

| Item | Detail |
|------|--------|
| 구현 | 수식 정의를 서버에 저장, 값 변경 시 계산 결과 반환 |
| 수식 타입 | SUM, 가중합(weighted sum), 사용자 정의 |
| 적용 | TOTAL, BINDER, 반사량, 안료량 등 계산 컬럼 |
| 표시 | 계산 컬럼은 읽기 전용, 배경색 구분 |

### 3.4 레시피 등록 연계

| Item | Detail |
|------|--------|
| 구현 | 시트에서 행 선택 → 기존 Register 플로우로 전달 |
| 호환 | 기존 Validate/Register API 재사용 |
| 전환 | "시트에서 등록" 버튼으로 선택 행을 Import 탭에 로드 |

### 3.5 기존 기능 유지

| Item | Detail |
|------|--------|
| Import 탭 | 클립보드 붙여넣기 → Validate → Register (변경 없음) |
| History 탭 | 등록 이력 조회 (변경 없음) |
| Lookup 탭 | 레시피 조회 (변경 없음) |

## 4. Scope

### 4.1 In Scope

- [x] Management 페이지에 "레시피 편집" 탭 추가
- [x] 제품별 탭(시트) CRUD
- [x] 행(테스트) 추가/삭제/편집
- [x] 서버 사이드 수식 계산 (TOTAL, BINDER 등)
- [x] 편집 내용 서버 저장/불러오기
- [x] 시트 → 레시피 등록 연계
- [x] 기존 Import/History/Lookup 탭 유지

### 4.2 Out of Scope

- 엑셀 파일 직접 업로드/다운로드 (향후 확장)
- 복잡한 셀 참조 수식 (A1:B5 형태)
- 셀 서식 지정 (색상, 폰트 등)
- 다중 사용자 동시 편집 (locking만 고려)
- 모바일 최적화

## 5. Requirements

### 5.1 Functional Requirements

| ID | Requirement | Priority |
|----|-------------|----------|
| FR-01 | 제품별 탭으로 시트 전환 | High |
| FR-02 | 탭(제품) 추가/삭제/이름변경 | High |
| FR-03 | 행(테스트) 추가/삭제 | High |
| FR-04 | 셀 값 편집 및 자동 저장 | High |
| FR-05 | 수식 컬럼 서버 계산 및 표시 | High |
| FR-06 | 선택 행을 Import 탭으로 전달 | Medium |
| FR-07 | 재료 컬럼 자동 구성 (materials 테이블 기반) | Medium |
| FR-08 | 엑셀로 복사 (클립보드 내보내기) | Low |

### 5.2 Non-Functional Requirements

| Category | Criteria |
|----------|----------|
| Performance | 수식 계산 응답 < 500ms |
| 호환성 | JSpreadsheet CE v4 기반 (이미 프로젝트에 포함) |
| 데이터 | SQLite 테이블로 저장 (새 테이블 추가) |

## 6. Architecture Considerations

### 6.1 Project Level

| Level | Selected |
|-------|:--------:|
| **Dynamic** | ✅ |

기존 프로젝트 구조(FastAPI + Jinja2 SSR + SQLite) 그대로 확장.

### 6.2 Key Decisions

| Decision | Selected | Rationale |
|----------|----------|-----------|
| 스프레드시트 UI | JSpreadsheet CE | 이미 프로젝트에 통합됨 |
| 수식 엔진 | 서버 사이드 Python | 브라우저에 수식 노출 방지, 복잡 수식 지원 |
| 데이터 저장 | SQLite 신규 테이블 | 기존 DB 활용, 마이그레이션 추가 |
| 저장 방식 | 명시적 저장 버튼 | 자동 저장은 실험 단계에서 과도 |

### 6.3 데이터 모델 (초안)

```
spreadsheet_products (제품/시트)
├── id, name, created_at, updated_at
│
spreadsheet_columns (컬럼 정의)
├── id, product_id, name, col_index, col_type (material|formula|text)
├── formula_expr (NULL or "SUM", "WEIGHTED:0.75,0.28,...")
│
spreadsheet_rows (행/테스트)
├── id, product_id, row_index, position, ink_name
│
spreadsheet_cells (셀 값)
├── id, row_id, column_id, value_numeric, value_text
```

## 7. Risks and Mitigation

| Risk | Impact | Mitigation |
|------|--------|------------|
| JSpreadsheet CE 기능 한계 | Medium | 필요 시 읽기 전용 컬럼, 커스텀 렌더러로 보완 |
| 수식 복잡도 증가 | Medium | 초기에는 SUM, 가중합만 지원, 점진 확장 |
| 기존 Import 플로우 충돌 | Low | 별도 탭으로 분리, 기존 코드 미수정 |
| 데이터 마이그레이션 | Low | 신규 테이블만 추가, 기존 recipes 테이블 무관 |

## 8. Next Steps

1. [ ] Design 문서 작성 (`recipe-spreadsheet-editor.design.md`)
2. [ ] DB 스키마 마이그레이션 구현
3. [ ] API 엔드포인트 설계 및 구현
4. [ ] Management 페이지 "레시피 편집" 탭 UI 구현
5. [ ] 수식 엔진 구현
6. [ ] 시트 → Import 연계 구현

## Version History

| Version | Date | Changes | Author |
|---------|------|---------|--------|
| 0.1 | 2026-04-09 | Initial draft | Claude |
