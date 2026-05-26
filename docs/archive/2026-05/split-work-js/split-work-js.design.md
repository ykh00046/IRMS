# split-work-js Design Document

> **Summary**: `static/js/work.js` (760 LOC, 단일 `DOMContentLoaded` 페이지 컨트롤러) → 책임별 6개 팩토리 모듈 + 얇은 컨트롤러. 공유 가변 상태는 단일 `ctx` 객체로 참조 공유. `/weighing` 페이지 동작 100% 보존. 번들러·ESM·TypeScript 도입 없음.
>
> **Project**: IRMS
> **Version**: post-`1121239`
> **Author**: ykh00046
> **Date**: 2026-05-27
> **Status**: Draft
> **Planning Doc**: [split-work-js.plan.md](../../01-plan/features/split-work-js/split-work-js.plan.md)
> **Parent Initiative**: split-large-files — [Phase 1](../../archive/2026-05/split-large-files/), [Phase 2](../../archive/2026-05/split-common-js/), [Phase 3](../../archive/2026-05/split-management-js/)

---

## 1. Overview

### 1.1 Design Goals

1. `work.js` 단일 IIFE(760 LOC)를 책임별 7개 파일로 분리 (6개 팩토리 모듈 + 1개 컨트롤러)
2. `/weighing` 페이지 모든 동작 100% 보존 (테이블 / 완료·원복 / 파우더·액상 / 큐 / 진행·되돌림 / 채팅 / 알림 / idle 로그아웃)
3. 함수가 공유하는 **5개 가변 상태**를 **단일 `ctx.state` 객체**로 안전하게 공유
4. 모듈 간 상호 호출을 `ctx` 콜백/핸들로만 — 모듈은 서로 직접 참조하지 않음
5. 단일 모듈 ≤ 250 LOC (헤더 JSDoc 포함)
6. `templates/work.html` `<script>` 블록 갱신 (1개 템플릿)

### 1.2 Design Principles

- **순수 분리** — 함수 본문·동작·부수효과 시점 0줄 변경. 코드 이동과 `ctx` 경유 참조 치환만
- **단일 상태 객체** — 모든 모듈이 `ctx.state`의 동일 참조를 공유. 원시값을 모듈 스코프로 캡처 금지 (§4.2)
- **lazy `ctx` 참조** — 모듈은 `ctx.weighingRender`/`ctx.onRefreshTable` 등을 **호출 시점에** 읽음. 팩토리 실행 시점에 캡처 금지 (2단계 와이어링이 성립하는 근거, §5)
- **컨트롤러 단일 조립 지점** — `ctx` 생성·모듈 인스턴스화·교차 와이어링·이벤트 바인딩은 `work.js` 한 곳에만
- **Phase 3 패턴 계승** — `IRMS.management.create*` 네임스페이스 분리에서 정착시킨 팩토리 + ctx 패턴을 `IRMS.work.create*`로 확장

### 1.3 Phase 3와의 차이

| 항목 | Phase 3 (management.js) | Phase 4 (work.js) |
|------|--------------------------|---------------------|
| 원본 LOC | 1,006 | 760 |
| 모듈 수 | 5 | 6 (weighing render/actions 분리 때문) |
| 페이지 구조 | 4개 탭 | 단일 화면 + 모달(계량 모드) |
| 가변 상태 객체 수 | 10개 단일 상태 | 4개 sub-state (state/recipeImportNotice/weighing) + lowStockSet |
| 외부 라이브러리 | jspreadsheet, jexcel | 없음 (vanilla DOM만) |
| 폴링 | 1개 (채팅) | 4개 (재고 30s, import 알림 8s, 채팅 3s, idle 30분) |
| 영향 템플릿 | 1개 (`management.html`) | 1개 (`work.html`) |

---

## 2. Architecture

### 2.1 Module Dependency Graph

```
                       ┌──────────────────┐
                       │     work.js       │  컨트롤러 (조립 지점)
                       │  ctx 생성·와이어링  │
                       └────────┬─────────┘
                                │ creates / injects ctx
       ┌─────────┬──────────────┼──────────────────┬─────────────┬───────────┐
       ▼         ▼              ▼                  ▼             ▼           ▼
┌────────────┐ ┌────────────┐ ┌────────────────┐ ┌──────────────┐ ┌────────────────┐ ┌─────────────┐
│ stock-     │ │ recipe-    │ │ weighing-      │ │ weighing-    │ │ import-        │ │ idle-       │
│ banner     │ │ table      │ │ render         │ │ actions      │ │ notifications  │ │ logout      │
└─────┬──────┘ └─────┬──────┘ └────────┬───────┘ └──────┬───────┘ └────────┬───────┘ └──────┬──────┘
      │              │                 │                │                  │                │
      │              │ ctx.onAfterR.   │                │ ctx.weighing-    │ ctx.onRefresh- │
      │              │ (선택)          │                │ Render            │ Table /         │
      │              │                 │                │ ctx.onRefresh-    │ onRefresh-      │
      │              │                 │                │ Table             │ WeighingQueue   │
      └──────────────┴─────────────────┴── ctx.state (공유 단일 객체) ────────┴────────────────┘
```

