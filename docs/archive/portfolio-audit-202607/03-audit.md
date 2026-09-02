# IRMS(BRM) 딥 버그 헌팅 감사 보고서

작성일: 2026-07-12
대상: `C:\X\IRMS` — src\ 핵심 모듈 정독 기반 (blend·auth·db·scale_agent·serve·import·viscosity·dashboard·attendance)
전제: `01-improvement-plan.md`·`02-design.md`에 이미 기록된 이슈(레포 위생, 백업 미검증, 세션 키 동거, `except sqlite3.OperationalError` 스키마 폴백)는 **중복 보고 제외**. 아래는 그 너머의 새로운 발견이다.

---

## 요약

- 총 발견: **12건**
- 심각도 분포: **Critical 1 · High 3 · Medium 5 · Low 3**
- 최우선(DHR 데이터 정합성) 관련: F-1(중복 LOT), F-2(무인증 취소), F-3(preview 부작용 커밋), F-4(개정 체인 분기)

발견은 모두 실제 코드를 재정독하고 반박(falsification)을 시도해 살아남은 것만 기록했다. 앱 실행·DB 수정은 하지 않았다(정적 분석 + 테스트 코드 대조).

---

## 발견 목록

### [Critical] F-1. `product_lot` 채번 경쟁 조건 + UNIQUE 제약 부재 → 중복 제품 LOT

- **위치**: `src/services/blend_service.py:485-501` (`generate_product_lot`) / `src/db/migrations.py:198-245` (blend_records 스키마, `product_lot`에 UNIQUE 없음, `idx_blend_records_lot`는 비유니크)
- **문제**: `generate_product_lot`은 "같은 날 같은 제품의 기존 최대 순번 + 1"을 **SELECT 후 INSERT** 하는 read-then-write 패턴이다. 두 요청이 동시에 들어오면 둘 다 같은 max_seq를 읽고 같은 순번을 만든다. 모든 라우트가 sync `def`(FastAPI 스레드풀)이고 요청마다 별도 `sqlite3.connect`를 열며(`db/connection.py:9`), `blend_create`는 채번→INSERT를 하나의 트랜잭션으로 묶지만 **테이블/행 잠금을 잡지 않는다**. SQLite WAL에서 리더는 라이터를 막지 않으므로 두 트랜잭션이 각자 SELECT max=N을 읽고, 직렬화된 두 INSERT가 모두 `N+1`을 기록한다. `product_lot` 컬럼에 UNIQUE 제약이 없어 DB가 이를 거르지도 못한다.
- **발동 조건**: 같은 제품·같은 작업일에 두 작업자(공용 단말 최대 8명)가 거의 동시에 저장(현장에서 흔한 병렬 계량), 또는 한 작업자의 더블클릭/네트워크 재전송. `create_bulk`는 한 연결 안에서 순차 INSERT라 자기 자신과는 안전하지만, 다른 동시 단건 저장과는 여전히 충돌한다.
- **영향**: 규제성 증빙(DHR)의 제품 LOT이 중복 발번된다. LOT은 배합일지·점도(`viscosity_readings.lot_no` 매칭, `migrations.py:270-281` 백필)·역추적의 1차 키라, 중복 시 두 배치가 한 LOT으로 뒤섞이고 점도-배합 연계가 잘못 물린다. 비가역 데이터 오염.
- **수정 방향**: (1) `blend_records.product_lot`에 `UNIQUE` 인덱스(마이그레이션 끝에 추가) → 최소한 중복을 500으로 드러냄. (2) 저장 트랜잭션을 `BEGIN IMMEDIATE`로 시작하거나, 채번 실패(IntegrityError) 시 재시도 루프. (3) 근본적으로는 채번+INSERT를 원자적 재시도로 감싼다.
- **확신도**: 유력 (코드 경로·잠금 부재는 확실, 실발동은 동시성 타이밍 의존)

---

### [High] F-2. 배합 기록 소프트 취소(`DELETE ?hard=false`)에 인증 검사 전무

