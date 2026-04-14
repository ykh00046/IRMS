# Recipe Version History Design

> Plan: `docs/01-plan/features/recipe-version-history.plan.md`

## 0. Resolved Decisions

| # | Question | Decision |
|---|---|---|
| Q1 | 체인 탐색 | **순수 revision 체인만** (revision_of 연결 기반, product 문자열 매칭 없음) |
| Q2 | 독립 레시피 포함 | **체인에 속한 것만** — revision_of가 없으면 root 1개짜리 체인 |
| Q3 | 동시 비교 버전 수 | **3개 이상 지원** — 선택한 N개 버전을 가로로 나란히 표시 |

## 1. Data Model

DB 변경 **없음**. 기존 `recipes.revision_of INTEGER` 컬럼 활용.

### 체인 정의

- **Root**: `revision_of IS NULL`인 레시피 (원본)
- **Descendant**: `revision_of = parent.id`인 레시피
- **Chain**: root부터 시작하여 revision_of 연결로 이어지는 모든 레시피 집합 (트리 구조)
- 현재 구현은 선형 revision 체인(한 부모 → 한 자식)이 기본이나, 분기도 허용 (동일 레시피에서 여러 번 복제됨)

### Root 찾기 알고리즘

```python
def find_root(connection, recipe_id):
    current = recipe_id
    seen = set()
    while current and current not in seen:
        seen.add(current)
        row = connection.execute(
            "SELECT revision_of FROM recipes WHERE id = ?", (current,)
        ).fetchone()
        if not row or row["revision_of"] is None:
            return current
        current = int(row["revision_of"])
    return current  # 순환 방지
```

### Chain 전체 수집 (재귀 CTE)

```sql
WITH RECURSIVE chain(id) AS (
    SELECT ? AS id
    UNION ALL
    SELECT r.id FROM recipes r, chain c WHERE r.revision_of = c.id
)
SELECT r.*
FROM recipes r
WHERE r.id IN (SELECT id FROM chain)
ORDER BY r.created_at ASC, r.id ASC
```

- `?` = root_id
- SQLite는 WITH RECURSIVE를 지원
- ORDER BY created_at → 버전 라벨 `v1, v2, ...` 부여

## 2. API Endpoints

### 2.1 `GET /api/recipes/{id}/history` (operator)

```json
{
  "root_id": 42,
  "current_id": 58,
  "items": [
    {
      "id": 42,
      "version_label": "v1",
      "product_name": "...",
      "position": "...",
      "ink_name": "...",
      "created_by": "홍길동",
      "created_at": "2026-04-10T...",
      "item_count": 13,
      "is_current": false,
      "is_root": true,
      "revision_of": null,
      "remark": null
    },
    { "id": 51, "version_label": "v2", ...},
    { "id": 58, "version_label": "v3", "is_current": true, ...}
  ]
}
```

- `current` = 체인에서 `created_at` 최대
- `version_label` = chain 내 시간순 index (1-based, `v{index}`)

### 2.2 `GET /api/recipes/history/compare?ids=42,51,58` (operator)

다중 버전 비교. Q3 결정에 따라 N개 버전 지원.

```json
{
  "versions": [
    { "id": 42, "version_label": "v1", "product_name": "...", ... },
    { "id": 51, "version_label": "v2", ... },
    { "id": 58, "version_label": "v3", ... }
  ],
  "materials": [
    {
      "material_id": 7,
      "material_name": "PL-835-1",
      "values": [
        { "version_id": 42, "value_weight": 30.0, "value_text": null },
        { "version_id": 51, "value_weight": 32.0, "value_text": null },
        { "version_id": 58, "value_weight": 32.0, "value_text": "(HR10)" }
      ],
      "change_status": "modified"  // 'same' | 'modified' | 'partial'
    }
  ]
}
```

### 비교 규칙

- 각 material_id에 대해 모든 버전의 값을 수집
- `change_status`:
  - `same`: 모든 버전에서 value가 동일
  - `modified`: 값이 변경되었고, 모든 버전에 존재
  - `partial`: 일부 버전에만 존재 (추가 또는 제거)
- material 목록 = 체인에 등장한 모든 material_id의 합집합, 이름 순 정렬

### 유효성

- `ids`는 2개 이상 50개 이하
- 모든 id가 동일 체인에 속해야 함 (root가 같음) — 아닐 경우 400
- 권한: operator

## 3. UI — Management Lookup 탭 확장

### 3.1 버전 이력 버튼