순환 없음. 모듈 간 직접 참조 0 — 모든 교차 호출은 `ctx`의 핸들/콜백 경유. 컨트롤러가 유일하게 모든 모듈을 안다.

### 2.2 Factory + Shared Context 패턴 (스펙)

각 모듈은 동일 보일러플레이트:

```javascript
/**
 * <module> module — <한 줄 설명>.
 * Split from static/js/work.js (split-work-js, 2026-05).
 *
 * Factory: IRMS.work.create<Name>(ctx)
 * Returns: { <handle1>, <handle2>, ... }
 * ctx deps: ctx.dom.<...>, ctx.state.<...>, ctx.<moduleHandle>, ctx.<callback>
 */
(function () {
  "use strict";
  const NS = (window.IRMS = window.IRMS || {});
  NS.work = NS.work || {};

  NS.work.createWeighingActions = function (ctx) {
    const { dom, state } = ctx;
    const weighing = state.weighing;  // 객체 참조는 캡처 OK (내부 키만 mutate)

    async function advance() {
      if (!weighing.open || weighing.loading || weighing.advancing) return;
      weighing.advancing = true;
      // ...
      ctx.weighingRender.syncControls();  // lazy 참조
      // ...
    }
    // ...
    return { open, close, loadQueue, advance, undo, isOpen };
  };
})();
```

**중요 규칙**:
- 팩토리는 `ctx`를 받아 핸들 객체를 반환할 뿐, **즉시 실행(이벤트 바인딩·DOM 조작) 안 함** → 6개 모듈 간 `<script>` 로드 순서 무관
- 상태는 `state.<sub>.field`처럼 객체 참조까지만 캡처 (객체 참조는 안전). 원시값 `const total = weighing.initialTotal` 같은 캡처 금지
- 다른 모듈 호출은 `ctx.weighingRender.render()` / `ctx.onRefreshTable()` 처럼 **호출 시점에** `ctx`에서 꺼냄

---

## 3. The `ctx` Object (핵심 설계 산출물)

컨트롤러가 만들어 모든 모듈에 전달하는 단일 공유 컨텍스트.

### 3.1 `ctx.dom` — DOM 참조 (현재 work.js L2-36과 동일)

| 그룹 | 멤버 |
|------|------|
| **shell/chat** | `shell`, `tableHead`, `tableBody`, `statsCount`, `statsStatus`, `roomMeta`, `roomTabs`, `chatMessages`, `chatForm`, `chatInput`, `chatSend`, `chatStage` (= `document.getElementById("work-chat-stage")` → 현재 work.html에 없어 `null`. `chat.js` `bindForm`이 `stage?.value` 안전 처리. 원본 `chatStage` 미정의 식별자 사용 부분을 안전화) |
| **재고 배너** | `workStockBanner` |
| **계량 컨트롤** | `weighingRefreshMainBtn`, `weighingMode`, `weighingCloseBtn`, `weighingRefreshBtn`, `weighingAdvanceBtn`, `weighingUndoBtn`, `weighingPowderBtn`, `weighingLiquidBtn`, `liquidColorPicker`, `weighingModeLabel` |
| **계량 상태 표시** | `weighingProgressFill`, `weighingProgressText`, `weighingSummary`, `weighingStateBadge`, `weighingProductName`, `weighingInkLabel`, `weighingPositionLabel`, `weighingMaterialName`, `weighingTargetValue`, `weighingActionHint`, `weighingNextValue`, `weighingCurrentCard` |

총 35개. 모든 참조는 컨트롤러에서 `getElementById`/`querySelector`로 1회 수집 → `ctx.dom`에 부착.

### 3.2 `ctx.state` — 공유 가변 상태 (단일 객체)

