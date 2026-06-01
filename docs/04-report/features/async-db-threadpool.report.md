# 완료 보고서: async-db-threadpool

- **Feature**: `async-db-threadpool`
- **완료일**: 2026-06-01
- **레벨**: Dynamic
- **최종 매칭률**: 98%
- **PDCA**: Plan → Design → Do → Check → (Iterate 불필요) → Report ✅

## 1. 개요

거의 모든 라우트 핸들러가 `async def`로 선언돼 있으면서 본문에서 **동기 `sqlite3`**
를 직접 호출해, DB 쿼리 동안 **단일 이벤트 루프 스레드가 블로킹**되던 구조적
리스크를 해소했다. 핸들러를 `def`로 전환하여 Starlette가 동기 라우트를
**anyio threadpool worker**에서 실행하도록 했고, 동시 요청이 서로를 직렬화하지
않게 됐다.

## 2. 변경 내용

| 항목 | 결과 |
|---|---|
| 전환 파일 | 라우터 16개 (`pages.py`, `*_routes.py`, `api.py`) |
| 전환 방식 | 핸들러 시그니처 `async def` → `def` (본문 불변) |
| diff 규모 | +96 / -96 (한 줄 단위 시그니처 치환) |
| 라우터 잔존 async def | **0** |
| 라우터 잔존 await | **0** (recipe_stats 상호 호출 안티패턴 제거 포함) |
| 보존된 async | 미들웨어 `dispatch` 2개, main lifespan (계약상 필수) |

## 3. 핵심 지표 (검증됨)

```
전체 테스트 : 138 passed, 회귀 0 (7.9s)
앱 기동      : create_app() → 101 routes, IMPORT OK
라우터 async : 0 / await : 0
```

## 4. 설계 대비 개선점

- **업로드 핸들러까지 완전 전환**: Design은 `UploadFile`+`await file.read()` 때문에
  업로드 핸들러 async 유지를 예외로 뒀으나, 실제 구현은 JSON body(`ImportRequest`)
  기반이라 `await`가 없어 안전하게 `def` 전환됨. 라우터에 `UploadFile` 사용처 없음.
  → 보수적 예외가 불필요했고 더 완전한 전환 달성.
- **recipe_stats 상호 호출 정리**: `stats_export`가 `await stats_consumption(...)`로
  라우트 핸들러를 직접 호출하던 안티패턴을 동기 함수 호출로 정리.

## 5. 효과

- DB 쿼리가 무거운 화면(통계/대시보드/엑셀 집계)이 다른 사용자의 요청을 막지 않음.
- 공용 PC 다수 + 외부 Cloudflare 터널 동시 접속 환경에서 **요청 직렬화 병목 제거**.
- SQLite는 이미 WAL + busy_timeout 설정이라 threadpool 병렬 읽기와 정합.

## 6. 한계 / 후속 권장 (Plan §8 차기 후보)

1. **브라우저 스모크 미실행**: 본 사이클은 코드 레벨 검증(임포트 + 138 passed)으로
   대체. 운영 반영 전 Playwright+격리DB 스모크 1회 권장.
2. **DB 세션 의존성 주입**: `get_connection()` 63곳을 `Depends(get_db)`로 일원화(별도 PDCA).
3. **파일 핸들러 DB 분리**: 향후 실제 `UploadFile` 도입 시 본문 내 DB 작업은
   `run_in_threadpool` 또는 services 분리 고려.
4. **main.py health() async**: DB 무관 단순 핸들러라 잔존 허용. 통일하려면 def 전환 가능.

## 7. 학습 (Learnings)

- `async def` + 동기 I/O는 **비동기의 이점 없이 함정만 안는** 최악 조합. 실 `await`가
  거의 없는 코드베이스에서는 `def` 전환이 `run_in_threadpool` 래핑보다 단순·안전·관용적.
- 시그니처 한 줄 변경이라 git diff가 +N/-N 대칭으로 깔끔하고 롤백도 파일 단위로 즉시 가능.
- 설계의 보수적 예외(업로드 async 유지)는 실제 코드 확인으로 검증할 것 — 가정이
  실제와 다를 수 있다(여기선 더 유리한 방향).

## 8. PDCA 요약

| Phase | 결과 |
|---|---|
| Plan | 블로킹 원인 분석 + def 전환 방식 결정 |
| Design | 대상 식별(16파일) + 보존 대상(미들웨어/lifespan) 명시 |
| Do | 라우터 16파일 async→def 전환 |
| Check | async 0/await 0 확인, 138 passed, 앱 기동 검증 (Match 98%) |
| Iterate | 불필요 (Match ≥ 90%) |
| Report | 본 문서 |

🎉 **Feature 완료** — 최종 매칭률 98%, 전체 138 passed, 라우터 블로킹 핸들러 0
