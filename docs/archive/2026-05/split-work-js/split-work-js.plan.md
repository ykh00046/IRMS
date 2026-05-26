# Split work.js Plan (Phase 4 of split-large-files initiative)

> `static/js/work.js` 760줄(단일 `DOMContentLoaded` 페이지 컨트롤러)을 책임별 6개 팩토리 모듈 + 얇은 컨트롤러로 분리하는 리팩터링 계획서. 화면 동작 100% 보존, 번들러 도입 없음. Phase 3(`split-management-js`)에서 정착시킨 팩토리 + 공유 ctx 패턴 그대로 적용.

## 1. Overview

| Item | Detail |
|------|--------|
| Feature | split-work-js (Phase 4) |
| Priority | Medium (장기 부채 정리, Phase 1·2·3 후속, split-large-files 마지막 단계) |
| Base | 2026-05-27 main, commit `1121239` |
| Goal | `static/js/work.js` (760 LOC, 단일 컨트롤러) → 책임별 6개 팩토리 모듈 + 얇은 진입 컨트롤러 |
| Deliverable | `static/js/work/*.js` (6개) + 얇아진 `static/js/work.js` + `work.html` `<script>` 로드 순서 갱신 |
| Author | ykh00046 |
| Date | 2026-05-27 |
| Status | Draft |
| Parent PDCA | [`docs/archive/2026-05/split-large-files`](../../archive/2026-05/split-large-files/), [`split-common-js`](../../archive/2026-05/split-common-js/), [`split-management-js`](../../archive/2026-05/split-management-js/) |

---

## 2. Problem Statement

`work.js`는 `document.addEventListener("DOMContentLoaded", () => { ... })` 한 덩어리(760줄)로 `/weighing` 페이지(담당자 작업 화면)를 통째로 제어한다:

1. **DOM 참조 수집** (L2-36) — `getElementById`/`querySelector` 약 35개
2. **재고 배너 폴링** (L38-64) — `refreshLowStock` + `setInterval(30000)` + `lowStockSet`
3. **공유 가변 상태** (L66-101) — `state`(채팅), `recipeImportNotice`(폴링), `weighing`(계량 모드 진행), `stageLabels`
4. **테이블 렌더링** (L106-216) — `buildHeader`, `buildRows`, `countRecipeMaterials`, `renderStats`, `render`
5. **Recipe Import 알림 폴링** (L185-299) — `storeLastSeenRecipeImportId`, `checkRecipeImportNotifications`, `startRecipeImportPolling`
6. **계량 모드 렌더링** (L301-438) — `resetWeighingProgress`, `getQueueColorCounts`, `syncWeighingControls`, `renderWeighingPanel`
7. **계량 모드 액션** (L440-603) — `loadWeighingQueue`, `openWeighingMode`, `closeWeighingMode`, `handleWeighingAdvance`, `handleWeighingUndo`
8. **이벤트 와이어링** (L605-740) — 채팅 폼/방 탭, 테이블 클릭(완료/원복), 계량 버튼(파우더/액상/단축키), visibility, 초기 부팅
9. **30분 비활동 자동 로그아웃** (L742-759) — `IDLE_TIMEOUT`, `resetIdleTimer`, 활동 이벤트 리스너

### Pain Points

1. **코드 리뷰 부담** — 계량 액션 한 줄 고치는 PR이 760줄 파일 전체로 표시됨
2. **머지 충돌** — 테이블 영역(이력)과 계량 영역(작업) 작업이 같은 파일을 동시 편집
3. **책임 추적 어려움** — `weighing` 상태 객체가 4개 영역(렌더·액션·키보드·visibility)에서 읽고 쓴다
4. **테스트 불가** — 전부 `DOMContentLoaded` 클로저 안 → 단위 테스트 import 불가

### Phase 3와의 일관성

Phase 3 `management.js`(1,006 LOC → 5 모듈) 분리에서 정착시킨 **팩토리 + 공유 ctx** 패턴을 그대로 사용:

- 각 모듈은 `IRMS.work.create<Name>(ctx)` 팩토리 함수만 등록 (즉시 실행 X)
- 컨트롤러가 단일 `ctx = { dom, state, ... }` 객체를 만들어 6개 모듈 조립
- 공유 가변 상태는 단일 `ctx.state` 객체, 원시값 캡처 금지 (`ctx.state.<key>`로만 접근)
- 교차 모듈 호출은 콜백(`ctx.onLowStockMap`, `ctx.onRefreshTable`) 또는 핸들(`ctx.weighing.render()`)

Phase 3와의 차이점:
- `work.js`는 페이지가 한 화면(탭 없음)이라 모듈 간 결합이 더 강함 (재고→계량 렌더, 알림→테이블 갱신)
- 모듈 수가 6개로 1개 더 많음 (계량 부분이 비대해 `weighing-render`/`weighing-actions` 분할)

---

## 3. Feature Items

> 각 모듈은 `IRMS.work.create<Name>(ctx)` 팩토리 함수로 노출된다. `ctx`는 컨트롤러가 만든 공유 컨텍스트(DOM 참조 + 가변 상태 + 교차 모듈 콜백). 코드 위치는 신규 디렉터리 `static/js/work/`.

### 3.1 `static/js/work/stock-banner.js` 신규

| Item | Detail |
|------|--------|
| 목표 | 재고 상태(저/음수) 폴링·배너 표시·계량 시 사용할 low-stock id Set 제공 |
| 포함 함수 | `refreshLowStock`, `startStockPolling` (setInterval 30s) |
| 팩토리 | `IRMS.work.createStockBanner(ctx)` |
| 의존 | `ctx.dom`(workStockBanner), `ctx.state.lowStockSet`(Set) |
| 반환 | `{ refresh, start, lowStockSet }` — `lowStockSet`은 ctx 공유 참조 |
| 예상 LOC | ~70 |

### 3.2 `static/js/work/recipe-table.js` 신규

| Item | Detail |
|------|--------|
| 목표 | 처리 대기 테이블 렌더링 + 행 액션(완료·원복) |
| 포함 함수 | `buildHeader`, `countRecipeMaterials`, `buildRows`, `renderStats`, `render`, `bindRowActions` |
| 팩토리 | `IRMS.work.createRecipeTable(ctx)` |
| 의존 | `ctx.dom`(tableHead, tableBody, statsCount, statsStatus), `ctx.state.loadingToken`, `ctx.onAfterRender`(→ 계량 모드 큐 재로딩 등) |
| 반환 | `{ render, bindRowActions }` |
| 예상 LOC | ~140 |

### 3.3 `static/js/work/import-notifications.js` 신규

| Item | Detail |
|------|--------|
| 목표 | 책임자가 Import한 신규 레시피 알림 폴링 + 토스트 표시 |
| 포함 함수 | `storeLastSeenRecipeImportId`, `checkRecipeImportNotifications`, `startRecipeImportPolling` |
| 팩토리 | `IRMS.work.createImportNotifications(ctx)` |
| 의존 | `ctx.state.recipeImportNotice`, `ctx.onRefreshTable`(→ recipeTable.render), `ctx.onRefreshWeighingQueue`(→ weighingActions.loadQueue when open) |
| 반환 | `{ check, start }` |
| 예상 LOC | ~130 |

### 3.4 `static/js/work/weighing-render.js` 신규

| Item | Detail |
|------|--------|
| 목표 | 계량 패널 UI 렌더링 + 컨트롤 활성/비활성 동기화 |
| 포함 함수 | `resetWeighingProgress`, `getQueueColorCounts`, `syncWeighingControls`, `renderWeighingPanel` |
| 팩토리 | `IRMS.work.createWeighingRender(ctx)` |
| 의존 | `ctx.dom`(weighingProgress*, weighingSummary, weighingStateBadge, weighingProduct*, weighingActionHint, weighingNextValue, weighingCurrentCard, weighingAdvanceBtn, weighingUndoBtn, weighingRefreshBtn), `ctx.state.weighing`, `ctx.state.lowStockSet`, `ctx.colorLabel`(= IRMS.colorLabel) |
| 반환 | `{ render, syncControls, resetProgress }` |
| 예상 LOC | ~160 |