| 키 | 타입 | 초기값 | Read by | Write by |
|----|------|-------|---------|----------|
| `lowStockSet` | `Set<number>` | `new Set()` | weighing-render | stock-banner |
| `loadingToken` | `number` | `0` | recipe-table | recipe-table |
| `currentUsername` | `string` | `shell.dataset.currentUsername` | (chatModule via proxy) | — |
| `selectedRoomKey` | `string` | `localStorage["irms_chat_room"] || "notice"` | (chatModule) | (chatModule) |
| `rooms` | `Array` | `[]` | (chatModule) | (chatModule) |
| `chatLatestIdByRoom` | `Object` | `{}` | (chatModule) | (chatModule) |
| `chatSending` | `boolean` | `false` | (chatModule) | (chatModule) |
| `chatTimerId` | `number?` | `null` | (chatModule) | (chatModule) |
| `recipeImportNotice` | `Object` | `{ initialized: false, checking: false, lastSeenId: <localStorage>, timerId: null }` | import-notifications | import-notifications |
| `weighing` | `Object` | `{ open, loading, advancing, undoing, queue, doneCount, initialTotal, colorGroup, pendingRecipeCompletion, lastCompleted, lastSpokenStepKey }` | weighing-render, weighing-actions | weighing-render, weighing-actions |
| `stageLabels` | `Object` (const) | `{ registered, in_progress, completed }` | (chatModule via proxy) | — |

전체 공유 심볼: **5개 가변 sub-state + 1개 상수 객체**. `chatModule`은 state proxy 게터/세터를 통해 6개 채팅 키에 접근(`work.js` 컨트롤러가 `IRMS.createChat()`에 직접 위임).

### 3.3 `ctx.const` / 보조

- `ctx.colorLabel = IRMS.colorLabel` — Phase 2 common.js의 컬러 라벨러. `weighing-render`에서 호출. lazy 참조도 가능하지만 모듈 사용 빈도가 높아 ctx에 캡처(common.js는 컨트롤러 진입 시점에 이미 로드 완료).

### 3.4 `ctx.<moduleHandle>` — 모듈 인스턴스 핸들 (2단계 와이어링)

| ctx 키 | 타입 | 컨트롤러 부착 시점 | 소비처 |
|--------|------|---------------------|--------|
| `ctx.stockBanner` | `{ refresh, start }` | `createStockBanner(ctx)` 호출 직후 | (필요 시) |
| `ctx.recipeTable` | `{ render, bindRowActions }` | `createRecipeTable(ctx)` 호출 직후 | (필요 시) |
| `ctx.weighingRender` | `{ render, syncControls, resetProgress }` | `createWeighingRender(ctx)` 호출 직후 | weighing-actions |
| `ctx.weighingActions` | `{ open, close, loadQueue, advance, undo, isOpen }` | `createWeighingActions(ctx)` 호출 직후 | 컨트롤러 이벤트 바인딩, import-notifications |

### 3.5 `ctx.<callback>` — 교차 콜백 (2단계 와이어링)

| ctx 키 | 시그니처 | 부착 시점 | 소비처 |
|--------|----------|-----------|--------|
| `ctx.onRefreshTable` | `() => Promise<void>` | `recipeTable` 생성 직후, `= recipeTable.render` | weighing-actions(advance/undo), import-notifications(check) |
| `ctx.onRefreshWeighingQueue` | `() => Promise<void> \| void` | `weighingActions` 생성 직후, `= () => weighingActions.isOpen() ? weighingActions.loadQueue() : undefined` | import-notifications(check) |

### 3.6 폴링/타이머 인터벌 통합

본 페이지의 4개 폴링/타이머를 한눈에:

| Module | Interval | Guard variable | Visibility-aware |
|---|---|---|---|
| stock-banner | 30s (`setInterval`) | module-private `stockTimer` | 아니오 (조용히 폴링) |
| import-notifications | 8s (`setInterval`) | `state.recipeImportNotice.timerId` | 예 (`hidden` 시 skip) |
| chat (via `IRMS.createChat`) | 3s (`setInterval`) | `state.chatTimerId` | 예 (`visible` 시 force refresh) |
| idle-logout | 30분 단일 `setTimeout` (활동 이벤트마다 reset) | module-private `idleTimer` | 아니오 (활동 이벤트 자체가 visible 신호) |

각 모듈의 `start()`는 이중 호출 가드를 둔다(`§6.x 함정` 참조).

---

## 4. Function ↔ State 매핑표 (분리 정합성 검증의 기준)

work.js의 모든 함수 18개와 각 함수가 읽고/쓰는 상태, 호출하는 다른 함수.