기존 `lookup-actions` 버튼 행에 추가:
```html
<button id="lookup-history-btn" class="btn" disabled>버전 이력</button>
```

- 레시피 선택 시 활성화
- 클릭 → 이력 모달 open

### 3.2 버전 이력 모달

```
┌─ 버전 이력 ─────────────────────────────────── X ┐
│ 제품명 / 위치 / 잉크명                            │
│                                                    │
│ ┌────────────────────────────────────────────────┐│
│ │ ☐  v1  2026-04-10  홍길동  13항목               ││
│ │ ☐  v2  2026-04-12  김철수  13항목               ││
│ │ ☑  v3  2026-04-14  홍길동  14항목  [현재 사용] ││
│ └────────────────────────────────────────────────┘│
│                                                    │
│  [선택한 버전 비교]  [v3로 복제]                   │
└────────────────────────────────────────────────────┘
```

- 체크박스로 2개 이상 선택 → "선택한 버전 비교" 활성화
- 각 행에 "이 버전 복제" 버튼 → 기존 `clone` 로직으로 편집기 seed

### 3.3 버전 비교 패널

```
┌─ 버전 비교 (v1, v2, v3) ───────────────── X ┐
│ 원재료        v1      v2      v3      상태 │
│ PL-835-1     30.0    32.0    32.0    수정 │
│ PL-150-2     15.0    15.0    15.0    동일 │
│ HR-400       -       -       10.0    추가 │
│ APB          5.0     -       -       제거 │
└──────────────────────────────────────────────┘
```

- 행 색: same=기본, modified=노란색, partial=파란색
- 가로 스크롤로 3개 이상 버전 지원
- 첫 컬럼은 sticky

## 4. Frontend Implementation

### 4.1 파일

**수정**
- `templates/management.html` — Lookup 탭에 버튼 + 모달 2개 (history, compare)
- `static/js/management.js` — history 버튼 핸들러, 모달 로직
- `static/css/management.css` — 버전 테이블 스타일

### 4.2 흐름

```
사용자 → 레시피 검색 → 레시피 선택 → [버전 이력] 클릭
  → GET /api/recipes/{id}/history
  → 모달에 버전 목록 표시
  → N개 체크 → [비교] 클릭
  → GET /api/recipes/history/compare?ids=...
  → 비교 모달에 가로 테이블 렌더
```

## 5. Implementation Order

1. **백엔드 API** (`recipe_routes.py`)
   - `get_recipe_history(recipe_id)` — root 찾기 + 재귀 CTE + 라벨 부여
   - `compare_recipe_versions(ids)` — 다중 fetch + union material_id + change_status 계산
2. **프론트 모달 템플릿** (`management.html`)
3. **프론트 로직** (`management.js`)
4. **스타일** (`management.css`)
5. **수동 QA**: 체인 생성 → 이력 조회 → 3개 비교 → 복제

## 6. Files to Create / Modify

**수정만** (신규 파일 없음)
- `src/routers/recipe_routes.py` — 2개 신규 엔드포인트
- `templates/management.html` — 버튼 + 모달 2개
- `static/js/management.js` — 핸들러
- `static/css/management.css` — 비교 테이블 스타일

## 7. Testing Plan

1. **체인 생성**: 레시피 A 저장 → A 복제 B 저장 → B 복제 C 저장 (v1, v2, v3)
2. **이력 조회**: C 선택 → 버전 이력 → v1, v2, v3 표시, v3=현재
3. **전체 비교**: v1, v2, v3 체크 → 3열 비교 테이블
4. **부분 비교**: v1, v3만 체크 → 2열
5. **변경 상태**: v2에서 재료 하나 값 변경 → modified 표시
6. **추가/제거**: v3에서 새 재료 추가 → partial 표시
7. **분기 체인**: B에서 복제 D → A-B-C와 A-B-D 둘 다 조회 가능
8. **순환 방지**: 수동으로 순환 설정 시도 시 무한루프 안됨

## 8. Risks

| 위험 | 완화 |
|------|------|
| 긴 체인 성능 | 재귀 CTE는 SQLite가 효율적 처리, 체인 길이 < 20 가정, 그 이상은 현장 미관찰 |
| 순환 참조 | `find_root`의 `seen` 세트 가드 + 재귀 CTE는 자연 종료 |
| 많은 버전 동시 비교 UI 폭주 | ids 상한 50, 가로 스크롤, sticky 첫 컬럼 |
| 체인에 속하지 않은 레시피 비교 요청 | 400 에러로 차단 |