- **위치**: `src/routers/blend_routes.py:606-647` (`blend_cancel`)
- **문제**: `hard=True` 분기는 `has_access_level(current_user, "manager")`를 확인하지만, **기본 분기(`hard=False`, 소프트 취소)는 어떤 권한 검사도 없다**. `get_current_user(request, required=False)`로 사용자를 받되 None이어도 그대로 `UPDATE blend_records SET status='canceled'`를 실행한다. 라우터에 `Depends(require_...)`도 없다(PUT·approve와 달리).
- **발동 조건**: 내부망의 누구든(로그인·작업자 세션 불필요) `DELETE /api/blend/records/{id}` 직접 호출. UI(`status.js:24`)는 항상 `hard=1`로 보내지만 API는 열려 있어 우회 가능. CSRF는 있으나 CSRF 토큰은 화면 진입 시 누구에게나 발급된다.
- **영향**: 규제 DHR 기록을 무권한으로 숨길 수 있다. 취소 기록은 `list_blend_records`(`status != 'canceled'`)·`export-all`·대시보드·insight에서 전부 사라진다. **되돌리는(un-cancel) 엔드포인트/UI가 없어** 실수·악의로 취소되면 책임자가 하드 삭제하거나 DB 직접 수정 외엔 복구 불가. 감사 대응 시 "기록이 사라졌다"가 됨.
- **수정 방향**: 소프트 취소에도 최소 권한(작업자 세션 또는 책임자)을 요구하고, 취소 사유를 필수화. 취소 복원(un-cancel) 경로를 책임자 전용으로 추가. `hard`/soft 모두 `require_access_level` 또는 `require_blend_worker`로 게이트.
- **확신도**: 확실 (코드에 권한 분기가 hard에만 존재)

---

### [High] F-3. 레시피 임포트 **미리보기(preview)**가 자동 등록한 자재를 영구 커밋

- **위치**: `src/routers/recipe_import_routes.py:29-33` (`import_preview`) → `src/services/import_parser.py:182` (`_auto_register_material`, `INSERT INTO materials ...`)
- **문제**: `parse_import_text`는 헤더에서 처음 보는 자재명을 만나면 그 자리에서 `materials`에 `INSERT`한다(파싱 부작용). preview 라우트는 `with get_connection() as connection: result = parse_import_text(...)`로 감싸는데, **sqlite3 Connection을 `with`로 쓰면 정상 종료 시 `__exit__`이 commit** 한다. 즉 사용자가 "미리보기"만 눌러도 신규 자재가 `미분류` 분류로 마스터에 영구 등록된다. (실제 import 경로는 파싱 후 에러가 있으면 `raise HTTPException`으로 `with`를 예외 종료시켜 롤백되지만, preview는 항상 정상 종료→커밋.)
- **발동 조건**: 책임자가 오타/실험용 엑셀을 붙여넣고 미리보기 → 등록을 취소해도 `materials`에 쓰레기 자재가 남는다. 반복 미리보기로 마스터 오염 누적.
- **영향**: 자재 마스터에 커밋되지 않은 유령 자재가 쌓임. `resolve_material`·자재 목록·자동완성·ERP 코드 매핑을 오염시키고, 나중에 같은 이름을 정식 등록하려 하면 `name UNIQUE` 충돌. "미리보기는 부작용이 없다"는 사용자 기대와 어긋남.
- **수정 방향**: preview는 자재를 쓰지 않도록 파싱과 자동등록을 분리(preview는 "신규 자재" 경고만 반환, 실제 INSERT는 commit 경로에서만). 또는 preview 라우트에서 `with` 대신 명시적 연결 관리 + `rollback()`. 근본적으로 `_auto_register_material`을 파서에서 분리해 "미등록 자재 목록"을 반환하고 커밋 단계에서만 등록.
- **확신도**: 확실 (`with sqlite3_connection`의 commit 동작 + INSERT 존재 확인)

---

### [High] F-4. 개정 체인 중간 버전이 취소되면 "현재 버전"이 둘로 갈라지고 배합 귀결이 어긋남