### 3.5 `static/js/work/weighing-actions.js` 신규

| Item | Detail |
|------|--------|
| 목표 | 계량 모드 진입·종료, 큐 조회, 진행/되돌림 액션 |
| 포함 함수 | `loadWeighingQueue`, `openWeighingMode`, `closeWeighingMode`, `handleWeighingAdvance`, `handleWeighingUndo` |
| 팩토리 | `IRMS.work.createWeighingActions(ctx)` |
| 의존 | `ctx.dom`(weighingMode, weighingModeLabel, liquidColorPicker), `ctx.state.weighing`, `ctx.weighingRender`(render/syncControls/resetProgress), `ctx.onRefreshTable`(→ recipeTable.render) |
| 반환 | `{ open, close, loadQueue, advance, undo, isOpen }` |
| 예상 LOC | ~210 |

### 3.6 `static/js/work/idle-logout.js` 신규

| Item | Detail |
|------|--------|
| 목표 | 30분 비활동 시 담당자 자동 로그아웃 + `/weighing/select`로 리다이렉트 |
| 포함 함수 | `startIdleLogout` (resetIdleTimer + 활동 이벤트 리스너) |
| 팩토리 | `IRMS.work.createIdleLogout(ctx)` |
| 의존 | 없음 (글로벌 document 이벤트만) |
| 반환 | `{ start, stop }` |
| 예상 LOC | ~45 |

### 3.7 `static/js/work.js` 진입 컨트롤러 축소

| Item | Detail |
|------|--------|
| 목표 | 기존 760줄 → ~170줄 컨트롤러 전용 |
| 잔여 책임 | DOM 참조 수집, 공유 `ctx` 객체 생성, 6개 모듈 인스턴스화 + 교차 와이어링, `IRMS.createChat` 연결, 정적 이벤트 바인딩(테이블 클릭, 계량 버튼, 키보드, visibility), 초기 부팅 호출 |
| 예상 LOC | ~170 |

### 3.8 `templates/work.html` `<script>` 순서 갱신

현재 (L147-148):
```html
<script src="/static/js/chat.js"></script>
<script src="/static/js/work.js"></script>
```

변경 후 — `work.js` 한 줄을 7줄로 교체:
```html
<script src="/static/js/chat.js"></script>
<!-- work modules (factory registration only) -->
<script src="/static/js/work/stock-banner.js"></script>
<script src="/static/js/work/recipe-table.js"></script>
<script src="/static/js/work/import-notifications.js"></script>
<script src="/static/js/work/weighing-render.js"></script>
<script src="/static/js/work/weighing-actions.js"></script>
<script src="/static/js/work/idle-logout.js"></script>
<script src="/static/js/work.js"></script>  <!-- controller last -->
```

모듈은 팩토리만 등록(`IRMS.work.create*`)하고 즉시 실행하지 않으므로 6개 모듈 간 로드 순서는 무관하다. `work.js`(컨트롤러)만 마지막이면 된다.

---

## 4. Scope

### 4.1 In Scope

- [ ] 6개 신규 `static/js/work/*.js` 팩토리 모듈 생성
- [ ] `static/js/work.js` 축소 (760 → ~170줄)
- [ ] `templates/work.html` `<script>` 로드 블록 갱신 (1개 템플릿)
- [ ] `/weighing` 페이지 동작 100% 보존 (테이블, 완료/원복, 파우더/액상 모드, 계량 진행/되돌림, 키보드 단축키, 채팅, 알림, idle 로그아웃)
- [ ] 순수 함수에 대한 최소 JS 단위 테스트 1~2개 신규 추가 (`getQueueColorCounts`, `countRecipeMaterials`)
- [ ] 브라우저 스모크 (콘솔 에러 0건) — [[feedback_browser_smoke_pattern]]에 따라 IRMS_DATA_DIR + 시드 매니저 + Playwright

### 4.2 Out of Scope

