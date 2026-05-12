# Split Large Files Plan

> 1,000줄을 넘긴 4개 파일을 책임 단위로 분리하여 유지보수성·테스트 용이성·코드 리뷰 부담을 개선하기 위한 리팩터링 계획서

## 1. Overview

| Item | Detail |
|------|--------|
| Feature | split-large-files |
| Priority | Medium (장기 부채 정리) |
| Base | 2026-05-12 main 브랜치 (commit `b808920`) |
| Goal | 1,000+ LOC 파일 4개를 200~400줄 수준의 모듈로 분리 |
| Deliverable | 라우터/JS 모듈 분리 PR + 회귀 테스트 통과 |
| Author | ykh00046 |
| Date | 2026-05-12 |
| Status | Draft |

---

## 2. Problem Statement

현장 안정화 사이클을 거치며 핵심 파일 4개가 1,000줄 이상으로 비대해졌다. 단일 파일에 다수 도메인(레시피·재고·통계·채팅·계량 등)이 혼재해 있어:

1. **변경 영향 범위 추적 어려움** — `recipe_routes.py` 한 곳에 operator/manager/import/stats가 섞여 있어 한 줄 수정도 전체 검토 필요
2. **테스트 추가 진입 장벽** — `common.js` 1,218줄에 `window.IRMS` 단일 객체로 모든 API/유틸/오디오/매퍼가 들어있어 단위 테스트가 사실상 불가능
3. **코드 리뷰 피로도** — 관련 변경이 여러 도메인 코드와 한 화면에서 보임
4. **충돌 위험** — 기능별 병렬 작업 시 같은 파일에서 머지 충돌 빈발

### 분리 대상

| 파일 | 줄 수 | 주요 도메인 |
|---|---:|---|
| `static/js/common.js` | 1,218 | CSRF·request·매퍼·API(users/recipes/chat/stock/spreadsheet)·포매터·UI helper·오디오·polling |
| `src/routers/recipe_routes.py` | 1,132 | operator(레시피/재료/재고-조회) + manager(재고-쓰기/진행률/import/통계) |
| `static/js/management.js` | 1,006 | 스프레드시트·이력·채팅·조회·모달 (탭별 코드 혼재) |
| `static/js/work.js` | 760 | 계량 워크플로우·채팅·테이블 |

---

## 3. Feature Items

### 3.1 `src/routers/recipe_routes.py` 분리 (Phase 1)

| Item | Detail |
|------|--------|
| 목표 | 1,132줄 라우터를 도메인별 5개 모듈로 분리 |
| 분리 단위 | operator routes / manager-recipe routes / stock routes / stats routes / import routes |
| 신규 파일 | `src/routers/recipe_operator_routes.py`, `src/routers/recipe_manager_routes.py`, `src/routers/stock_routes.py`, `src/routers/recipe_stats_routes.py`, `src/routers/recipe_import_routes.py` |
| 공유 헬퍼 | `_format_display_value`, `_fetch_recipe_items`, `_find_chain_root`, `_fetch_chain`, `_ensure_material` → `src/services/recipe_helpers.py` |
| 등록 위치 | `src/routers/api.py`에서 `include_router(...)` 5개 호출 (기존 한 줄 → 5줄로 변경) |
| 리스크 | URL 경로·응답 스키마 보존 필수 (프론트엔드 깨짐 방지) |
| 회귀 검증 | 기존 attendance 테스트 + 신규 라우터별 smoke 테스트 |

### 3.2 `static/js/common.js` 분리 (Phase 2)

| Item | Detail |
|------|--------|
| 목표 | 1,218줄 IIFE를 책임별 7개 파일로 분리, `window.IRMS` 단일 export 유지 (호환성) |
| 분리 단위 | core(request/csrf) / mappers / api-users / api-recipes / api-stock / api-spreadsheet / api-chat / format / ui / audio / polling |
| 신규 파일 | `static/js/common/core.js`, `static/js/common/mappers.js`, `static/js/common/api-users.js`, `static/js/common/api-recipes.js`, `static/js/common/api-stock.js`, `static/js/common/api-spreadsheet.js`, `static/js/common/api-chat.js`, `static/js/common/format.js`, `static/js/common/ui.js`, `static/js/common/audio.js`, `static/js/common/polling.js` |
| 진입점 | `static/js/common.js`는 위 모듈을 합쳐 `window.IRMS`를 노출하는 얇은 래퍼로 축소 |
| 모듈 시스템 | **번들러 도입 안 함** — 템플릿에서 순서 보장된 다중 `<script>` 태그 + 각 모듈은 `window.IRMS = window.IRMS \|\| {}; window.IRMS.api = ...` 패턴으로 점진 확장 |
| 리스크 | 스크립트 로드 순서 의존, 템플릿 다수 수정 필요 |
| 회귀 검증 | `tests/js/` Jest 추가 + 수동 브라우저 스모크 (관리자/작업자/근태 페이지) |