- **위치**: 목록 tip 판정 `src/services/blend_service.py:462-463` / `recipe_operator_routes.py:118-122,416-418` vs 배합 귀결 `src/services/blend_service.py:36-53` (`_resolve_latest_revision`)
- **문제**: 두 곳이 "현재 버전"을 **다른 방식**으로 계산한다.
  - 목록(`list_blend_recipes`, `/recipes`)은 `id NOT IN (SELECT revision_of FROM recipes WHERE revision_of IS NOT NULL AND status != 'canceled')` — "취소되지 않은 자식을 가진 부모"만 숨긴다.
  - 배합 상세 귀결(`_resolve_latest_revision`)은 `revision_of = current AND status NOT IN ('canceled','draft')` 자식을 따라 앞으로 걷는다.

  체인 A→B→C에서 **중간 B만 취소**되면: 목록 subquery는 B(취소)를 제외하므로 A는 숨겨지지 않고, C의 부모 B는 활성 조건에 안 걸려 B는 숨겨지지만 A·C가 **동시에 tip으로 노출**된다. 한편 `_resolve_latest_revision(A)`는 A의 활성 자식이 없어(B 취소) A에 머물고, `_resolve_latest_revision(C)`는 C를 반환한다. 같은 제품이 목록에 두 줄, 서로 다른 배합 기준으로 저장된다.
- **발동 조건**: 개정 3세대 이상에서 중간 세대를 취소(오등록 정정 등). 실운영에서 개정·취소는 흔한 흐름.
- **영향**: 같은 반제품의 레시피가 배합 화면에 중복 노출되고, 작업자가 어느 줄을 고르냐에 따라 이론량 산출 기준(A vs C)이 달라진다. DHR에 옛 배합비가 섞여 들어갈 수 있음.
- **수정 방향**: tip 판정 로직을 한 헬퍼로 단일화(`recipe_helpers.find_chain_root`/`fetch_chain` 기반으로 "취소 안 된 최신"을 한 곳에서 계산). `_resolve_latest_revision`도 같은 규칙 재사용. 취소가 체인을 끊지 않도록 "취소 건너뛰고 다음 활성 자식" 탐색.
- **확신도**: 유력 (SQL 조건 대조로 도출, 3세대+중간취소 픽스처로 재현 가능)

---

### [Medium] F-5. DHR 이론량·비율·제품명을 서버가 검증 없이 클라이언트 값 그대로 저장

- **위치**: `src/routers/blend_routes.py:355-414` (`blend_create`) → `blend_service.create_blend_record:586-608`
- **문제**: 저장 시 `theory_amount`·`ratio`·`product_name`·`material_name`을 **요청 본문 그대로** INSERT한다. 허용편차 검사(`weighing_tolerance_violations`)는 `|actual - theory| ≤ 0.05`만 보는데, 그 `theory` 자체가 클라이언트가 보낸 값이다. 서버는 recipe_id로 레시피를 재조회해 비율/이론량을 재계산·대조하지 않는다.
- **발동 조건**: 버그 있는/조작된 클라이언트, 또는 화면이 오래 열린 사이 레시피가 개정되어 프론트 상태와 서버 레시피가 어긋난 상태로 저장.
- **영향**: 규제 DHR의 이론량·비율이 실제 레시피와 불일치해도 저장이 통과한다. 편차 검사가 "이론=실측"만 강제하므로 잘못된 이론값이면 잘못된 실측값도 함께 통과.
- **수정 방향**: 서버가 `recipe_id`로 이론량·비율을 재산출해 본문 값과 대조(허용오차 내 불일치만 허용) 또는 서버 산출값으로 덮어쓰기. anchor 모드도 서버 재계산.
- **확신도**: 유력 (검증 부재는 확실, 악용 난이도로 심각도 조정)

---

### [Medium] F-6. `sheets_backup.push_records`가 매 실행마다 전체 기록을 append → 중복 누적

