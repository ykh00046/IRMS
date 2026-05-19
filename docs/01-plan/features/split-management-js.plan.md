# Split management.js Plan (Phase 3 of split-large-files initiative)

> `static/js/management.js` 1,006줄(단일 `DOMContentLoaded` 페이지 컨트롤러)을 책임별 5개 팩토리 모듈 + 얇은 컨트롤러로 분리하는 리팩터링 계획서. 화면 동작 100% 보존, 번들러 도입 없음. 이미 정착된 `IRMS.createChat` 팩토리 패턴을 따른다.

## 1. Overview

| Item | Detail |
|------|--------|
| Feature | split-management-js (Phase 3) |
| Priority | Medium (장기 부채 정리, Phase 1·2 후속) |
| Base | 2026-05-19 main, commit `28aa888` |
| Goal | `static/js/management.js` (1,006 LOC, 단일 IIFE 컨트롤러) → 책임별 5개 팩토리 모듈 + 얇은 진입 컨트롤러 |
| Deliverable | `static/js/management/*.js` (5개) + 얇아진 `static/js/management.js` + `management.html` `<script>` 로드 순서 갱신 |
| Author | ykh00046 |
| Date | 2026-05-19 |
| Status | Draft |
| Parent PDCA | [`docs/archive/2026-05/split-large-files`](../../archive/2026-05/split-large-files/), [`docs/archive/2026-05/split-common-js`](../../archive/2026-05/split-common-js/) |

---

## 2. Problem Statement

`management.js`는 `document.addEventListener("DOMContentLoaded", () => { ... })` 한 덩어리(1,006줄)로 `/management` 페이지의 4개 탭(Import / 이력 / Lookup / Chat)을 모두 제어한다:

1. **DOM 참조 수집** (L2-68) — `getElementById`/`querySelector` 약 50개
2. **공유 가변 상태** (L41-95) — `currentPreview`, `materials`, `sheet`, `confirmedRawText`, `previewIsStale`, `suppressDirtyTracking`, `spreadsheetFallbackNotified`, `currentHistoryChain`, `selectedRecipeId`, `pendingRevisionOf`, `chatState` 등
3. **스프레드시트 편집기** (L97-313) — jspreadsheet 생성·파괴·데이터 추출 6개 함수
4. **검증/등록** (L175-184, L329-563) — `markPreviewStale`, `syncRegisterState`, `renderIssues`, `renderValidationMeta`, `handlePreview`, `handleRegister`, `handleClear`
5. **레시피 이력 탭** (L128-173, L356-486) — 필터 4개 함수 + `renderHistory`(아코디언 상세행)
6. **Lookup 탭** (L603-947) — 제품 조회·피벗 테이블·복사·복제 등 11개 함수
7. **버전 이력/비교 모달** (L633-760) — `renderHistoryModal`, `handleCompareVersions`, `renderCompareModal` 등
8. **이벤트 와이어링 + 초기화 IIFE** (L565-1005)

### Pain Points

1. **코드 리뷰 부담** — Lookup 탭 버튼 하나 고치는 PR이 1,006줄 파일 전체로 표시됨
2. **머지 충돌** — Import 탭과 Lookup 탭 작업이 같은 파일을 동시 편집
3. **책임 추적 어려움** — `markPreviewStale` 호출 경로가 4개 책임 영역에 흩어짐
4. **테스트 불가** — 전부 `DOMContentLoaded` 클로저 안 → 단위 테스트 import 불가

### Phase 2와의 결정적 차이

`common.js`(Phase 2)는 **독립 함수 라이브러리**라 도메인별 IIFE로 쉽게 쪼갰다. `management.js`는 **단일 페이지 컨트롤러**다:

- `window.IRMS` 같은 **공개 API가 없다** — 보존할 시그니처 계약이 없고, 계약은 "`/management` 페이지 동작이 동일하다"뿐
- 함수들이 **가변 클로저 상태를 공유**한다 (`sheet`, `currentPreview`, `selectedRecipeId`를 여러 함수가 읽고 쓴다) → 단순 IIFE 분리 불가
- 모듈 간 **상호 호출**이 있다 (예: Lookup의 `handleLookupClone` → 스프레드시트의 `destroySpreadsheet` + 검증의 `markPreviewStale`)