### 3.3 `static/js/management.js` 분리 (Phase 3)

| Item | Detail |
|------|--------|
| 목표 | 1,006줄을 탭(스프레드시트·이력·채팅·조회) 단위로 분리 |
| 분리 단위 | spreadsheet 탭 / history 탭 / chat 탭 / lookup 탭 / 공유 모달 |
| 신규 파일 | `static/js/management/spreadsheet.js`, `static/js/management/history.js`, `static/js/management/chat.js`, `static/js/management/lookup.js`, `static/js/management/modals.js` |
| 진입점 | `static/js/management.js`는 탭 라우팅만 담당 (~50줄 예상) |
| 리스크 | DOM 요소 참조가 모듈 간 공유될 수 있음 → 각 모듈이 자체 DOM 캐시 수행 |
| 회귀 검증 | 수동 브라우저 스모크 (관리자 페이지 4개 탭 모두) |

### 3.4 `static/js/work.js` 분리 (Phase 4)

| Item | Detail |
|------|--------|
| 목표 | 760줄을 계량/채팅/테이블 단위로 분리 |
| 분리 단위 | weighing 컨트롤러 / chat 컨트롤러 / 테이블 렌더러 |
| 신규 파일 | `static/js/work/weighing.js`, `static/js/work/chat.js`, `static/js/work/table.js` |
| 진입점 | `static/js/work.js`는 진입 부트스트랩만 (~50줄 예상) |
| 리스크 | 계량 흐름이 채팅·테이블과 상태 공유 → 이벤트 기반 디커플 필요 |
| 회귀 검증 | 수동 브라우저 스모크 (작업자 계량 골든패스: 시작 → 완료 → 되돌리기) |

---

## 4. Scope

### 4.1 In Scope

- [ ] Phase 1: `recipe_routes.py` → 5개 라우터 + 1개 헬퍼 모듈로 분리
- [ ] Phase 2: `common.js` → 11개 모듈 + 진입점으로 분리 (`window.IRMS` API 호환)
- [ ] Phase 3: `management.js` → 5개 모듈로 분리
- [ ] Phase 4: `work.js` → 3개 모듈로 분리
- [ ] 각 Phase별 PR 분리 (4개 PR), 단계마다 수동 스모크 통과 후 다음 Phase 진행
- [ ] `docs/IRMS-overview.md`에 새 모듈 구조 1단락 추가

### 4.2 Out of Scope

- ❌ 번들러(esbuild/vite/webpack) 도입 — 빌드 파이프라인 추가 부담이 ROI 대비 큼
- ❌ TypeScript 마이그레이션 — 별도 PDCA 사이클로 진행
- ❌ ES Module(`import`/`export`) 전환 — 브라우저 직접 로드 환경 유지
- ❌ 코드 동작 변경 (성능 개선·API 변경·UX 개선) — 순수 분리만 수행
- ❌ 다른 큰 파일 분리 (`admin_users.js` 533줄, `status.js` 582줄 등) — 1,000줄 미만은 제외

---

## 5. Requirements

### 5.1 Functional Requirements

| ID | Requirement | Priority | Status |
|----|-------------|----------|--------|
| FR-01 | 모든 기존 URL 경로/응답 스키마/`window.IRMS` API 시그니처 보존 | High | Pending |
| FR-02 | 분리 후 파일 평균 200~400줄, 단일 파일 600줄 초과 금지 | High | Pending |
| FR-03 | 각 모듈은 단일 도메인 책임 (operator/manager/stock/import/stats 등) | High | Pending |
| FR-04 | 템플릿의 `<script>` 태그 순서 의존성 명시 (HTML 주석 추가) | Medium | Pending |
| FR-05 | 기존 32개 pytest 모두 통과 | High | Pending |
| FR-06 | 기존 JS 테스트 (`tests/js/` 3개) 모두 통과 | High | Pending |