- **위치**: `src/routers/admin_routes.py:422-440` (`admin_sheets_backup`) → `src/services/sheets_backup.py:106-137` (`push_records`)
- **문제**: 백업 실행 시 `list_blend_records(limit=10000)` 전체를 `ws.append_rows`로 **추가**만 한다. 기존 행 삭제·중복 제거·증분(마지막 백업 이후분만) 로직이 없다. 두 번 실행하면 모든 배합 기록이 시트에 두 번 쌓인다.
- **발동 조건**: 책임자가 "Google Sheets 백업"을 두 번 이상 누름(정기 백업 습관).
- **영향**: 백업 시트가 배합 기록의 정본이라면 중복 행으로 집계·역추적이 오염된다. 규제 증빙 백업의 신뢰성 저하.
- **수정 방향**: 실행 전 워크시트 clear 후 전량 재기록, 또는 `product_lot`+순서 키로 upsert(증분). 최소한 UI/문서에 "누적 추가" 성격 명시.
- **확신도**: 확실 (append_rows만 존재, 멱등화 코드 없음)

---

### [Medium] F-7. 허용편차 ±0.05g 절대값이 배치 크기와 무관하게 강제됨

- **위치**: `src/services/blend_service.py:504-519` (`WEIGHING_TOLERANCE_G = 0.05`, `weighing_tolerance_violations`) + `blend_routes.py:366-375`
- **문제**: 자재별 편차 한계가 배치 총량과 무관한 고정 0.05g다. 4000g 배치의 주자재(수천 g)도 ±0.05g(=0.00125%) 안에 들어야 저장된다. 저울 GX-10202M 해상도(0.01g) 기준이라지만, 대용량 배치에서 실무상 달성 불가능한 편차라 손입력 정정을 원천 차단한다.
- **발동 조건**: 큰 총량 + 실측값이 이론과 0.05g 넘게 차이나는 정상 계량. bulk 생성은 actual=theory라 우회되지만 수동 실측 입력은 막힘.
- **영향**: 정상 계량이 저장 거부되어 작업자가 실측값을 이론값으로 위조 입력하도록 유도(데이터 신뢰성 역효과).
- **수정 방향**: 편차 한계를 배치·자재 비율에 비례(예: max(0.05g, 이론량의 0.1%))하거나 자재별 설정화. 도메인 확인 필요.
- **확신도**: 의심 (도메인 정책일 수 있음 — 코드는 확실, 적정성은 현장 확인 필요)

---

### [Medium] F-8. 배합 실적 수정(PUT) 시 `product_name` 변경돼도 `product_lot`는 옛 제품명 유지

- **위치**: `src/routers/blend_routes.py:416-479` (`blend_update`) → `blend_service.update_blend_record:612-671`
- **문제**: 수정은 `product_name`을 갱신하지만 `product_lot`은 "보존" 정책으로 그대로 둔다(`update_blend_record`가 product_lot을 건드리지 않음). LOT은 `{제품명}{YYMMDD}{순번}` 규칙인데, 제품명을 바꾸면 LOT 접두사와 실제 product_name이 어긋난다.
- **발동 조건**: 책임자가 제품명 오등록을 PUT으로 정정.
- **영향**: LOT 문자열과 제품명 불일치 → LOT 기반 점도 매칭(`viscosity_readings.lot_no`)·역추적·엑셀 출력에서 혼동. 규제 문서 일관성 저하.
- **수정 방향**: 제품명 변경 시 LOT 재발번(감사 로그에 old→new 기록) 또는 PUT에서 product_name 변경을 금지/경고. 정책 명문화.
- **확신도**: 확실 (update_blend_record가 product_lot 미갱신)

---

### [Medium] F-9. 점도 직접 등록 시 `measured_date` 폴백이 이중 계산되어 판정 연도와 저장 연도가 어긋날 수 있음

