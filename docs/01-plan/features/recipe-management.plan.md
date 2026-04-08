# Recipe Management Enhancement Plan

> 레시피 조회/복제/내보내기 기능 강화 계획서

## 1. Overview

| Item | Detail |
|------|--------|
| Feature | recipe-management |
| Priority | High |
| Base | IRMS v0.2 (2026-04-08, management 탭 기반) |
| Goal | 제품별 레시피 이력 조회, 이전 레시피 복제 등록, 엑셀 연동 강화 |

## 2. Problem Statement

현재 IRMS의 레시피 등록은 엑셀 복사 → 붙여넣기 → Validate → Register 단방향 흐름만 지원한다.
이전에 등록한 레시피를 다시 확인하려면 이력 목록에서 제품명을 검색해야 하며,
상세 재료/배합량을 한 눈에 볼 수 없고, 이전 레시피를 기반으로 수정 등록하는 기능이 없다.

### 현재 Pain Points

1. **조회 불편** - 이력 목록이 플랫 리스트로만 표시되어 같은 제품의 레시피 변경 이력 추적 어려움
2. **재입력 비용** - 비슷한 레시피를 다시 등록할 때 엑셀에서 처음부터 복사해야 함
3. **데이터 활용 제한** - IRMS에 등록된 데이터를 엑셀로 가져가는 방법이 없음

## 3. Feature Items

### 3.1 제품별 레시피 상세 조회

| Item | Detail |
|------|--------|
| 목표 | 제품명 선택 시 해당 제품의 모든 레시피를 등록일순으로 스프레드시트 형태로 표시 |
| 위치 | management 페이지 내 새 탭 또는 이력 탭 확장 |
| 주요 기능 | 제품명 드롭다운/자동완성, 레시피별 재료-배합량 테이블, 등록일/상태/등록자 표시 |
| 관련 파일 | `recipe_routes.py`, `management.html`, `management.js` |
| DB 변경 | 없음 (기존 recipes + recipe_items 조인) |

### 3.2 이전 레시피 복제 → 수정 등록

| Item | Detail |
|------|--------|
| 목표 | 이력에서 레시피 선택 → 등록 스프레드시트에 데이터 로드 → 수정 후 새 레시피로 등록 |
| 위치 | 이력 조회에서 "복제" 버튼 → 등록 탭으로 전환 |
| 주요 기능 | 레시피 데이터를 스프레드시트에 자동 채움, revision_of 컬럼으로 원본 추적 |
| 관련 파일 | `recipe_routes.py`, `management.js`, `import_parser.py` |
| DB 변경 | `revision_of` 컬럼 활용 (이미 존재) |

### 3.3 IRMS → 엑셀 복사 (클립보드 내보내기)

| Item | Detail |
|------|--------|
| 목표 | 레시피 상세 데이터를 탭 구분 텍스트로 클립보드 복사 → 엑셀에 Ctrl+V |
| 위치 | 레시피 상세 조회에 "복사" 버튼 |
| 주요 기능 | 헤더 + 재료/배합량을 TSV 형식으로 클립보드 복사, 복사 완료 피드백 |
| 관련 파일 | `management.js` |
| DB 변경 | 없음 |

## 4. Scope

### In Scope
- 제품별 레시피 조회 API (`GET /api/recipes/by-product`)
- 레시피 상세 조회 API (`GET /api/recipes/{id}/detail`)
- 복제 시 스프레드시트 데이터 로드 (프론트엔드)
- 클립보드 복사 기능 (프론트엔드)
- revision_of 연결 (등록 시 원본 ID 저장)

### Out of Scope
- 레시피 직접 수정 (기존 레시피 내용 변경) - 복제 후 신규 등록으로 대체
- 엑셀 파일(.xlsx) 직접 다운로드 - 클립보드 복사로 대체
- 레시피 버전 비교 (diff) - 향후 검토

## 5. Dependencies

| Dependency | Status |
|------------|--------|
| recipes 테이블 | ✅ 존재 |
| recipe_items 테이블 | ✅ 존재 |
| revision_of 컬럼 | ✅ 존재 (미사용) |
| JSpreadsheet | ✅ 존재 (vendor/) |
| Clipboard API | ✅ 브라우저 내장 |

## 6. Implementation Order

```
1. [Backend]  제품별 레시피 조회 API
2. [Backend]  레시피 상세(재료 포함) 조회 API
3. [Frontend] 제품별 조회 UI (탭 또는 이력 확장)
4. [Frontend] 클립보드 복사 기능
5. [Frontend] 복제 → 스프레드시트 로드 기능
6. [Backend]  등록 시 revision_of 저장
```

## 7. Risk

| Risk | Impact | Mitigation |
|------|--------|------------|
| 대량 레시피 조회 시 성능 | 중 | 페이지네이션 또는 제품당 최근 N건 제한 |
| 클립보드 API 브라우저 호환 | 저 | HTTPS 또는 localhost에서만 동작, 폴백 textarea 제공 |
| 복제 데이터 파싱 오류 | 저 | 기존 import_parser 재사용, 이미 검증된 데이터 |
