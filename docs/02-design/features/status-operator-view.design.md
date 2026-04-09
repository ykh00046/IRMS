# Status Operator View Design

> 당일 작업자별 진행 현황 섹션 상세 설계서

## 1. Overview

| Item | Detail |
|------|--------|
| Feature | status-operator-view |
| Plan | `docs/01-plan/features/status-operator-view.plan.md` |
| Scope | 당일 작업 시작한 작업자별 진행 현황 API + Status 페이지 UI |

## 2. API Design

### 2.1 작업자별 당일 진행 현황

```
GET /api/recipes/operator-progress
```

**Access Level:** `manager`

**Response:**
```json
{
  "date": "2026-04-07",
  "operators": [
    {
      "name": "홍길동",
      "completed_steps": 15,
      "total_steps": 24,
      "progress_pct": 62.5,
      "last_measured_at": "2026-04-07T06:30:00Z",
      "current_recipe": {
        "recipe_id": 42,
        "product_name": "제품A",
        "ink_name": "BLACK-001",
        "position": "1"
      },
      "category_summary": [
        { "category": "안료", "completed": 8, "total": 12 },
        { "category": "첨가제", "completed": 5, "total": 8 },
        { "category": "미분류", "completed": 2, "total": 4 }
      ],
      "worked_recipes": [
        { "product_name": "제품A", "count": 2 },
        { "product_name": "제품B", "count": 1 }
      ]
    }
  ],
  "total_operators": 1
}
```

**SQL (핵심 쿼리):**

```sql
-- 1) 당일 작업자 목록 + 기본 집계
SELECT
    ri.measured_by                          AS name,
    COUNT(*)                                AS completed_steps,
    MAX(ri.measured_at)                     AS last_measured_at
FROM recipe_items ri
WHERE ri.measured_by IS NOT NULL
  AND ri.measured_at >= ?  -- 당일 00:00:00Z
  AND ri.measured_at <  ?  -- 익일 00:00:00Z
GROUP BY ri.measured_by
ORDER BY last_measured_at DESC

-- 2) 작업자별 담당 전체 스텝 수 (당일 작업한 레시피 기준)
SELECT
    ri2.measured_by,
    COUNT(*) AS total_steps
FROM recipe_items ri2
WHERE ri2.recipe_id IN (
    SELECT DISTINCT ri3.recipe_id
    FROM recipe_items ri3
    WHERE ri3.measured_by = ?
      AND ri3.measured_at >= ? AND ri3.measured_at < ?
)
  AND (ri2.measured_by = ? OR ri2.measured_by IS NULL)
GROUP BY ri2.measured_by

-- 3) 카테고리별 집계
SELECT
    COALESCE(m.category, '미분류')           AS category,
    COUNT(CASE WHEN ri.measured_at IS NOT NULL
               AND ri.measured_at >= ? AND ri.measured_at < ?
          THEN 1 END)                        AS completed,
    COUNT(*)                                 AS total
FROM recipe_items ri
JOIN materials m ON m.id = ri.material_id
WHERE ri.recipe_id IN (
    SELECT DISTINCT ri3.recipe_id
    FROM recipe_items ri3
    WHERE ri3.measured_by = ?
      AND ri3.measured_at >= ? AND ri3.measured_at < ?
)
GROUP BY COALESCE(m.category, '미분류')

-- 4) 현재 계량 중인 레시피 (가장 최근 작업 레시피의 다음 미완료 항목)
SELECT r.id, r.product_name, r.ink_name, r.position
FROM recipes r
WHERE r.id = (
    SELECT ri.recipe_id
    FROM recipe_items ri
    WHERE ri.measured_by = ?
      AND ri.measured_at >= ? AND ri.measured_at < ?
    ORDER BY ri.measured_at DESC
    LIMIT 1
)
AND r.status = 'in_progress'
```

**구현 방식:** 단일 엔드포인트에서 위 쿼리들을 순차 실행. 작업자 수가 적으므로(당일 기준) N+1 허용 가능.

**위치:** `recipe_routes.py` → `manager_router`에 추가

**당일 기준:** UTC date() 사용 — `datetime.now(timezone.utc).date()`

---

## 3. Frontend Design

### 3.1 위치

```
[Hero Section - Live Progress Monitor]
[Summary Grid - 5 metric cards]
[Toolbar Panel - Filter + Buttons]
──────────────────────────────────────
[★ NEW: Operator Progress Section]    ← 여기
──────────────────────────────────────
[Import Feed]
[Position Board]
[Chat Sidebar]
```

### 3.2 마크업 구조

```html
<!-- status.html에 추가 -->
<section class="operator-progress-section" id="operatorProgressSection">
  <h2 class="section-title">
    <span class="title-icon">👷</span>
    작업자 현황
    <span class="operator-count" id="operatorCount">0</span>
  </h2>
  <div class="operator-grid" id="operatorGrid">
    <!-- JS로 렌더링 -->
  </div>
</section>
```

### 3.3 작업자 카드 템플릿

```html
<div class="operator-card">
  <div class="operator-header">
    <span class="operator-name">홍길동</span>
    <span class="operator-time">06:30</span>
  </div>
  <div class="operator-progress">
    <div class="progress-bar">
      <div class="progress-fill" style="width: 62.5%"></div>
    </div>
    <span class="progress-text">15 / 24 (62.5%)</span>
  </div>
  <div class="operator-current">
    <span class="current-label">현재:</span>
    <span class="current-recipe">제품A · BLACK-001 · P1</span>
  </div>
  <div class="operator-categories">
    <span class="category-chip">안료 8/12</span>
    <span class="category-chip">첨가제 5/8</span>
    <span class="category-chip">미분류 2/4</span>
  </div>
</div>
```

