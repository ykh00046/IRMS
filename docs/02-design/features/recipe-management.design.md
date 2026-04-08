# Recipe Management Enhancement Design

> 레시피 조회/복제/내보내기 기능 상세 설계서

## 1. Overview

| Item | Detail |
|------|--------|
| Feature | recipe-management |
| Plan | `docs/01-plan/features/recipe-management.plan.md` |
| Scope | 3개 기능: 제품별 조회, 복제 등록, 클립보드 내보내기 |

## 2. API Design

### 2.1 제품명 목록 조회

```
GET /api/recipes/products
```

**Response:**
```json
{
  "items": ["제품A", "제품B", "제품C"],
  "total": 3
}
```

**SQL:**
```sql
SELECT DISTINCT product_name
FROM recipes
ORDER BY product_name ASC
```

**위치:** `recipe_routes.py` → `operator_router`에 추가

---

### 2.2 제품별 레시피 조회

```
GET /api/recipes/by-product?product_name=제품A&limit=50
```

**Query Params:**
| Param | Type | Default | Description |
|-------|------|---------|-------------|
| product_name | string (required) | - | 제품명 (정확히 일치) |
| limit | int | 50 | 최대 조회 수 |

**Response:**
```json
{
  "product_name": "제품A",
  "items": [
    {
      "id": 10,
      "product_name": "제품A",
      "position": "1도",
      "ink_name": "INK-001",
      "status": "completed",
      "created_by": "매니저A",
      "created_at": "2026-04-07T09:00:00Z",
      "completed_at": "2026-04-07T15:00:00Z",
      "revision_of": null,
      "items": [
        {
          "material_id": 1,
          "material_name": "카본블랙",
          "unit": "g",
          "value": 150.0
        },
        {
          "material_id": 2,
          "material_name": "BYK-199",
          "unit": "g",
          "value": 30.5
        }
      ]
    }
  ],
  "total": 5
}
```

**SQL:**
```sql
SELECT r.id, r.product_name, r.position, r.ink_name, r.status,
       r.created_by, r.created_at, r.completed_at, r.revision_of
FROM recipes r
WHERE r.product_name = ?
ORDER BY r.created_at DESC, r.id DESC
LIMIT ?
```

재료 항목은 기존 `_fetch_recipe_items()` 헬퍼 재사용.

**위치:** `recipe_routes.py` → `operator_router`에 추가

---

### 2.3 레시피 상세 조회 (단건)

```
GET /api/recipes/{recipe_id}/detail
```

**Response:**
```json
{
  "id": 10,
  "product_name": "제품A",
  "position": "1도",
  "ink_name": "INK-001",
  "status": "completed",
  "created_by": "매니저A",
  "created_at": "2026-04-07T09:00:00Z",
  "revision_of": null,
  "items": [
    {
      "material_id": 1,
      "material_name": "카본블랙",
      "unit": "g",
      "value": 150.0,
      "measured_at": "2026-04-07T14:30:00Z",
      "measured_by": "작업자A"
    }
  ],
  "tsv": "제품명\t위치\t잉크명\t카본블랙\tBYK-199\n제품A\t1도\tINK-001\t150.0\t30.5"
}
```

`tsv` 필드: 프론트에서 클립보드 복사 및 스프레드시트 로드에 바로 사용할 수 있는 탭 구분 텍스트.

**TSV 생성 로직:**
```
행1 (헤더): 제품명 \t 위치 \t 잉크명 \t [재료명1] \t [재료명2] \t ...
행2 (데이터): 제품A \t 1도 \t INK-001 \t 150.0 \t 30.5 \t ...
```

**위치:** `recipe_routes.py` → `operator_router`에 추가

---

### 2.4 등록 시 revision_of 지원

기존 `POST /api/recipes/import` 수정:

**Request body 변경:**
```json
{
  "raw_text": "...",
  "revision_of": 10
}
```

**변경 사항:**
- `ImportRequest` 모델에 `revision_of: int | None = None` 추가
- INSERT 시 `revision_of` 값 저장
- audit_log details에 `revision_of` 포함

## 3. Frontend Design

### 3.1 탭 구조 변경

현재:
```
[레시피 등록] [등록 이력]
```

변경:
```
[레시피 등록] [등록 이력] [레시피 조회]
```

### 3.2 레시피 조회 탭 (`tab-lookup`)

```
┌─────────────────────────────────────────────────────┐
│  레시피 조회                                          │
│                                                      │
│  제품명: [____자동완성 드롭다운____] [검색]             │
│                                                      │
│  ┌────┬──────┬────────┬────────┬────────┬────────┐  │
│  │ ID │ 잉크명 │ 카본블랙 │ BYK-199 │ 등록일   │ 상태  │  │
│  ├────┼──────┼────────┼────────┼────────┼────────┤  │
│  │ 10 │INK-01│ 150.0  │  30.5  │ 04-07  │ 완료  │  │
│  │  8 │INK-01│ 148.0  │  31.0  │ 04-05  │ 완료  │  │
│  │  5 │INK-01│ 145.0  │  29.0  │ 04-01  │ 취소  │  │
│  └────┴──────┴────────┴────────┴────────┴────────┘  │
│                                                      │
│  선택된 레시피: #10                                    │
│  [엑셀로 복사]  [복제하여 등록]                          │
└─────────────────────────────────────────────────────┘
```

**테이블 특징:**
- 열: 고정 컬럼(ID, 위치, 잉크명, 상태, 등록일) + 동적 컬럼(재료명별)
- 같은 제품의 레시피를 한 눈에 비교 가능
- 행 클릭 시 선택 → 하단 버튼 활성화