### 5.2 Non-Functional Requirements

| Category | Criteria | Measurement Method |
|---|---|---|
| 회귀 안전성 | 골든패스 (로그인 → 레시피 등록 → 계량 → 출고) 전 구간 동작 | 수동 브라우저 스모크 |
| 페이지 로드 시간 | 기존 대비 +200ms 이내 (`<script>` 다수화에 따른 영향 측정) | DevTools Network 탭 |
| 코드 가독성 | 분리 후 단일 파일 라인 수 평균 < 400 | `wc -l` |

---

## 6. Success Criteria

### 6.1 Definition of Done

- [ ] Phase 1~4 PR 모두 머지
- [ ] 모든 pytest + JS 테스트 통과
- [ ] 골든패스 수동 스모크 통과 (관리자 + 작업자 + 근태)
- [ ] `docs/IRMS-overview.md` 모듈 구조 단락 갱신
- [ ] PDCA 분석 (Match Rate ≥ 90%) + 완료 보고서

### 6.2 Quality Criteria

- [ ] 단일 파일 ≤ 600 LOC
- [ ] 분리된 각 모듈에 1단락 docstring/JSDoc (책임 + 의존성)
- [ ] `git diff --stat` 기준 신규 파일 수 합계 ≈ 19~24개

---

## 7. Risks and Mitigation

| Risk | Impact | Likelihood | Mitigation |
|------|---|---|---|
| 라우터 분리 시 URL 경로 누락 | High | Low | 분리 전 `grep -E '@.*\.(get\|post)' src/routers/recipe_routes.py`로 전체 경로 목록 추출 → 분리 후 동일 목록 재추출 비교 |
| `window.IRMS` API 누락으로 화면 깨짐 | High | Medium | 분리 전 `IRMS.X` 호출처를 `grep -r "IRMS\." static/ templates/`로 전수 추출 → 분리 후 모든 키 존재 확인 스크립트 |
| `<script>` 로드 순서 실수로 `undefined` 참조 | Medium | Medium | 모든 의존 모듈을 `window.IRMS` 객체에만 부착하고, 사용 시점은 DOMContentLoaded 이후로 통일 |
| 큰 PR로 인한 리뷰 부담 | Medium | High | Phase별 4개 PR로 분리, 각 PR은 단일 파일에만 영향 |
| 분리 도중 다른 기능 작업과 충돌 | Medium | Medium | 각 Phase는 단기간(1~2일) 내 완료, 머지 빈도 높임 |
| 테스트 부재 영역의 회귀 발견 못함 | High | High | 분리 PR마다 해당 도메인 라우터·페이지 수동 스모크 체크리스트 작성 + 다음 PDCA(`/pdca plan tests-coverage`)로 후속 |

---

## 8. Architecture Considerations

### 8.1 Project Level Selection

| Level | Selected |
|---|:---:|
| **Starter** | ☐ |
| **Dynamic** | ☑ |
| **Enterprise** | ☐ |

IRMS는 FastAPI + SQLite + Jinja2 기반의 단일 서버 풀스택 앱이므로 Dynamic 레벨에 해당.

### 8.2 Key Architectural Decisions

| Decision | Options | Selected | Rationale |
|---|---|---|---|
| JS 모듈화 방식 | ES Modules / IIFE 다중 파일 / 번들러 | **IIFE 다중 파일 + window.IRMS** | 빌드 파이프라인 0, 기존 `<script>` 로드 패턴 유지, 브라우저 직접 디버깅 가능 |
| Python 라우터 분리 단위 | 도메인 / 권한 / 파일별 LOC | **도메인 + 권한 혼합** | operator/manager는 인증 정책이 다르므로 권한 경계 따라 분리, 그 안에서 도메인(stock/stats/import) 분리 |
| 헬퍼 함수 위치 | 라우터 내부 / `services/` | **`services/`로 추출** | 여러 라우터에서 공유되며 서비스 계층 컨벤션과 일치 |
| 템플릿 수정 범위 | 전체 / 영향 페이지만 | **영향 페이지만** | management/work/근태 페이지 등 사용처만 수정 |