→ 따라서 Phase 2의 "IIFE 증분 부착" 패턴이 아니라, **팩토리 함수 + 공유 컨텍스트** 패턴을 쓴다 (§8).

---

## 3. Feature Items

> 각 모듈은 `IRMS.management.create<Name>(ctx)` 팩토리 함수로 노출된다. `ctx`는 컨트롤러가 만든 공유 컨텍스트(DOM 참조 + 가변 상태 + 교차 모듈 콜백). 코드 위치는 신규 디렉터리 `static/js/management/`.

### 3.1 `static/js/management/spreadsheet-editor.js` 신규

| Item | Detail |
|------|--------|
| 목표 | jspreadsheet/jexcel 인스턴스 생명주기 관리 |
| 포함 함수 | `getSpreadsheetFactory`, `setRawInputMode`, `destroySpreadsheet`, `getActiveWorksheet`, `initSpreadsheet`, `getSpreadsheetDataAsText` |
| 팩토리 | `IRMS.management.createSpreadsheetEditor(ctx)` |
| 의존 | `ctx.dom`(spreadsheetContainer, rawInput), `ctx.state`(sheet, suppressDirtyTracking, spreadsheetFallbackNotified), `ctx.onDirty`(→ markPreviewStale) |
| 예상 LOC | ~210 |

### 3.2 `static/js/management/import-validate.js` 신규

| Item | Detail |
|------|--------|
| 목표 | Import 탭 — 검증·등록·초기화 흐름 |
| 포함 함수 | `markPreviewStale`, `syncRegisterState`, `renderIssues`, `renderValidationMeta`, `handlePreview`, `handleRegister`, `handleClear` |
| 팩토리 | `IRMS.management.createImportValidate(ctx)` |
| 의존 | `ctx.dom`(previewBtn, registerBtn, previewMeta, errorList, warningList), `ctx.state`(currentPreview, confirmedRawText, previewIsStale, pendingRevisionOf), `ctx.spreadsheet`(getSpreadsheetDataAsText, initSpreadsheet) |
| 예상 LOC | ~130 |

### 3.3 `static/js/management/recipe-history.js` 신규

| Item | Detail |
|------|--------|
| 목표 | 이력 탭 — 필터 저장·복원 + 이력 테이블 + 아코디언 상세행 |
| 포함 함수 | `persistHistoryFilters`, `updateHistorySummary`, `restoreHistoryFilters`, `resetHistoryFilters`, `renderHistory` |
| 팩토리 | `IRMS.management.createRecipeHistory(ctx)` |
| 의존 | `ctx.dom`(historyBody, historyStatus/Search/From/To, historySummary), `ctx.onClone`(→ lookup.handleLookupClone) |
| 예상 LOC | ~180 |

### 3.4 `static/js/management/recipe-lookup.js` 신규

| Item | Detail |
|------|--------|
| 목표 | Lookup 탭 — 제품별 레시피 피벗 조회·선택·복사·복제 |
| 포함 함수 | `loadProducts`, `setLookupSelection`, `handleLookup`, `copyToClipboard`, `handleLookupCopy`, `handleLookupClone` |
| 팩토리 | `IRMS.management.createRecipeLookup(ctx)` |
| 의존 | `ctx.dom`(lookup* 요소), `ctx.state`(selectedRecipeId, pendingRevisionOf), `ctx.spreadsheet`(destroySpreadsheet, getSpreadsheetFactory), `ctx.importValidate`(renderValidationMeta, renderIssues, syncRegisterState), `ctx.tabs`(switchTab) |
| 예상 LOC | ~190 |

### 3.5 `static/js/management/version-compare.js` 신규

| Item | Detail |
|------|--------|
| 목표 | 버전 이력 모달 + 버전 비교 모달 |
| 포함 함수 | `handleLookupHistory`, `renderHistoryModal`, `getSelectedVersionIds`, `updateCompareButtonState`, `handleCompareVersions`, `renderCompareModal` |
| 팩토리 | `IRMS.management.createVersionCompare(ctx)` |
| 의존 | `ctx.dom`(historyModal*, compareModal*, versionHistoryBody, compareThead/Tbody), `ctx.state`(selectedRecipeId, currentHistoryChain), `ctx.onClone`(→ lookup.handleLookupClone) |
| 예상 LOC | ~140 |