- ❌ 번들러(esbuild/vite/webpack) 도입
- ❌ ES Modules(`import`/`export`) 전환, TypeScript
- ❌ 로직 변경·성능 개선·UX 변경 (순수 분리만)
- ❌ `static/js/common.js` / `chat.js` / `stock.js` 수정
- ❌ `/weighing/select` 로그인 페이지 코드 수정
- ❌ `static/js/management.js`, `status.js` 등 다른 페이지 컨트롤러

---

## 5. Requirements

### 5.1 Functional Requirements

| ID | Requirement | Priority | Status |
|----|-------------|----------|--------|
| FR-01 | `/weighing` 화면 동작 100% 보존 (테이블/완료/원복/파우더/액상/큐/진행/되돌림/채팅/알림/idle) | High | Pending |
| FR-02 | 분리 후 단일 모듈 ≤ 250 LOC | High | Pending |
| FR-03 | 공유 상태(`ctx.state`)는 단일 객체로, 모든 모듈이 동일 참조 공유 (값 복사 금지) | High | Pending |
| FR-04 | 교차 모듈 호출은 `ctx`의 콜백/모듈 핸들로만 (모듈이 서로 직접 import 금지) | High | Pending |
| FR-05 | 브라우저 콘솔 에러 0건 (골든 패스 + 모드 전환 + 키보드 단축키) | High | Pending |
| FR-06 | 기존 JS 테스트 4개 영향 없음 (`tests/js/*.test.js`) | Medium | Pending |
| FR-07 | 새 순수 함수 단위 테스트 1~2건 추가 (Phase 3 management_lookup.test.js 패턴) | Medium | Pending |

### 5.2 Non-Functional Requirements

| Category | Criteria | Measurement |
|---|---|---|
| 페이지 로드 시간 | 기존 대비 +200ms 이내 (`<script>` 6개 추가) | DevTools Network, 캐시 비활성화 |
| 콘솔 에러 | `Uncaught ReferenceError`/`TypeError` 0건 | DevTools Console / Playwright |
| 상태 일관성 | 계량 모드 열고 작업 진행/되돌리기 후 테이블 상태 동기화 정상 | 수동 스모크 |
| 폴링 안전 | `setInterval` 이중 시작 방지 (재고/Import 알림/채팅 각각) | 모듈 내부 가드 |

---

## 6. Success Criteria

### 6.1 Definition of Done

- [ ] 6개 신규 모듈 + 축소된 `work.js` 생성
- [ ] `work.html` `<script>` 갱신 완료
- [ ] `/weighing` 골든 패스 수동 스모크 PASS
- [ ] 콘솔 에러 0건
- [ ] 신규 순수 함수 테스트 PASS + 기존 JS 테스트 4개 PASS
- [ ] pytest 전수 통과 (회귀 0)
- [ ] gap-detector Match Rate ≥ 90%

### 6.2 Quality Criteria

- [ ] 단일 모듈 ≤ 250 LOC (헤더 JSDoc 포함)
- [ ] 각 모듈 상단에 책임·`ctx` 의존성 JSDoc
- [ ] 교차 모듈 직접 호출 0건 (`ctx` 경유만)
- [ ] 분리 전후 함수명 인벤토리 diff = 0 (의도된 신규 외)

---

## 7. Risks and Mitigation

| Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|
| 공유 클로저 상태(`weighing`, `recipeImportNotice`, `lowStockSet`) 분리 후 어긋남 | High | High | `ctx.state`를 단일 객체로, 원시값 직접 캡처 금지. Design 단계에서 ctx 스키마 확정 |
| 교차 모듈 순환 호출 (테이블↔계량↔알림) | High | High | 2단계 와이어링: 모듈 생성 → ctx에 핸들/콜백 주입 → 모듈 간 직접 import 0 |
| 화면 동작 회귀 (공개 API 계약 없음) | High | Medium | 4개 시나리오(테이블 완료/계량 모드 진행/알림 토스트/idle 로그아웃) 수동 스모크 체크리스트 |
| `colorLabel = IRMS.colorLabel` 캡처 — Phase 2에서 common.js 로드 순서 의존 | Medium | Medium | 모듈 안에서도 `IRMS.colorLabel`을 직접 호출 시점에 참조 (캡처 X) 또는 ctx.colorLabel로 주입 |
| 폴링 이중 시작 (모듈 재호출 시 setInterval 누적) | Medium | Medium | 각 모듈 내부에 `if (timerId) return` 가드 + Phase 2 common.js 패턴 따름 |
| jspreadsheet/jexcel 의존성 (work.js는 사용 안 함) | — | — | 해당 없음 (스프레드시트는 management.js 전용) |
| visibility/idle/keydown 이벤트 이중 바인딩 | Medium | Medium | 이벤트 바인딩은 **컨트롤러에서만** 수행. 모듈은 핸들러 반환만 |
| `static/js/common.js` 12개 모듈 로드 후 `work.js` 로드 순서 — 페이지별 JS는 IIFE 최상단에서 IRMS.colorLabel 즉시 읽음 | Low | Low | work.js 컨트롤러는 `DOMContentLoaded` 안에서 ctx 만들 때 IRMS.colorLabel 읽음 → 이미 안전 |

