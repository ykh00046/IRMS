# Plan — async-db-threadpool (동기 SQLite 이벤트 루프 블로킹 해소)

> R3 코드 품질/아키텍처 개선 1순위 · 2026-06-01 · Level: Dynamic

## 1. 배경 / 문제

현황 분석에서 식별된 아키텍처 최우선 리스크.

- 라우터 핸들러가 **`async def` 95개**로 선언되어 있다.
- 그러나 DB 접근은 동기 `sqlite3`(`src/db/connection.py`, `check_same_thread=False`)를
  핸들러 본문에서 **직접 블로킹 호출**한다 (`with get_connection() as conn:` 63곳/13파일).
- 실제로 `await`를 사용하는 핸들러는 **단 1곳**뿐:
  `recipe_stats_routes.py`의 `stats_consumption_csv` → `await stats_consumption(...)`
  (라우트 핸들러를 직접 호출하는 안티패턴).
- 즉 거의 모든 핸들러가 **비동기 이벤트 루프 위에서 동기 I/O를 수행** → 단일 이벤트
  루프 스레드가 DB 쿼리 동안 블로킹된다. 동시 접속(공용 PC 다수 + 외부 터널)이
  늘면 한 사용자의 무거운 쿼리(통계/대시보드/엑셀 집계)가 **전체 요청을 직렬화**시킨다.

### 근본 원인

FastAPI는 핸들러가 `def`(동기)면 **외부 threadpool(anyio worker)** 에서 실행해 루프를
보호하지만, `async def`면 **루프 스레드에서 직접** 실행한다. 현재 코드는 비동기 이점은
전혀 쓰지 않으면서(거의 await 없음) 비동기의 함정(블로킹)만 안고 있다.

## 2. 목표 (Goals)

1. async 핸들러 내 동기 SQLite 호출로 인한 **이벤트 루프 블로킹 제거**.
2. 동시 요청이 서로를 직렬화하지 않고 threadpool에서 병렬 처리되도록 한다.
3. 기존 동작/응답 100% 보존 (회귀 0). 71개 테스트 전부 통과 유지.

## 3. 비목표 (Non-Goals)

- `aiosqlite` 도입 / DB 레이어 비동기 재작성 (과도한 변경, 별도 PDCA).
- DB 세션 의존성 주입(`Depends(get_db)`) 리팩토링 (별도 후보, 본 사이클 범위 밖).
- 쿼리 성능 튜닝/인덱스 추가.
- 라우터 비즈니스 로직의 services 추출.

## 4. 접근 방식 결정 (run_in_threadpool vs def 전환)

| 기준 | `run_in_threadpool` 래핑 | **`async def`→`def` 전환** (채택) |
|------|--------------------------|-----------------------------------|
| 변경 범위 | DB 호출부마다 래퍼 삽입 (침습적) | 핸들러 시그니처 `async ` 제거 (표면적) |
| 가독성 | 콜백/람다 증가 | 본문 그대로, 더 단순 |
| 블로킹 해소 | 호출 단위 | 핸들러 전체 자동 위임 |
| await 의존 | 유지 가능 | await 거의 없어 손실 없음 |
| FastAPI 관용성 | 보조 수단 | **표준 권장 패턴** |

**결정: `async def` → `def` 전환.** 이 코드베이스는 실질 `await`가 1곳뿐이라
def 전환이 가장 단순·안전·관용적이다. Starlette가 동기 라우트를 자동으로 threadpool에서
실행하므로 목표가 그대로 달성된다.

**예외 처리**: `stats_consumption_csv`의 `await stats_consumption(...)` 상호 호출은
두 핸들러를 모두 `def`로 바꾸고 `await`를 제거한다(동기 함수 직접 호출).

## 5. 작업 범위 (대상 파일)

라우터 13개 파일의 DB 접근 핸들러:

- `dashboard_routes.py`, `recipe_operator_routes.py`, `recipe_manager_routes.py`,
  `recipe_stats_routes.py`, `recipe_import_routes.py`, `spreadsheet_routes.py`,
  `weighing_routes.py`, `stock_routes.py`, `attendance_routes.py`, `chat_routes.py`,
  `admin_routes.py`, `auth_routes.py`, `forecast_routes.py`
- `pages.py` (HTML 페이지 라우트도 DB 조회 시 동일 적용)

## 6. 리스크 / 완화

| 리스크 | 완화 |
|--------|------|
| `await` 호출부 누락 시 런타임 에러 | grep으로 `await` 전수 확인(현재 1곳) 후 동시 전환 |
| 백그라운드 task/이벤트 핸들러의 async 의존 | 라우트 핸들러만 대상, lifespan/startup은 제외 |
| SQLite 동시 쓰기 충돌(threadpool 병렬↑) | 이미 `busy_timeout=5000`+`timeout=30` 설정됨, WAL 여부 점검 |
| 의존성(`Depends`)이 async인 경우 | `require_access_level` 등은 동기 — 영향 없음 확인 |

## 7. 완료 기준 (Definition of Done)

- DB를 접근하는 라우트 핸들러 중 불필요한 `async`가 제거됨.
- 잔존 `await`는 정당한 비동기 호출만 남음(또는 0).
- `pytest` 71/71 통과 (회귀 0).
- 브라우저 스모크: 대시보드/통계/계량 등 주요 화면 정상 + 콘솔 오류 0.
- gap-detector Match Rate ≥ 90%.

## 8. 차기 후보 (Out of Scope)

- DB 세션 의존성 주입(`Depends(get_db)`)으로 `get_connection()` 63곳 일원화.
- SQLite WAL 모드 적용 검토(동시성 추가 개선).
- 프런트 공통 `IRMS.request`(CSRF 자동) 래퍼.