### 3.6 `static/js/management.js` 진입 컨트롤러 축소

| Item | Detail |
|------|--------|
| 목표 | 기존 1,006줄 → ~160줄 컨트롤러 전용 |
| 잔여 책임 | DOM 참조 수집, 탭 네비게이션, 공유 `ctx` 객체 생성, 5개 모듈 인스턴스화 + 교차 와이어링, 이벤트 리스너 바인딩, `IRMS.createChat` 연결, 초기화 IIFE |
| 예상 LOC | ~160 |

### 3.7 `templates/management.html` `<script>` 순서 갱신

현재 (L402-407): `jsuites → jspreadsheet → chat.js → spreadsheet_editor.js → management.js → stock.js`.
변경 후 — `management.js` 한 줄을 6줄로 교체:

```html
<!-- Order: 4 modules → controller last -->
<script src="/static/js/management/spreadsheet-editor.js"></script>
<script src="/static/js/management/import-validate.js"></script>
<script src="/static/js/management/recipe-history.js"></script>
<script src="/static/js/management/recipe-lookup.js"></script>
<script src="/static/js/management/version-compare.js"></script>
<script src="/static/js/management.js"></script>  <!-- controller last -->
```

모듈은 팩토리만 등록(`IRMS.management.create*`)하고 즉시 실행하지 않으므로 5개 모듈 간 로드 순서는 무관하다. `management.js`(컨트롤러)만 마지막이면 된다.

---

## 4. Scope

### 4.1 In Scope

- [ ] 5개 신규 `static/js/management/*.js` 팩토리 모듈 생성
- [ ] `static/js/management.js` 축소 (1,006 → ~160줄)
- [ ] `templates/management.html` `<script>` 로드 블록 갱신 (1개 템플릿)
- [ ] `/management` 페이지 4개 탭 동작 100% 보존
- [ ] `copyToClipboard` 등 순수 함수에 대한 최소 JS 단위 테스트 1~2개 신규 (현재 management 테스트 0건)
- [ ] 4개 탭 수동 스모크 (Import 검증·등록, 이력 필터·상세, Lookup 조회·복제, 버전 비교)

### 4.2 Out of Scope

- ❌ 번들러(esbuild/vite/webpack) 도입
- ❌ ES Modules(`import`/`export`) 전환, TypeScript
- ❌ 로직 변경·성능 개선·UX 변경 (순수 분리만)
- ❌ `static/js/common.js` / `chat.js` / `spreadsheet_editor.js` / `stock.js` 수정
- ❌ Phase 4 (`work.js` ~760 LOC) — 별도 PDCA

---

## 5. Requirements

### 5.1 Functional Requirements

| ID | Requirement | Priority | Status |
|----|-------------|----------|--------|
| FR-01 | `/management` 4개 탭 동작 100% 보존 (검증·등록·이력·Lookup·복제·버전비교·채팅) | High | Pending |
| FR-02 | 분리 후 단일 모듈 ≤ 250 LOC | High | Pending |
| FR-03 | 공유 상태(`ctx.state`)는 단일 객체로, 모든 모듈이 동일 참조 공유 (값 복사 금지) | High | Pending |
| FR-04 | 교차 모듈 호출은 `ctx`의 콜백/모듈 핸들로만 (모듈이 서로 직접 import 금지) | High | Pending |
| FR-05 | 브라우저 콘솔 에러 0건 (4개 탭 골든패스) | High | Pending |
| FR-06 | 기존 JS 테스트 3개(`tests/js/`) 영향 없음(회귀 0) | Medium | Pending |

### 5.2 Non-Functional Requirements