- **위치**: `src/routers/viscosity_routes.py:94-119` vs `src/services/viscosity_service.py:604-625` (`add_reading`)
- **문제**: 라우트는 `resolved_date`를 `body.measured_date or parse_lot_date(lot_no) or local_today_text()`로 계산해 **판정 연도(`reading_year`)**를 정한다. 그런데 `add_reading`에는 **`measured_date=body.measured_date`(원본)**를 넘기고, 서비스가 내부에서 다시 `measured_date or parse_lot_date(lot_no) or date.today().isoformat()`로 폴백한다. 라우트는 `local_today_text()`(로컬), 서비스는 `date.today()`(로컬)로 지금은 같지만 폴백 소스가 이원화돼 있어, `parse_lot_date`가 라우트/서비스에서 다르게 동작하거나 향후 한쪽만 바뀌면 **판정에 쓴 연도와 실제 저장된 measured_date의 연도가 달라진다**. classify는 `reading_year` 표본으로 하고 저장은 서비스 폴백 날짜로 하므로, 경계(연말/연초, LOT 날짜 파싱 성공/실패 분기)에서 "다른 연도 기준으로 판정 후 다른 연도로 저장"이 발생.
- **발동 조건**: `measured_date` 미지정 + LOT에서 날짜 추론 가능/불가 경계, 연말 자정 부근.
- **영향**: 이상 판정에 쓴 관리한계 표본 연도와 저장 레코드 연도 불일치 → 재분석 시 그 측정의 status가 달라짐(판정 재현성 깨짐).
- **수정 방향**: 라우트에서 계산한 `resolved_date`를 `add_reading(measured_date=resolved_date)`로 넘겨 단일 소스화. 서비스 내부 폴백은 제거하거나 라우트가 항상 확정값을 전달.
- **확신도**: 유력 (이중 폴백 구조는 확실, 실제 어긋남은 파싱 경계 의존)

---

### [Low] F-10. 로그인 엔드포인트 CSRF 예외 → 로그인 CSRF(세션 고정) 여지

- **위치**: `src/main.py:47-56` (CSRF `exempt_urls`에 `management-login`·`attendance/login`·`blend/session/login`)
- **문제**: 로그인 경로가 CSRF 면제라, 공격자가 피해자 브라우저로 하여금 공격자 자격증명으로 로그인시키는 로그인 CSRF가 이론상 가능. 최초 로그인 시 토큰 부재 문제를 피하려는 의도(주석의 sendBeacon 근거는 logout에만 해당).
- **발동 조건**: 내부망에서 피해자가 악성 페이지 방문. 폐쇄망이라 현실 위험은 낮음.
- **영향**: 작업자가 공격자 세션으로 배합/근태를 조회·입력하게 될 수 있음(귀속 오염). 내부망 한정이라 낮음.
- **수정 방향**: 로그인 폼에 GET으로 CSRF 토큰 선발급 후 검증, 또는 SameSite=strict(운영은 이미 strict)로 완화 유지 + 문서화.
- **확신도**: 유력 (예외 목록은 확실, 악용 경로는 폐쇄망 제약)

---

### [Low] F-11. 근태 잠금 카운터에 시간 윈도우가 없어 장기 누적으로 잠금

- **위치**: `src/attendance_auth.py:234-260` (`authenticate`)
- **문제**: 주석은 "counter window 내 5회 실패 시 잠금"이라 하지만, 실제로는 `failed_attempts`가 성공 또는 잠금 전까지 시간 감쇠 없이 계속 누적된다. 며칠에 걸친 산발적 오타 4회 + 오늘 1회 = 잠금.
- **발동 조건**: 비밀번호를 가끔 틀리는 사용자(임시 비번 환경에서 흔함).
- **영향**: 정상 사용자가 의도치 않게 5분 잠금. 경미한 사용성 저하.
- **수정 방향**: 마지막 실패 시각 기준 윈도우(예: 15분) 도입 후 초과 시 카운터 리셋, 또는 주석을 실제 동작에 맞게 정정.
- **확신도**: 확실 (감쇠 로직 부재)

---

### [Low] F-12. `_is_sequential_digits` 랩어라운드 오탐 + 좁은 커버리지

- **위치**: `src/attendance_auth.py:74-79`
- **문제**: 연속 숫자 판정을 `password in "01234567890123456789"`로 하여 (1) `"8901"`, `"9012"` 같은 랩어라운드 문자열을 연속으로 오판(거부), (2) `"13579"`·역순 일부 조합은 통과. substring 방식이라 커버리지가 들쭉날쭉.
- **발동 조건**: 사용자가 우연히 `890123`류(정상적일 수 있는) 숫자 비번 선택 시 거부.
- **영향**: 약한 비번 정책의 오탐/누락. 보안·사용성 모두 경미.
- **수정 방향**: 인접 문자 차분(±1 연속) 검사로 대체(models.py `_check_manager_password`는 이미 diff 방식 — 두 정책 통일 권장).
- **확신도**: 확실 (문자열 substring 로직)