---

## 8. Architecture Considerations

### 8.1 팩토리 + 공유 컨텍스트 패턴 (Phase 3 그대로 재사용)

```javascript
// static/js/work/weighing-render.js
(function () {
  "use strict";
  const NS = (window.IRMS = window.IRMS || {});
  NS.work = NS.work || {};

  NS.work.createWeighingRender = function (ctx) {
    function resetProgress(totalSteps) {
      ctx.state.weighing.doneCount = 0;
      ctx.state.weighing.initialTotal = Number(totalSteps || 0);
      ctx.state.weighing.pendingRecipeCompletion = null;
      ctx.state.weighing.lastCompleted = null;
    }
    function syncControls() { /* ... */ }
    function render() { /* ... */ }
    return { render, syncControls, resetProgress };
  };
})();
```

```javascript
// static/js/work.js (얇은 컨트롤러)
document.addEventListener("DOMContentLoaded", () => {
  const dom = { /* getElementById 묶음 35개 */ };
  const ctx = {
    dom,
    state: {
      // 채팅
      currentUsername: dom.shell?.dataset.currentUsername || "",
      selectedRoomKey: window.localStorage.getItem("irms_chat_room") || "notice",
      rooms: [], chatLatestIdByRoom: {}, chatSending: false, chatTimerId: null,
      // 테이블
      loadingToken: 0,
      // 재고
      lowStockSet: new Set(),
      // import 알림
      recipeImportNotice: { initialized: false, checking: false, lastSeenId: ..., timerId: null },
      // 계량
      weighing: { open: false, loading: false, advancing: false, undoing: false,
                  queue: [], doneCount: 0, initialTotal: 0, colorGroup: "all",
                  pendingRecipeCompletion: null, lastCompleted: null, lastSpokenStepKey: null },
      stageLabels: { registered: "Registered", in_progress: "In Progress", completed: "Completed" },
    },
    colorLabel: IRMS.colorLabel,
  };

  const stockBanner = IRMS.work.createStockBanner(ctx);
  const recipeTable = IRMS.work.createRecipeTable(ctx);
  ctx.onRefreshTable = recipeTable.render;
  const weighingRender = IRMS.work.createWeighingRender(ctx);
  ctx.weighingRender = weighingRender;
  const weighingActions = IRMS.work.createWeighingActions(ctx);
  ctx.weighingActions = weighingActions;
  ctx.onRefreshWeighingQueue = () => {
    if (weighingActions.isOpen()) return weighingActions.loadQueue();
  };
  const importNotifications = IRMS.work.createImportNotifications(ctx);
  const idleLogout = IRMS.work.createIdleLogout(ctx);

  const chatModule = IRMS.createChat({ /* state proxies */ });
  // 이벤트 바인딩 + 부팅 (render, polling start, idle start) ...
});
```

**선택 이유**: 번들러 0, 기존 `<script>` 모델 유지, Phase 3 패턴 일관성, 공유 상태를 단일 객체로 안전 공유.

### 8.2 모듈 의존 방향