| 모듈 | 함수 | Reads | Writes | Calls (cross-module) |
|------|------|-------|--------|----------------------|
| stock-banner | `refreshLowStock` | `dom.workStockBanner` | `state.lowStockSet`, `dom.workStockBanner.textContent/hidden` | — (fetch만) |
| stock-banner | `startStockPolling` (신규 명명, 기존 setInterval) | — | — | `refreshLowStock` |
| recipe-table | `buildHeader` | `dom.tableHead` | `dom.tableHead.innerHTML` | — |
| recipe-table | `countRecipeMaterials(recipe)` | — | — | — (순수) |
| recipe-table | `buildRows(recipes)` | `dom.tableBody` | `dom.tableBody.innerHTML` | `IRMS.statusClass/statusLabel/escapeHtml/formatDateTime`, `countRecipeMaterials` |
| recipe-table | `renderStats(recipes)` | `dom.statsCount/statsStatus` | DOM | — |
| recipe-table | `render` | `state.loadingToken` | `state.loadingToken (++)`, `dom.tableBody` | `IRMS.getRecipes`, `IRMS.notify`, `buildHeader`, `buildRows`, `renderStats` |
| recipe-table | `bindRowActions` (신규 추출 — 기존 L609-652 click handler) | — | — | `IRMS.updateRecipeStatus`, `IRMS.resetWeighingRecipe`, `IRMS.notify`, `ctx.recipeTable.render` (lazy) |
| import-notifications | `storeLastSeenRecipeImportId(nextId)` | — | `state.recipeImportNotice.lastSeenId`, `localStorage` | — |
| import-notifications | `checkRecipeImportNotifications(options)` | `state.recipeImportNotice.{checking, initialized, lastSeenId}`, `state.weighing.open` | `state.recipeImportNotice.{checking, initialized}` | `IRMS.getRecipeImportNotifications/notify`, `ctx.onRefreshTable`, `ctx.onRefreshWeighingQueue`, `storeLastSeenRecipeImportId` |
| import-notifications | `startRecipeImportPolling` | `state.recipeImportNotice.timerId`, `document.visibilityState` | `state.recipeImportNotice.timerId` | `checkRecipeImportNotifications` |
| weighing-render | `resetWeighingProgress(totalSteps)` | — | `state.weighing.{doneCount, initialTotal, pendingRecipeCompletion, lastCompleted}` | — |
| weighing-render | `getQueueColorCounts(queue)` | — | — | — (순수) |
| weighing-render | `syncWeighingControls` | `state.weighing.{loading, advancing, undoing, pendingRecipeCompletion, queue, lastCompleted}` | `dom.weighing*Btn.disabled` | — |
| weighing-render | `renderWeighingPanel` | `state.weighing.*`, `state.lowStockSet`, `ctx.colorLabel`, `dom.*` (대량) | `dom.*`, `state.weighing.lastSpokenStepKey` | `IRMS.speakText`, `getQueueColorCounts`, `syncWeighingControls` |
| weighing-actions | `loadWeighingQueue(options)` | `state.weighing.{colorGroup, initialTotal, doneCount, open}` | `state.weighing.{loading, queue, colorGroup, initialTotal}` | `IRMS.getWeighingQueue/notify`, `ctx.weighingRender.{resetProgress, syncControls, render}` |
| weighing-actions | `openWeighingMode(colorGroup, modeLabel)` | `dom.{weighingMode, weighingModeLabel, liquidColorPicker}` | `state.weighing.{colorGroup, open}`, `dom.*` | `loadWeighingQueue` |
| weighing-actions | `closeWeighingMode` | `dom.weighingMode` | `state.weighing.open`, `dom.*` | — |
| weighing-actions | `handleWeighingAdvance` | `state.weighing.*` | `state.weighing.*` | `IRMS.completeWeighingRecipe/completeWeighingStep/notify`, `ctx.onRefreshTable`, `loadWeighingQueue`, `ctx.weighingRender.{render, syncControls}` |
| weighing-actions | `handleWeighingUndo` | `state.weighing.*` | `state.weighing.*` | `IRMS.undoWeighingStep/notify`, `loadWeighingQueue`, `ctx.onRefreshTable`, `ctx.weighingRender.render` |
| idle-logout | `startIdleLogout` | — | (internal `idleTimer`) | `IRMS.logout`, `location.assign` |

### 4.1 컨트롤러 잔존 함수 (모듈로 옮기지 않음)

| 함수 | 역할 | 잔존 이유 |
|------|------|-----------|
| `refreshChatPanel(options)` | `chatModule.refresh(options)` 위임 | 1줄 어댑터, IRMS.createChat 통합 |
| `startChatPolling` | `chatModule.startPolling(3000)` 위임 | 1줄 어댑터 |
| (인라인) | DOM 수집, ctx 생성, 모듈 조립, 이벤트 바인딩, 부팅 호출 | 컨트롤러 책임 |