### 3.4 카드 스타일

```
┌──────────────────────────────┐
│  홍길동                06:30  │  ← header
│  ████████████░░░░░░░░        │  ← progress bar
│  15 / 24 (62.5%)             │  ← progress text
│  현재: 제품A · BLACK · P1    │  ← current recipe
│  [안료 8/12] [첨가제 5/8]    │  ← category chips
│  ─────────────────────────── │
│  작업 이력: 제품A(2) 제품B(1)│  ← worked recipes
└──────────────────────────────┘
```

**카드 그리드:** `display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr))`

### 3.5 상태별 시각 효과

| 조건 | 표시 |
|------|------|
| 진행률 100% | 카드 배경 연한 초록, 체크 아이콘 |
| 현재 레시피 없음 (완료 상태) | "현재:" 행 숨김 |
| 5분 이상 미활동 | 시간 표시 회색 처리 |

## 4. JavaScript 구현

### 4.1 common.js 추가

```javascript
async getOperatorProgress() {
  return this._request('/api/recipes/operator-progress');
}
```

### 4.2 status.js 수정

```javascript
// loadStatusBoard() 내부에서 호출
async function loadOperatorProgress() {
  const data = await IRMS.getOperatorProgress();
  renderOperatorSection(data);
}

function renderOperatorSection(data) {
  const grid = document.getElementById('operatorGrid');
  const count = document.getElementById('operatorCount');
  count.textContent = data.total_operators;

  if (!data.operators.length) {
    grid.innerHTML = '<p class="empty-message">당일 작업을 시작한 작업자가 없습니다.</p>';
    return;
  }

  grid.innerHTML = data.operators.map(op => `
    <div class="operator-card ${op.progress_pct >= 100 ? 'completed' : ''}">
      <div class="operator-header">
        <span class="operator-name">${IRMS.escapeHtml(op.name)}</span>
        <span class="operator-time">${formatTime(op.last_measured_at)}</span>
      </div>
      <div class="operator-progress">
        <div class="progress-bar">
          <div class="progress-fill" style="width:${op.progress_pct}%"></div>
        </div>
        <span class="progress-text">
          ${op.completed_steps} / ${op.total_steps} (${op.progress_pct}%)
        </span>
      </div>
      ${op.current_recipe ? `
      <div class="operator-current">
        <span class="current-label">현재:</span>
        <span class="current-recipe">
          ${IRMS.escapeHtml(op.current_recipe.product_name)}
          · ${IRMS.escapeHtml(op.current_recipe.ink_name)}
          · P${IRMS.escapeHtml(String(op.current_recipe.position))}
        </span>
      </div>` : ''}
      <div class="operator-categories">
        ${op.category_summary.map(c => `
          <span class="category-chip">${IRMS.escapeHtml(c.category)} ${c.completed}/${c.total}</span>
        `).join('')}
      </div>
    </div>
  `).join('');
}
```

### 4.3 자동 갱신 통합

기존 `refreshWorkspace()` 내에서 `loadOperatorProgress()` 호출 추가.
10초 주기 자동 갱신에 자연스럽게 통합.

## 5. CSS 추가 (status.css)

```css
/* Operator Progress Section */
.operator-progress-section { ... }
.operator-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 1rem; }
.operator-card { background: var(--card-bg); border-radius: 12px; padding: 1rem; border: 1px solid var(--border); }
.operator-card.completed { background: var(--success-bg); }
.operator-header { display: flex; justify-content: space-between; margin-bottom: 0.5rem; }
.operator-name { font-weight: 600; }
.operator-time { color: var(--text-secondary); font-size: 0.85rem; }
.progress-bar { height: 8px; background: var(--bg-secondary); border-radius: 4px; overflow: hidden; }
.progress-fill { height: 100%; background: var(--primary); border-radius: 4px; transition: width 0.3s; }
.progress-text { font-size: 0.85rem; color: var(--text-secondary); }
.operator-current { margin-top: 0.5rem; font-size: 0.9rem; }
.current-label { color: var(--text-secondary); }
.category-chip { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 0.8rem; background: var(--bg-secondary); margin: 2px; }
```

## 6. Data Flow

```
[10초 타이머]
    ↓
refreshWorkspace()
    ↓
┌────────────────────────┐     ┌──────────────────────────┐
│ loadStatusBoard()      │     │ loadOperatorProgress()    │
│ GET /recipes/progress  │     │ GET /recipes/operator-    │
│                        │     │     progress              │
└────────┬───────────────┘     └────────┬─────────────────┘
         ↓                              ↓
   [Position Board]              [Operator Section]
   [Summary Grid]                [작업자 카드 렌더링]
```

## 7. Implementation Order

```
1. [Backend]  recipe_routes.py — GET /api/recipes/operator-progress 엔드포인트
2. [Frontend] common.js — getOperatorProgress() 함수 추가
3. [Frontend] status.html — operator-progress-section 마크업 추가
4. [Frontend] status.css — 작업자 카드 그리드 + 칩 스타일
5. [Frontend] status.js — renderOperatorSection() + 자동 갱신 통합
```

## 8. Edge Cases

| Case | Handling |
|------|----------|
| 당일 작업자 0명 | "당일 작업을 시작한 작업자가 없습니다." 메시지 |
| 작업자가 여러 레시피 진행 | 모든 레시피의 스텝 합산, 가장 최근 레시피를 "현재" 표시 |
| category NULL인 재료 | '미분류'로 표시 |
| 자정 넘어 계속 작업 | UTC 기준 date()이므로 KST 09:00 기준 리셋 |
| measured_by 미입력 | 해당 스텝은 집계 제외 (NULL 작업자) |
