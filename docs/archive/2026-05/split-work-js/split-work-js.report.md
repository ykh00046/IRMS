# split-work-js Completion Report

> Phase 4 of split-large-files initiative. PDCA 사이클 종료 보고.

| Item | Value |
|---|---|
| Feature | split-work-js |
| PDCA Cycle | Plan → Design → Do → Check → Report |
| Started | 2026-05-27 |
| Completed | 2026-05-27 |
| Author | ykh00046 |
| Match Rate | **99%** (Phase 1·2·3와 동일 baseline) |
| Status | Complete |

---

## Summary

`static/js/work.js` (760 LOC, 단일 `DOMContentLoaded` 페이지 컨트롤러)를 책임별 6개 팩토리 모듈 + 얇은 진입 컨트롤러(232 LOC)로 분리. 화면 동작 100% 보존. 번들러·ESM·TypeScript 도입 없음. Phase 3 (split-management-js)에서 정착시킨 **팩토리 + 공유 ctx** 패턴을 그대로 계승했다.

이로써 split-large-files 시리즈가 4-Phase 모두 완주:

| Phase | 대상 | 원본 LOC | 모듈 수 | Match Rate | 완료일 |
|------:|---|---:|---:|---:|---|
| 1 | `src/routers/recipe_routes.py` | 1,132 | 5 라우터 + helper | 99% | 2026-05-13 |
| 2 | `static/js/common.js` | 1,218 | 11 모듈 + bootstrap | 99% | 2026-05-19 |
| 3 | `static/js/management.js` | 1,006 | 5 팩토리 + 컨트롤러 | 99% | 2026-05-19 |
| **4** | **`static/js/work.js`** | **760** | **6 팩토리 + 컨트롤러** | **99%** | **2026-05-27** |

---

## Deliverables

### 신규 파일 (7개)
- `static/js/work/stock-banner.js` (68 LOC) — 재고 배너 폴링 (30s)
- `static/js/work/recipe-table.js` (153 LOC) — 처리 대기 테이블 + 행 액션
- `static/js/work/import-notifications.js` (121 LOC) — Import 알림 폴링 (8s, visibility-aware)
- `static/js/work/weighing-render.js` (165 LOC) — 계량 패널 렌더 + 컨트롤 동기화
- `static/js/work/weighing-actions.js` (199 LOC) — 계량 모드 액션 + 큐 관리
- `static/js/work/idle-logout.js` (62 LOC) — 30분 비활동 자동 로그아웃
- `tests/js/work_pure.test.js` — 신규 순수 함수 + 팩토리 시그니처 테스트 9건

### 수정 파일 (2개)
- `static/js/work.js` 760 → 232 LOC (DOM 수집 + ctx 생성 + 모듈 조립 + 이벤트 바인딩 + 부팅만 잔존)
- `templates/work.html` L147-148 1줄 → L147-156 9줄 (6 모듈 + 컨트롤러 마지막)

---

## Validation

### 정적
- 함수 인벤토리 diff: 의도된 리네이밍만 (모든 사라진 함수는 모듈 내부 단축명으로 재명명, 모든 신규 함수는 모듈 핸들)
- 상태 캡처 의심 패턴: 0건 (원시값 캡처 금지 컨벤션 준수)
- 모듈 LOC ≤ 250 한도: 6/6 통과 (최대 199)
- 교차 모듈 직접 import: 0건 (모든 호출이 `ctx` 경유)

### 동적
- pytest: **40/40 passed**
- JS 테스트: **5/5 passed** (기존 4개 + 신규 1개)
- 신규 테스트: `countRecipeMaterials`, `getQueueColorCounts`, `resetProgress`, 6개 팩토리 시그니처

### Agent
- design-validator: 94 → 98 (3건 fix 적용: `<script>` 블록 재현, polling guard 명시, IDLE_TIMEOUT_MS 상수)
- gap-detector: **99%** (Match Rate, Phase 1·2·3와 동일)

---

## 부수적 개선

### 원본 ReferenceError 위험 안전화
원본 work.js L607: `chatModule.bindForm({ ..., stage: chatStage, ... })` — `chatStage`가 미정의 식별자였음(work.html에 `chat-stage` 요소 없음, chat.js에 글로벌 등록도 없음). 새 컨트롤러는 `dom.chatStage = document.getElementById("work-chat-stage")` (= `null`)로 명시 수집해 `chat.js`의 `stage?.value` 안전 처리에 위임. 페이지에서 실제로 stage 입력 UI를 쓰지 않으므로 동작 영향 0, ReferenceError 잠재 위험 제거.

### 폴링 이중 start 가드 표준화
3개 폴링(stock-banner, import-notifications, idle-logout)에 모두 명시적 가드를 적용. 향후 모듈을 재사용/재초기화할 때 setInterval 누적 위험 제거.

---

## Lessons learned (Phase 4 학습 사항)

1. **컨트롤러 LOC는 페이지 복잡도에 비례** — Phase 3 management.js(1,006 LOC, 4 탭)는 컨트롤러 252 LOC였고, Phase 4 work.js(760 LOC, 단일 화면 + 계량 모달)도 컨트롤러 232 LOC. 화면당 키보드/버튼 바인딩 inline 분량이 비슷.

2. **`weighing` sub-state 객체 캡처 패턴** — 모듈 진입 시 `const weighing = state.weighing;`처럼 **객체 참조까지만 캡처** 허용 (Design §4.2). 객체 내부 key는 매번 읽음. 원시값 캡처는 금지. design-validator/gap-detector 모두 grep으로 검증.

3. **테스트 `assert.deepEqual` 함정** — 객체 키 순서가 같아도 strict 모드에서 fail 발생 사례. 개별 키별 `assert.equal`로 분해 작성 추천.

4. **vm.runInNewContext sandboxing** — Phase 3 management_lookup.test.js 패턴을 work_pure.test.js로 이식. 모듈별 IRMS stub만 다르게 주입.

5. **원본 잠재 버그 발견** — 분리 작업 자체로 인해 `chatStage` 미정의 식별자 같은 잠재 ReferenceError가 가시화되는 부수 이득. 회귀 위험 없이 안전화 가능.

---

## 후속 작업

split-large-files 시리즈 종료. 다음 부채 후보:
- `src/services/attendance_excel.py` (791 LOC, 최대 Python 파일)
- `src/database.py` (719 LOC, 잉크/사출 폐기 잔존 고아 테이블 6개 DROP 동반)
- `static/js/status.js` (582 LOC), `admin_users.js` (534 LOC), `spreadsheet_editor.js` (680 LOC) — Phase 3 패턴 재사용 가능

또는 미해결 활성 피처:
- `cloudflare-tunnel-access` 운영자 sign-off 마무리 (Match 98%, 5월 24일 archived)

---

## Archive 위치

`docs/archive/2026-05/split-work-js/`로 이동:
- `split-work-js.plan.md`
- `split-work-js.design.md`
- `split-work-js.analysis.md`
- `split-work-js.report.md` (본 문서)