### 4.2 상태 캡처 금지 (검증 포인트)

다음 패턴은 **금지** (모듈 간 상태 불일치 유발):

```javascript
// ❌ 금지 — 원시값 캡처
const NS = window.IRMS;
NS.work.createWeighingActions = function (ctx) {
  const open = ctx.state.weighing.open;  // ❌ 시점 캡처
  function advance() {
    if (!open) return;  // 영원히 false
  }
};

// ✅ 허용 — 객체 참조까지만 캡처, 내부 키는 매번 읽음
NS.work.createWeighingActions = function (ctx) {
  const weighing = ctx.state.weighing;  // 객체 참조 OK
  function advance() {
    if (!weighing.open) return;  // 매번 최신 값 읽음
  }
};

// ✅ 허용 — 핸들 lazy 참조
function advance() {
  ctx.weighingRender.syncControls();  // 호출 시점에 ctx에서 꺼냄
}
```

design-validator/gap-detector 검증 포인트: 모듈 파일에서 `const \w+ = ctx\.state\.\w+\.\w+`(원시 캡처 의심) grep = 0이어야 함. `const \w+ = ctx\.state\.\w+\s*;`(객체 참조 캡처)는 허용.

---

## 5. 와이어링 순서 (컨트롤러 부팅 시퀀스)

### 5.1 `templates/work.html` `<script>` 블록 (Before → After)

**Before** (현재 L147-148):
```html
<script src="/static/js/chat.js"></script>
<script src="/static/js/work.js"></script>
```

**After** (8줄):
```html
<script src="/static/js/chat.js"></script>
<!-- work modules (factory registration only — order among modules is irrelevant) -->
<script src="/static/js/work/stock-banner.js"></script>
<script src="/static/js/work/recipe-table.js"></script>
<script src="/static/js/work/import-notifications.js"></script>
<script src="/static/js/work/weighing-render.js"></script>
<script src="/static/js/work/weighing-actions.js"></script>
<script src="/static/js/work/idle-logout.js"></script>
<script src="/static/js/work.js"></script>  <!-- controller last -->
```

모듈은 팩토리만 등록(`IRMS.work.create*`)하고 즉시 실행하지 않으므로 6개 모듈 간 로드 순서는 무관. `work.js`(컨트롤러)만 마지막. 영향 템플릿 **1개**(`templates/work.html`) 외 변경 없음 — `grep -l "work.js" templates/` = 1.

### 5.2 부팅 시퀀스