| Category | Criteria | Measurement |
|---|---|---|
| 페이지 로드 시간 | 기존 대비 +200ms 이내 (`<script>` 5개 추가) | DevTools Network, 캐시 비활성화 |
| 콘솔 에러 | `Uncaught ReferenceError`/`TypeError` 0건 | DevTools Console |
| 상태 일관성 | 탭 전환·복제 시 `sheet`/`selectedRecipeId` 등 공유 상태가 모듈 간 어긋나지 않음 | 수동 스모크 (Lookup→복제→Import 흐름) |

---

## 6. Success Criteria

### 6.1 Definition of Done

- [ ] 5개 신규 모듈 + 축소된 `management.js` 생성
- [ ] `management.html` `<script>` 갱신 완료
- [ ] 4개 탭 골든패스 수동 스모크 PASS
- [ ] 콘솔 에러 0건
- [ ] 신규 순수함수 테스트 PASS + 기존 JS 테스트 3개 PASS
- [ ] PDCA 분석 ≥ 90% + 완료 보고서

### 6.2 Quality Criteria

- [ ] 단일 모듈 ≤ 250 LOC
- [ ] 각 모듈 상단에 책임·`ctx` 의존성 JSDoc
- [ ] 교차 모듈 직접 호출 0건 (`ctx` 경유만)

---

## 7. Risks and Mitigation

| Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|
| 공유 클로저 상태가 모듈 분리 후 어긋남 (값 복사 → 상태 불일치) | High | High | `ctx.state`를 **단일 객체**로 만들고 모든 모듈이 동일 참조 공유. 원시값 직접 캡처 금지, 항상 `ctx.state.<key>`로 읽고 씀. Design 단계에서 `ctx` 스키마 확정 |
| 교차 모듈 순환 호출 (history↔lookup↔import-validate) | High | High | 컨트롤러가 모듈 인스턴스를 만든 뒤 `ctx`에 핸들을 주입하는 **2단계 와이어링**. Design §에서 의존 방향 그래프 명시 |
| 화면 동작 회귀 (공개 API 계약 없음 → 정적 검증 불가) | High | Medium | 4개 탭 전수 수동 스모크 체크리스트 작성. 분리 전/후 동일 시나리오 비교 |
| `management.html` 외 다른 페이지가 management.js 로드 | Low | Low | `grep -l management.js templates/` = 1개(`management.html`)임을 사전 확인 |
| jspreadsheet `onchange` 콜백이 분리 후 `markPreviewStale` 참조를 못 찾음 | High | Medium | `initSpreadsheet`가 `ctx.onDirty()`를 호출하도록. 콜백은 컨트롤러 와이어링 시점에 주입 |
| JS 테스트 부재로 회귀 탐지 약함 | Medium | High | 순수 함수(`copyToClipboard` 등) 최소 테스트 신규 추가. 나머지는 수동 스모크로 보완 |

---

## 8. Architecture Considerations

### 8.1 팩토리 + 공유 컨텍스트 패턴 (선택)

이미 `IRMS.createChat({ prefix, elements, state })`가 같은 패턴으로 `chat.js`에 존재하고 management/status/work 3개 페이지가 소비 중 → **검증된 선례**. Phase 3는 동일 패턴을 management 전용으로 확장한다.

```javascript
// static/js/management/import-validate.js
(function () {
  "use strict";
  const NS = (window.IRMS = window.IRMS || {});
  NS.management = NS.management || {};

  NS.management.createImportValidate = function (ctx) {
    function markPreviewStale() { /* ctx.state.previewIsStale = true; ... */ }
    function handlePreview() { /* ... */ }
    // ...
    return { markPreviewStale, handlePreview, handleRegister, handleClear,
             renderValidationMeta, renderIssues, syncRegisterState };
  };
})();
```

```javascript
// static/js/management.js (얇은 컨트롤러)
document.addEventListener("DOMContentLoaded", () => {
  const ctx = {
    dom: { /* getElementById 묶음 */ },
    state: { currentPreview: null, sheet: null, selectedRecipeId: null, /* ... */ },
    tabs: { switchTab },
  };
  const spreadsheet = IRMS.management.createSpreadsheetEditor(ctx);
  ctx.spreadsheet = spreadsheet;
  const importValidate = IRMS.management.createImportValidate(ctx);
  ctx.importValidate = importValidate;
  const lookup = IRMS.management.createRecipeLookup(ctx);
  const history = IRMS.management.createRecipeHistory(ctx);
  const compare = IRMS.management.createVersionCompare(ctx);
  // 교차 콜백 주입 (2단계 와이어링)
  ctx.onDirty = importValidate.markPreviewStale;
  ctx.onClone = lookup.handleLookupClone;
  // 이벤트 바인딩 + 초기화 ...
});
```