```
stock-banner          (ctx.dom, ctx.state.lowStockSet)
recipe-table          (ctx.dom, ctx.state.loadingToken)
weighing-render       (ctx.dom, ctx.state.weighing, ctx.state.lowStockSet, ctx.colorLabel)
weighing-actions      (+ ctx.weighingRender, ctx.onRefreshTable)
import-notifications  (+ ctx.onRefreshTable, ctx.onRefreshWeighingQueue)
idle-logout           (독립)
work.js               컨트롤러 — ctx 생성·6개 모듈 조립·와이어링
```

순환 없음: 컨트롤러가 유일한 조립 지점. `weighing-actions`는 `weighing-render`를 단방향 참조. `import-notifications`는 `onRefreshTable`/`onRefreshWeighingQueue` 콜백만 사용.

---

## 9. Convention Prerequisites

| Target | Rule | Example |
|---|---|---|
| 파일명 | `work/<scope>.js` (케밥 케이스) | `work/weighing-render.js` |
| 모듈 IIFE | `(function () { "use strict"; ... })();` 통일 | §8.1 |
| 팩토리 네임스페이스 | `IRMS.work.create<PascalName>` | `createWeighingRender` |
| 공유 상태 접근 | 항상 `ctx.state.<key>` (원시값 캡처 금지) | `ctx.state.weighing.queue` |
| 모듈 헤더 | 책임·`ctx` 의존·반환 핸들 JSDoc 1단락 | — |
| 폴링 가드 | 모듈 내부 `if (state.<>.timerId) clearInterval(...)` 후 재시작 | Phase 2 common.js polling 패턴 |

---

## 10. Implementation Order

```
Step 1: 분리 전 inventory
  - work.js 함수 18개 + 공유 상태 4개 객체 + DOM 참조 35개 목록화
  - 각 함수가 읽고/쓰는 상태, 호출하는 다른 함수 매핑표 작성 (Design 입력)

Step 2: ctx 스키마 확정 (Design 산출물)
  - ctx.dom / ctx.state(5개 sub-state) / ctx.weighingRender / ctx.weighingActions / ctx.onRefreshTable / ctx.onRefreshWeighingQueue / ctx.colorLabel

Step 3: 모듈 6개 생성
  - idle-logout (독립, 가장 단순) → stock-banner → recipe-table → weighing-render → weighing-actions → import-notifications

Step 4: work.js 컨트롤러 축소
  - DOM 수집 + ctx 생성 + 6개 모듈 조립 + 2단계 와이어링 + 이벤트 바인딩 + 부팅 호출만 잔존

Step 5: 템플릿 + 검증
  - work.html <script> 7줄로 교체
  - /weighing 수동 + Playwright 스모크 (콘솔 에러 0건)
  - 신규 순수 함수 테스트 + 기존 JS/Python 테스트 PASS
  - gap-detector ≥ 90%
```

---

## 11. PR 전략

- **단일 PR**: Step 1~5 한 PR (중간 상태는 페이지 깨짐 → bisect 곤란)
- **커밋 단위**:
  - Plan (이 문서)
  - Design (다음 단계)
  - Do — 모듈별 6커밋 분리 가능하나 단일 commit 도 허용 (서버 영향 0)
  - 실제 분리 시 1커밋: "Split static/js/work.js (760 LOC) into 6 modules + controller"
  - Archive 1커밋
- **롤백**: `git revert <merge-commit>` 한 번 (서버·DB 영향 없음, 단일 템플릿)

---

## 12. Next Steps

1. [x] 본 Plan 검토·승인 (자동 진행)
2. [ ] `/pdca design split-work-js` — `ctx` 스키마 + 함수↔상태 매핑표 + 의존 그래프 확정
3. [ ] `/pdca do split-work-js` — Step 1~5 실행
4. [ ] `/pdca analyze split-work-js` — gap-detector 검증
5. [ ] `/pdca report split-work-js` + archive
6. [ ] split-large-files 시리즈 완료 — 다음 부채는 `attendance_excel.py`(791 LOC) 또는 `status.js`(582 LOC)

---

## Version History

| Version | Date | Changes | Author |
|---|---|---|---|
| 0.1 | 2026-05-27 | Initial draft (Phase 4 of split-large-files initiative, split-large-files 마지막 단계) | ykh00046 |