```javascript
document.addEventListener("DOMContentLoaded", () => {
  // 1단계: DOM 수집 + ctx 생성
  const dom = { shell, tableHead, tableBody, /* 35개 */ };
  const ctx = {
    dom,
    state: {
      lowStockSet: new Set(),
      loadingToken: 0,
      currentUsername: dom.shell?.dataset.currentUsername || "",
      selectedRoomKey: window.localStorage.getItem("irms_chat_room") || "notice",
      rooms: [],
      chatLatestIdByRoom: {},
      chatSending: false,
      chatTimerId: null,
      recipeImportNotice: {
        initialized: false,
        checking: false,
        lastSeenId: Number(window.localStorage.getItem("irms_last_recipe_import_id") || 0),
        timerId: null,
      },
      weighing: {
        open: false, loading: false, advancing: false, undoing: false,
        queue: [], doneCount: 0, initialTotal: 0, colorGroup: "all",
        pendingRecipeCompletion: null, lastCompleted: null, lastSpokenStepKey: null,
      },
      stageLabels: { registered: "Registered", in_progress: "In Progress", completed: "Completed" },
    },
    colorLabel: IRMS.colorLabel,
  };

  // 2단계: 모듈 인스턴스화 (의존 순서대로 — 단방향)
  ctx.stockBanner = IRMS.work.createStockBanner(ctx);
  ctx.recipeTable = IRMS.work.createRecipeTable(ctx);
  ctx.onRefreshTable = ctx.recipeTable.render;
  ctx.weighingRender = IRMS.work.createWeighingRender(ctx);
  ctx.weighingActions = IRMS.work.createWeighingActions(ctx);
  ctx.onRefreshWeighingQueue = async () => {
    if (ctx.weighingActions.isOpen()) {
      await ctx.weighingActions.loadQueue();
    }
  };
  const importNotifications = IRMS.work.createImportNotifications(ctx);
  const idleLogout = IRMS.work.createIdleLogout(ctx);

  // 3단계: 채팅 모듈 (state proxy)
  const chatModule = IRMS.createChat({
    prefix: "chat",
    stageLabels: ctx.state.stageLabels,
    elements: { roomTabs: dom.roomTabs, chatMessages: dom.chatMessages, roomMeta: dom.roomMeta },
    state: {
      get rooms() { return ctx.state.rooms; },
      set rooms(v) { ctx.state.rooms = v; },
      get selectedRoomKey() { return ctx.state.selectedRoomKey; },
      set selectedRoomKey(v) { ctx.state.selectedRoomKey = v; },
      get latestByRoom() { return ctx.state.chatLatestIdByRoom; },
      set latestByRoom(v) { ctx.state.chatLatestIdByRoom = v; },
      get timerId() { return ctx.state.chatTimerId; },
      set timerId(v) { ctx.state.chatTimerId = v; },
      get currentUsername() { return ctx.state.currentUsername; },
    },
  });

  // 4단계: 정적 이벤트 바인딩 (컨트롤러 책임)
  chatModule.bindRoomTabs(dom.roomTabs);
  chatModule.bindForm({ form: dom.chatForm, input: dom.chatInput, send: dom.chatSend });
  ctx.recipeTable.bindRowActions();  // 테이블 click 위임 핸들러 부착
  bindWeighingButtons(ctx);           // 파우더/액상/리프레시/닫기/진행/되돌림/모달 close
  bindKeyboardShortcuts(ctx);         // Esc / Enter / Space
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") {
      importNotifications.check({ silent: true });
      chatModule.refresh({ replace: false, silent: true });
    }
  });

  // 5단계: 부팅 — 초기 렌더 + 폴링 시작
  ctx.stockBanner.refresh();
  ctx.stockBanner.start();
  ctx.recipeTable.render();
  chatModule.refresh({ replace: true, silent: true });
  importNotifications.check({ silent: true });
  importNotifications.start();
  chatModule.startPolling(3000);
  idleLogout.start();
});
```

`bindWeighingButtons` / `bindKeyboardShortcuts`는 컨트롤러 내부 헬퍼(또는 인라인). 모듈로 옮기지 않는 이유는 (a) 라이프사이클이 1회 부착이고 (b) 5개 모듈 핸들을 모두 참조해야 해서 컨트롤러가 자연스러운 위치이기 때문.

---

## 6. 모듈별 상세 스펙

### 6.1 `static/js/work/stock-banner.js`

**책임**: 30초 간격으로 `/api/materials/stock`을 폴링해 음수/임계 미달 재고를 배너에 표시하고, `state.lowStockSet`에 id 목록을 유지(계량 패널이 줄무늬 경고로 사용).

**ctx 의존**:
- `ctx.dom.workStockBanner`
- `ctx.state.lowStockSet` (Set; 모듈이 클리어/추가)

**반환**:
- `refresh(): Promise<void>` — 1회 폴링
- `start(): void` — 30초 setInterval (이중 시작 가드)

**예상 LOC**: ~70 (헤더 포함)

**함정**:
- `lowStockSet`은 새 객체를 만들지 말고 **기존 Set의 `clear()` + `add()`로** 갱신해야 weighing-render가 참조 무효화 없이 읽음
- start 호출이 중복돼도 setInterval은 1개만

### 6.2 `static/js/work/recipe-table.js`

**책임**: 처리 대기 레시피 테이블 헤더/행/통계 렌더, 행의 "완료"/"원복" 버튼 click 위임.

**ctx 의존**:
- `ctx.dom.{tableHead, tableBody, statsCount, statsStatus}`
- `ctx.state.loadingToken` (race 방지 토큰)

**반환**:
- `render(): Promise<void>` — `IRMS.getRecipes({})` → 필터링 → 헤더/행/통계 렌더
- `bindRowActions(): void` — `tableBody`에 click 위임 핸들러 1회 부착 (`complete-btn`, `reset-btn`)

**예상 LOC**: ~140

**함정**:
- `bindRowActions` 안의 `setTimeout(render, 280)`는 lazy 참조 `() => ctx.recipeTable.render()`로 호출. 직접 `render` 캡처 OK(같은 모듈 함수)

### 6.3 `static/js/work/import-notifications.js`

**책임**: 8초 간격으로 책임자가 import한 신규 레시피 알림을 폴링, 토스트 표시, 테이블/계량 큐 동시 갱신.