**선택 이유**: 번들러 0, 기존 `<script>` 모델 유지, `IRMS.createChat` 패턴 일관성, 공유 상태를 단일 객체로 안전 공유.

### 8.2 모듈 의존 방향 (Design에서 확정)

```
spreadsheet-editor  (ctx.dom, ctx.state, ctx.onDirty)
import-validate     (+ ctx.spreadsheet)
recipe-lookup       (+ ctx.spreadsheet, ctx.importValidate, ctx.tabs)
recipe-history      (+ ctx.onClone)
version-compare     (+ ctx.onClone)
management.js       컨트롤러 — ctx 생성·5개 모듈 조립·와이어링
```

순환 없음: 컨트롤러가 유일한 조립 지점. lookup↔history는 직접 호출하지 않고 `ctx.onClone` 콜백으로만 연결.

---

## 9. Convention Prerequisites

| Target | Rule | Example |
|---|---|---|
| 파일명 | `management/<scope>.js` (케밥 케이스) | `management/recipe-lookup.js` |
| 모듈 IIFE | `(function () { "use strict"; ... })();` 통일 | §8.1 |
| 팩토리 네임스페이스 | `IRMS.management.create<PascalName>` | `createImportValidate` |
| 공유 상태 접근 | 항상 `ctx.state.<key>` (원시값 캡처 금지) | `ctx.state.selectedRecipeId` |
| 모듈 헤더 | 책임·`ctx` 의존·반환 핸들 JSDoc 1단락 | — |

---

## 10. Implementation Order

```
Step 1: 분리 전 inventory
  - management.js 함수 31개 + 공유 상태 13개 + DOM 참조 50개 목록화
  - 각 함수가 읽고/쓰는 상태, 호출하는 다른 함수 매핑표 작성 (Design 입력)

Step 2: ctx 스키마 확정 (Design 산출물)
  - ctx.dom / ctx.state / ctx.spreadsheet / ctx.importValidate / ctx.tabs / ctx.onDirty / ctx.onClone

Step 3: 모듈 5개 생성
  - spreadsheet-editor → import-validate → recipe-history → recipe-lookup → version-compare

Step 4: management.js 컨트롤러 축소
  - DOM 수집 + ctx 생성 + 모듈 조립 + 2단계 와이어링 + 이벤트 바인딩 + 초기화 IIFE만 잔존

Step 5: 템플릿 + 검증
  - management.html <script> 6줄 교체
  - 4개 탭 수동 스모크 (Import/이력/Lookup/Chat)
  - 신규 순수함수 테스트 + 기존 JS 테스트 3개 실행
  - 콘솔 에러 0건 확인
```

---

## 11. PR 전략

- **단일 PR**: Step 1~5 한 PR (중간 상태는 페이지 깨짐 → bisect 곤란)
- **커밋 단위**: Step 3 모듈별 커밋, Step 4·5 각각 별도 커밋
- **롤백**: `git revert <merge-commit>` 한 번 (서버·DB 영향 없음, 단일 템플릿)

---

## 12. Next Steps

1. [ ] 본 Plan 검토·승인
2. [ ] `/pdca design split-management-js` — `ctx` 스키마 + 함수↔상태 매핑표 + 의존 그래프 확정
3. [ ] `/pdca do split-management-js` — Step 1~5 실행
4. [ ] `/pdca analyze split-management-js` — gap-detector 검증
5. [ ] `/pdca report split-management-js` + archive
6. [ ] Phase 4 (`work.js` ~760 LOC) → `/pdca plan split-work-js`

---

## Version History

| Version | Date | Changes | Author |
|---|---|---|---|
| 0.1 | 2026-05-19 | Initial draft (Phase 3 of split-large-files initiative) | ykh00046 |