---

## 수색했으나 문제 없었거나 이미 알려진 영역 (감사 범위 증빙)

- **SQL 파라미터화**: 동적 SQL은 모두 플레이스홀더 사용. f-string으로 조립되는 부분(`material_usage_periods`의 `period_expr`, `blend_approve`의 `{col}_by`, IN 절 placeholders)은 모두 **코드 내부 화이트리스트/딕셔너리 키/`Literal`**에서만 값이 오고 사용자 입력이 직접 들어가지 않음 → 인젝션 없음. `generate_product_lot`의 LIKE는 `%`/`_`/`\` 이스케이프 처리됨(`:493-494`).
- **경로 조작**: `signature_samples.delete_sample`은 `os.path.basename`으로 디렉터리 탈출 차단(`:93`). `admin_signature_delete`도 동일 경유.
- **CSV 인젝션**: 점도 CSV export가 `=,+,-,@` 선두 값을 `'`로 이스케이프(`viscosity_routes.py:283-286`). 다만 **blend 엑셀 export(`blend_routes.py`, `dashboard_export`)에는 동일 방어가 없음** — openpyxl은 수식 자동실행이 아니라 위험 낮아 별건 미보고, 필요 시 후속.
- **권한 게이트**: `require_access_level("manager")` 의존성이 recipe write/import/admin/worker-admin/viscosity-mgr/blend PUT·approve·hard-delete에 일관 적용됨(F-2의 soft-cancel만 누락).
- **내부망 미들웨어**: `InternalNetworkOnlyMiddleware`가 `X-Forwarded-For`를 의도적으로 무시(프록시 없음 전제, 주석 명시)하고 `hmac.compare_digest`로 토큰 비교 → 타이밍 안전. 운영은 토큰 필수.
- **비밀번호 해시**: pbkdf2_sha256 200k 반복 + `hmac.compare_digest` 검증(`security.py`) — 적정.
- **세션 토큰 회전**: 로그인/비번변경 시 단일 세션 토큰 회전 구현됨(`auth.py`, `auth_routes.py:94-120`). `_clear_auth_session`이 blend/att 키를 보존(이미 알려진 이슈).
- **점도 추세 룰**(`_trend_alerts`, `_control_limits`): run/shift/σ 계산은 표본 n<2 가드·중심선 폴백 등 경계 처리 정상. 빈 표본에서 std=0 → 관리한계 None 처리로 나눗셈 오류 없음.
- **scale_agent 파서**(`_parse_and/_parse_sics/_parse_cas`): 빈 입력·짧은 프레임·비숫자 가드 있음. `float()` 실패 시 None 반환. EventBus dedupe·스레드 락 정상. 단위 kg/mg→g 환산 일관.
- **dhr_cache**: 마커(record+서명설정 SHA-256) 기반 자동 무효화, OSError 흡수 — 캐시 실패가 본 기능을 막지 않음.
- **날짜/타임존**: 저장 타임스탬프 UTC(`utc_now_text`), '오늘'·측정일 판정은 로컬(`local_today_text`/`date.today`)로 의도적 분리 — 자정 밀림 대체로 방어됨(F-9의 점도 이중 폴백만 예외).
- **record_delete_service**: 하드 삭제 시 `viscosity_readings.blend_record_id` NULL 처리 + `blend_details` CASCADE 정리, 레시피 삭제 시 revision_of NULL 재부모화 — FK 정합성 유지. 단 이 전체가 한 트랜잭션이나 라우터에서 commit하므로 원자성 OK.

---

## 최우선 조치 권고 (요약)

1. **F-1 (Critical)**: `product_lot` UNIQUE 인덱스 + 채번 원자화. DHR 1차 키 중복은 비가역 오염이라 즉시 처리.
2. **F-2 (High)**: 소프트 취소 무인증 구멍 — 규제 기록을 내부망 누구나 화면에서 숨길 수 있고 복구 경로도 없음.
3. **F-3 (High)**: import 미리보기의 자재 자동등록 커밋 — "미리보기=부작용 없음" 기대를 깨고 마스터를 오염시킴.