**ctx 의존**:
- `ctx.state.recipeImportNotice`
- `ctx.onRefreshTable(): Promise<void>`
- `ctx.onRefreshWeighingQueue(): Promise<void> | void`

**반환**:
- `check({ silent }?): Promise<void>`
- `start(): void`

**예상 LOC**: ~130

**함정**:
- `localStorage["irms_last_recipe_import_id"]` 키 보존
- `document.visibilityState === "hidden"`이면 setInterval tick에서 skip (기존 동작 유지)
- **이중 start 가드**: `start()` 진입 시 `if (state.recipeImportNotice.timerId) { window.clearInterval(state.recipeImportNotice.timerId); }` 후 새 `setInterval` 등록(기존 work.js L289-292 동작 보존). Phase 2 common.js 폴링 패턴 동일

### 6.4 `static/js/work/weighing-render.js`

**책임**: 계량 패널 시각 상태(진행률/요약 칩/현재 카드/다음 안내) 렌더, 컨트롤 버튼 disabled 동기화, 첫 진입 음성 발성.

**ctx 의존**:
- `ctx.dom.weighing*` (모두)
- `ctx.state.weighing`
- `ctx.state.lowStockSet`
- `ctx.colorLabel`

**반환**:
- `render(): void`
- `syncControls(): void`
- `resetProgress(totalSteps: number): void`

**예상 LOC**: ~165 (renderWeighingPanel 105 + getQueueColorCounts 10 + syncControls 15 + resetProgress 5 + 헤더 + 보일러플레이트)

**함정**:
- `IRMS.speakText` 호출은 `lastSpokenStepKey` 갱신과 같은 트랜잭션. 모듈 분리해도 동일 state.weighing 객체 참조라 안전
- 현재 코드의 `weighing.lastSpokenStepKey = null` (L390) 패턴은 `state.weighing.lastSpokenStepKey = null`로 치환

### 6.5 `static/js/work/weighing-actions.js`

**책임**: 계량 모드 진입/종료, 큐 조회, 진행(advance), 되돌림(undo). 액션 후 `weighingRender.render()` + `onRefreshTable()` 호출.

**ctx 의존**:
- `ctx.dom.{weighingMode, weighingModeLabel, liquidColorPicker}`
- `ctx.state.weighing`
- `ctx.weighingRender` (lazy)
- `ctx.onRefreshTable` (lazy)

**반환**:
- `open(colorGroup, modeLabel): void`
- `close(): void`
- `loadQueue(options?): Promise<void>`
- `advance(): Promise<void>`
- `undo(): Promise<void>`
- `isOpen(): boolean` — `state.weighing.open` 읽기 헬퍼

**예상 LOC**: ~215

**함정**:
- `document.body.style.overflow = "hidden"` / `""` 부수효과 보존
- `aria-hidden` 토글 보존

### 6.6 `static/js/work/idle-logout.js`

**책임**: 30분 비활동 시 담당자 자동 로그아웃, `/weighing/select` 리다이렉트.

**ctx 의존**: 없음 (글로벌 document 이벤트만)

**Constants**: module-private `const IDLE_TIMEOUT_MS = 30 * 60 * 1000;` (모듈 export 불필요 — 기존 work.js L743 동작 그대로 보존)

**반환**:
- `start(): void` — 활동 이벤트 리스너 5개 등록 + 초기 타이머 set
- `stop(): void` — (선택) clearTimeout + removeEventListener

**예상 LOC**: ~50

**함정**:
- 5개 활동 이벤트(mousemove/mousedown/keydown/touchstart/scroll) 모두 `{ passive: true }` 보존
- **이중 start 가드**: module-private `let idleTimer = null; let started = false; function start() { if (started) return; started = true; ... }`

---

## 7. 검증 절차

### 7.1 정적 검증 (Phase 3와 동일)

1. **함수 인벤토리 diff**:
   ```bash
   # 분리 전
   grep -oE "function [a-zA-Z_]+" static/js/work.js | sort -u > /tmp/before.txt
   # 분리 후
   grep -ohE "function [a-zA-Z_]+" static/js/work.js static/js/work/*.js | sort -u > /tmp/after.txt
   diff /tmp/before.txt /tmp/after.txt
   ```
   - 추출되어 사라진 함수: 없음 (모두 모듈로 이동)
   - 신규 함수: `startStockPolling`, `bindRowActions`, `startIdleLogout` 정도 (설계상 명시)

2. **상태 캡처 의심 패턴 grep**:
   ```bash
   grep -nE "const [a-zA-Z]+ = ctx\.state\.[a-zA-Z]+\.[a-zA-Z]+" static/js/work/*.js
   ```
   - 결과 = 0건 (객체 참조 캡처 `const x = ctx.state.weighing` 형태만 허용)