### 3.3 엑셀로 복사 기능

**흐름:**
```
행 선택 → [엑셀로 복사] 클릭
  → GET /api/recipes/{id}/detail (tsv 포함)
  → navigator.clipboard.writeText(tsv)
  → "클립보드에 복사되었습니다" 토스트
  → 엑셀에서 Ctrl+V
```

**폴백 (Clipboard API 미지원 시):**
```
숨겨진 textarea에 tsv 삽입 → select() → document.execCommand('copy')
```

### 3.4 복제하여 등록 기능

**흐름:**
```
행 선택 → [복제하여 등록] 클릭
  → GET /api/recipes/{id}/detail (tsv 포함)
  → 탭을 "레시피 등록"으로 전환
  → 스프레드시트에 tsv 데이터 로드 (setData)
  → revision_of = 선택한 recipe ID 저장 (JS 변수)
  → 사용자가 수정 → Validate → Register
  → import API 호출 시 revision_of 포함
```

**스프레드시트 데이터 로드:**
```javascript
function loadTsvToSpreadsheet(tsv, sourceRecipeId) {
  const rows = tsv.split('\n').map(r => r.split('\t'));
  const worksheet = getActiveWorksheet();
  
  // 기존 데이터 클리어 후 새 데이터 설정
  destroySpreadsheet();
  // rows 데이터로 새 스프레드시트 초기화
  initSpreadsheetWithData(rows);
  
  pendingRevisionOf = sourceRecipeId;
  IRMS.notify("레시피를 불러왔습니다. 수정 후 Validate → Register 하세요.", "info");
}
```

### 3.5 등록 이력 테이블 확장

기존 이력 테이블의 행 클릭 시 간단한 상세 정보 표시:

```
행 클릭 → 해당 행 아래에 재료/배합량 펼침 (accordion)
  [엑셀로 복사]  [복제하여 등록]
```

이력 탭에서도 복사/복제 접근 가능하도록.

## 4. Data Flow

### 4.1 제품별 조회 흐름

```
제품명 입력 → GET /api/recipes/products (자동완성)
  → 선택 → GET /api/recipes/by-product?product_name=X
  → 피벗 테이블 렌더링 (재료를 열 헤더로)
```

### 4.2 복제 등록 흐름

```
레시피 선택 → GET /api/recipes/{id}/detail
  → tsv를 스프레드시트에 로드
  → 사용자 수정
  → Validate (기존 handlePreview 재사용)
  → Register (기존 handleRegister + revision_of)
  → POST /api/recipes/import { raw_text, revision_of }
```

### 4.3 클립보드 복사 흐름

```
레시피 선택 → GET /api/recipes/{id}/detail
  → response.tsv → navigator.clipboard.writeText()
  → 토스트 알림
```

## 5. File Changes

### Backend (Python)

| File | Change |
|------|--------|
| `src/routers/recipe_routes.py` | `GET /recipes/products` 추가 |
| `src/routers/recipe_routes.py` | `GET /recipes/by-product` 추가 |
| `src/routers/recipe_routes.py` | `GET /recipes/{id}/detail` 추가 (tsv 생성 포함) |
| `src/routers/recipe_routes.py` | `POST /recipes/import` 수정 (revision_of 지원) |
| `src/routers/models.py` | `ImportRequest`에 `revision_of` 필드 추가 |

### Frontend (HTML/CSS/JS)

| File | Change |
|------|--------|
| `templates/management.html` | "레시피 조회" 탭 추가, lookup UI 마크업 |
| `static/js/management.js` | 조회 탭 로직, 복사/복제 함수 |
| `static/js/common.js` | `getProducts()`, `getRecipesByProduct()`, `getRecipeDetail()` API 함수 추가 |
| `static/css/management.css` | 조회 테이블, 선택 행 스타일 |

### DB 변경: 없음
- `revision_of` 컬럼 이미 존재
- 신규 테이블/인덱스 불필요

## 6. Implementation Order

```
Step 1: [Backend]  GET /recipes/products API
Step 2: [Backend]  GET /recipes/by-product API
Step 3: [Backend]  GET /recipes/{id}/detail API (tsv 생성)
Step 4: [Backend]  POST /recipes/import revision_of 지원
Step 5: [Frontend] common.js API 함수 3개 추가
Step 6: [Frontend] management.html 조회 탭 마크업
Step 7: [Frontend] management.css 조회 탭 스타일
Step 8: [Frontend] management.js 조회 탭 로직 (검색, 테이블, 선택)
Step 9: [Frontend] 클립보드 복사 기능
Step 10: [Frontend] 복제 → 스프레드시트 로드 기능
Step 11: [Frontend] 이력 탭 행 클릭 확장 (accordion)
```

## 7. Edge Cases

| Case | Handling |
|------|----------|
| 제품명이 없는 경우 | products API가 빈 배열 반환, UI에 안내 메시지 |
| 레시피에 재료가 0개 | detail API에서 items 빈 배열, tsv는 헤더만 |
| 재료 종류가 레시피마다 다름 | 피벗 테이블에서 모든 재료를 열로 포함, 없는 값은 빈칸 |
| 복제 후 수정 없이 등록 | 허용 (동일 배합 재등록 가능, raw_input_hash로 추적 가능) |
| Clipboard API 미지원 | textarea + execCommand 폴백 |
| 삭제된 재료 참조 | material JOIN에서 is_active 무관하게 표시 (이력이므로) |
