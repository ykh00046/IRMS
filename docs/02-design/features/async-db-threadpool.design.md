# Design — async-db-threadpool

> Plan: docs/01-plan/features/async-db-threadpool.plan.md · 2026-06-01

## 1. 설계 개요

라우트 핸들러의 불필요한 `async def`를 `def`로 전환하여, Starlette가 동기 라우트를
**외부 threadpool(anyio worker thread)** 에서 실행하도록 한다. 이로써 동기 `sqlite3`
호출이 이벤트 루프를 블로킹하지 않는다.

### 동작 원리 (FastAPI/Starlette)

| 핸들러 선언 | 실행 위치 | 블로킹 I/O 영향 |
|-------------|-----------|-----------------|
| `async def` | 이벤트 루프 스레드 | **루프 전체 블로킹** (현재 문제) |
| `def` | anyio threadpool worker | 워커 스레드만 점유, 루프 자유 |

동기 라우트는 기본 40개(anyio 기본) 워커풀에서 병렬 실행되므로, 한 쿼리가 다른 요청을
막지 않는다. SQLite는 이미 **WAL 모드**(`src/db/schema.py:12`)라 다중 읽기 동시성이
보장되고, `busy_timeout=5000`으로 쓰기 충돌도 대기 처리된다.

## 2. 대상 식별

### 전환 대상 (async → def)

라우트 핸들러로서 DB/순수 동기 작업만 하는 함수. 파일별 async def 수:

| 파일 | async def | 비고 |
|------|:---------:|------|
| pages.py | 21 | HTML 페이지 |
| spreadsheet_routes.py | 10 | 업로드 2건은 예외(아래) |
| recipe_operator_routes.py | 9 | |
| attendance_routes.py | 9 | |
| dashboard_routes.py | 7 | |
| admin_routes.py | 6 | |
| stock_routes.py | 6 | |
| auth_routes.py | 5 | |
| weighing_routes.py | 5 | |
| chat_routes.py | 4 | |
| forecast_routes.py | 3 | |
| recipe_manager_routes.py | 3 | |
| recipe_stats_routes.py | 2 | 상호 호출 쌍(아래) |
| recipe_import_routes.py | 2 | 업로드 1건 예외(아래) |
| public_attendance_alert_routes.py | 2 | |
| api.py | 1 | health |

### 유지 대상 (async 그대로)

1. **파일 업로드 핸들러** — `UploadFile`을 `await file.read()`로 비동기 수신해야 함:
   - `recipe_import_routes.py:32` `upload_recipes`
   - `spreadsheet_routes.py:51/52` `upload_spreadsheet`, `import_spreadsheet`
   → 이들은 `async def` 유지. 단, **DB 작업 부분이 길면 본문 내 블로킹은 남음**.
     본 사이클에서는 파일 핸들러의 async 유지를 우선(정확성), DB 분리는 차기 후보.
2. **미들웨어 dispatch** (`security_headers.py`, `internal_only.py`) — `BaseHTTPMiddleware`
   계약상 `async def dispatch` 필수. **변경 금지**.
3. **lifespan/startup** (`main.py`) — async 필수. **변경 금지**.

### 특수 처리: recipe_stats 상호 호출

`stats_export`(async)가 `await stats_consumption(...)`(async)를 직접 호출(라우트 핸들러를
함수처럼 호출하는 안티패턴). 두 함수를 모두 `def`로 바꾸고 `await`를 제거한다:

```python
# before
async def stats_consumption(...): ...
async def stats_export(...):
    response = await stats_consumption(date_from, date_to, color_group, category)
# after
def stats_consumption(...): ...
def stats_export(...):
    response = stats_consumption(date_from, date_to, color_group, category)
```

## 3. 구현 방법

라우트 데코레이터 직후의 핸들러 정의에서 `async def` → `def`로 치환한다.
함수 본문은 변경하지 않는다(await가 없으므로 그대로 동기 실행).

검증 절차:
1. 전환 후 `grep "await "`로 잔존 await가 **정당한 케이스만**(업로드 핸들러) 남았는지 확인.
2. 전환된 핸들러에 `async`를 전제로 한 코드(`await`, `asyncio.*`)가 없는지 재확인.

## 4. 영향 범위 / 호환성

- **응답·동작 불변**: 동기 함수로 바뀌어도 반환값·예외·상태코드 동일.
- **의존성**: `Depends(require_access_level(...))` 등은 동기 호출 가능 객체 → 영향 없음.
- **테스트**: `TestClient`(동기)는 sync/async 라우트 모두 지원 → 기존 71개 그대로 통과 예상.

## 5. 검증 전략

| 단계 | 방법 | 통과 기준 |
|------|------|-----------|
| 단위/회귀 | `pytest tests -q` | 71/71 통과, 회귀 0 |
| 정적 확인 | grep `async def` (라우터) / `await` | 업로드·미들웨어·lifespan 외 async 0 |
| 브라우저 스모크 | 격리 DB + Playwright | 대시보드/통계/계량/재고 정상, 콘솔 0 |
| Gap 분석 | gap-detector | Match ≥ 90% |

## 6. 롤백

각 파일 단위 git diff로 즉시 되돌릴 수 있음(시그니처 한 줄 변경). 위험 낮음.