3. **모듈 LOC 한도**:
   ```bash
   wc -l static/js/work/*.js | awk '$1 > 250'
   ```
   - 결과 = 0건

### 7.2 동적 검증

1. **pytest 전수** — `pytest tests/ -x -q` 통과
2. **JS 단위 테스트** — 기존 4개 + 신규 1~2개 통과 (vm.runInNewContext 패턴은 [[feedback_browser_smoke_pattern]] 참고)
3. **브라우저 스모크** ([[feedback_browser_smoke_pattern]]):
   - IRMS_DATA_DIR=tmp_split_work_js 격리
   - 시드 매니저 120206 등 + 시드 레시피 2~3건
   - Playwright 시나리오:
     a. `/weighing/select`에서 매니저 로그인
     b. `/weighing` 진입 → 테이블 표시, 콘솔 0건
     c. 파우더 모드 진입 → 큐 표시 → Enter로 1스텝 진행 → 토스트 확인
     d. Esc로 종료 → 액상 모드 → 컬러 선택 → 종료
     e. 테이블에서 완료 버튼 → confirm OK → 행 사라짐
     f. visibility 변화 시뮬레이션 → 콘솔 0건
   - 콘솔 에러 0건 (favicon 404 제외)

### 7.3 gap-detector

`design ↔ implementation` Match Rate ≥ 90%. Phase 1·2·3 모두 99% 달성 — 동일 패턴이라 유사 수준 예상.

---

## 8. 함정 (Phase 2·3 학습 사항)

1. **Write 도구 제어문자 변환** ([[feedback_write_tool_control_chars]]):
   - 본 분리에는 정규식이 거의 없어 영향 적음. 단, `escapeHtml` 등은 common.js에 있으므로 work 모듈은 호출만.

2. **단위 테스트 모듈 로드 순서**:
   - Phase 2에서 12개 common.js 모듈 순차 로드 helper를 만들었음. work 모듈은 페이지별 IRMS.work.createX 팩토리만 테스트하면 됨 → `tests/js/management_lookup.test.js` 패턴 그대로 사용.

3. **lastSpokenStepKey 위치**:
   - 현재 코드 L370 `weighing.lastSpokenStepKey = null`은 `state.weighing.lastSpokenStepKey = null`로 치환. weighing 객체 자체는 가변이므로 키 추가/접근 모두 안전.

4. **JS 13줄 로드 블록 (Phase 2 산물)**:
   - `templates/work.html` L147 `chat.js` 다음 줄에 `work/*.js` 6개 삽입. `common.js` 12개 모듈은 base 템플릿에서 이미 로드됨 → work 모듈은 IRMS.* API 자유롭게 사용 가능.

5. **`IRMS.colorLabel`은 common.js의 colors 모듈에서 export**:
   - `static/js/common/colors.js` 등에 정의. work.js 컨트롤러가 진입할 때 이미 로드 완료 → `ctx.colorLabel = IRMS.colorLabel` 캡처 안전.

---

## 9. 산출물 체크리스트

- [ ] `static/js/work/stock-banner.js` (~70 LOC)
- [ ] `static/js/work/recipe-table.js` (~140 LOC)
- [ ] `static/js/work/import-notifications.js` (~130 LOC)
- [ ] `static/js/work/weighing-render.js` (~165 LOC)
- [ ] `static/js/work/weighing-actions.js` (~215 LOC)
- [ ] `static/js/work/idle-logout.js` (~50 LOC)
- [ ] `static/js/work.js` 축소 (760 → ~230 LOC; 키보드 단축키/계량 버튼 바인딩을 §5.2 허용대로 컨트롤러 인라인 유지)
- [ ] `templates/work.html` `<script>` 블록 7줄로 갱신
- [ ] `tests/js/work_pure.test.js` 신규 (countRecipeMaterials, getQueueColorCounts)
- [ ] 분리 전/후 함수 인벤토리 diff = 의도된 신규만
- [ ] 모듈 LOC 한도 ≤ 250
- [ ] pytest + JS 테스트 PASS
- [ ] 브라우저 스모크 PASS (콘솔 에러 0건)

---

## Version History

| Version | Date | Changes | Author |
|---|---|---|---|
| 0.1 | 2026-05-27 | Initial draft (Phase 4 of split-large-files, ctx 스키마 + 함수 매핑표 + 와이어링 순서 확정) | ykh00046 |