### 8.3 Folder Structure Preview

```
src/
├── routers/
│   ├── recipe_routes.py            ← 삭제 (분리 후)
│   ├── recipe_operator_routes.py   ← 신규 (operator 권한)
│   ├── recipe_manager_routes.py    ← 신규 (manager 권한, 레시피 CUD)
│   ├── stock_routes.py             ← 신규 (재고 조회/쓰기)
│   ├── recipe_import_routes.py     ← 신규 (엑셀 import)
│   └── recipe_stats_routes.py      ← 신규 (통계/CSV export)
└── services/
    └── recipe_helpers.py           ← 신규 (chain·display 헬퍼)

static/js/
├── common.js                       ← 진입 래퍼만 (~50줄)
├── common/                         ← 신규 폴더
│   ├── core.js                     ← request, csrf, detail
│   ├── mappers.js                  ← mapUser, mapRecipe, ...
│   ├── api-users.js                ← login/listUsers/...
│   ├── api-recipes.js              ← getRecipes/import/...
│   ├── api-stock.js                ← getMaterials/restock/...
│   ├── api-spreadsheet.js          ← ssListProducts/...
│   ├── api-chat.js                 ← listChatRooms/...
│   ├── format.js                   ← formatDateTime/statusLabel/...
│   ├── ui.js                       ← notify/showLoading/btnLoading
│   ├── audio.js                    ← playChatSound/speakText
│   └── polling.js                  ← pollNegativeStock
├── management.js                   ← 탭 라우팅만 (~50줄)
├── management/
│   ├── spreadsheet.js
│   ├── history.js
│   ├── chat.js
│   ├── lookup.js
│   └── modals.js
├── work.js                         ← 부트스트랩만 (~50줄)
└── work/
    ├── weighing.js
    ├── chat.js
    └── table.js
```

---

## 9. Convention Prerequisites

### 9.1 Existing Project Conventions

- [x] `CLAUDE.md`(글로벌) 코딩 컨벤션 — Context7 우선, 한국어 UI
- [x] 메모리 시스템 (`feedback_korean_ui.md`, `feedback_common_form_css.md` 등)
- [ ] `CONVENTIONS.md` 미존재 (불필요)
- [x] PDCA 컨벤션 (`docs/01-plan/`, `02-design/`, `03-analysis/`, `04-report/`, `archive/`)
- [x] 마이그레이션 컨벤션 (`docs/migrations.md`, 2026-05-12 추가)

### 9.2 Conventions to Define for This Feature

| Category | Current State | To Define | Priority |
|---|---|---|:---:|
| **JS 모듈 명명** | 카멜케이스 단일 파일 | `<scope>/<domain>.js` (스네이크 또는 케밥) | High |
| **`window.IRMS` 확장 패턴** | 통째 할당 | `window.IRMS = window.IRMS \|\| {}; window.IRMS.api = window.IRMS.api \|\| {}; window.IRMS.api.recipes = {...}` | High |
| **라우터 등록 순서** | 단일 `include_router` | `src/routers/api.py`에 분리 라우터 5개 등록 순서 명시 (operator → manager → import → stats) | Medium |
| **Helper 추출 위치** | 라우터 내부 정의 | `src/services/recipe_helpers.py` | Medium |

---

## 10. Implementation Order

```
Phase 1 (P1) → Phase 2 (P2) → Phase 3 (P3) → Phase 4 (P4)
   ↓             ↓               ↓              ↓
 1~2일          2~3일           1~2일          1일
 (Python)       (JS core)       (관리자 JS)    (작업자 JS)
```

각 Phase 완료 후:
1. 해당 도메인 수동 스모크 (체크리스트 별도)
2. PR 생성 + 머지
3. 다음 Phase 시작

총 예상 기간: **5~8 작업일** (현장 우선 작업 사이 끼워서 진행 가능)

---

## 11. Next Steps

1. [ ] 본 Plan 검토·승인
2. [ ] `/pdca design split-large-files` — Phase 1(Python) 상세 설계
3. [ ] Phase 1 구현 → 분석 → 보고
4. [ ] Phase 2~4 반복

---

## Version History

| Version | Date | Changes | Author |
|---|---|---|---|
| 0.1 | 2026-05-12 | Initial draft | ykh00046 |
