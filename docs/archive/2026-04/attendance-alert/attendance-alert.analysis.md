# attendance-alert Analysis Report

> Design vs Implementation Gap Analysis — `/pdca analyze` Check phase

| Item | Value |
|------|-------|
| Project | IRMS (원료 계량 시스템) |
| Feature | attendance-alert |
| Plan | [../01-plan/features/attendance-alert.plan.md](../../01-plan/features/attendance-alert.plan.md) |
| Design | [../02-design/features/attendance-alert.design.md](../../02-design/features/attendance-alert.design.md) |
| Commit | `11fa7c3` on main |
| Analyst | gap-detector (Claude) |
| Date | 2026-04-24 |
| Method | 정적 소스 검증 (서버 + 트레이 클라이언트 + 인스톨러) |
| Status | Approved — proceed to report |

---

## Match Rate: **98%**

| Category | Score | Status |
|----------|:-----:|:------:|
| Detection Rules (§3.2) | 100% | PASS |
| API Shape & Error Codes (§3.1) | 100% | PASS |
| Polling Behavior (§4.1) | 95% | PASS (minor variance) |
| Menu / Mute Toggle (§4.2) | 100% | PASS |
| Middleware Protection | 100% | PASS |
| Version Bump (§7) | 100% | PASS |

---

## 1. 분석 개요

attendance-alert 기능은 서버 측에 당일 근태 이상을 감지하는 REST 엔드포인트를 추가하고, 트레이 클라이언트에 30분 주기 폴러와 Windows Toast 알림을 통합한다. 이름 노출 브로드캐스트, 이상 해소 시 자동 중단, 자정 자동 리셋, 하루 단위 음소거 토글이 설계대로 작동함을 정적 검증으로 확인했다.

28개 설계 체크 항목 중 27개 Match, 1개 Partial(수용된 variance), 실제 gap 0건. 후속 cleanup 후보 2건(Info level)만 존재.

---

## 2. 검증 테이블

### 2.1 감지 규칙 (Design §3.2)

| # | 설계 항목 | 구현 위치 | 결과 |
|---|-----------|-----------|:----:|
| 1 | `day_type != '평일'` 건너뜀 | `src/services/attendance_excel.py:203-204` | Match |
| 2 | 출근 누락 → "출근 누락" | `attendance_excel.py:208-209` | Match |
| 3 | 퇴근 누락 → "퇴근 누락" | `attendance_excel.py:210-211` | Match |
| 4 | 지각 + `:g` 포맷 | `attendance_excel.py:212-213` | Match |
| 5 | 조퇴 + `:g` 포맷 | `attendance_excel.py:214-215` | Match |
| 6 | 외출 제외 | OUTING 컬럼 미참조 | Match |
| 7 | 복수 issue 가능 | `attendance_excel.py:207-224` | Match |
| 8 | `current_date()` 헬퍼 | `attendance_excel.py:165-166` | Match |

### 2.2 API (Design §3.1)

| # | 설계 항목 | 구현 위치 | 결과 |
|---|-----------|-----------|:----:|
| 9 | `GET /api/public/attendance-alerts/today` | `public_attendance_alert_routes.py:21-23` + `api.py:19,38` | Match |
| 10 | `date/day_type/total/items` 응답 | `public_attendance_alert_routes.py:38-43` | Match |
| 11 | item `emp_id/name/issues` | `attendance_excel.py:218-223` | Match (+department 추가, variance 수용) |
| 12 | 404 `MONTH_FILE_NOT_FOUND` | `public_attendance_alert_routes.py:31-32` | Match |
| 13 | 503 `FILE_LOCKED_RETRY` | `public_attendance_alert_routes.py:33-34` | Match |
| 14 | 500 `FILE_FORMAT_INVALID` | `public_attendance_alert_routes.py:35-36` | Match |

### 2.3 폴링 동작 (Design §4.1)

| # | 설계 항목 | 구현 위치 | 결과 |
|---|-----------|-----------|:----:|
| 15 | 폴링 주기 30분 | `tray_client/src/attendance_alerts.py:26` (`DEFAULT_INTERVAL_SECONDS = 30*60`) | Match |
| 16 | 이름 3명 + "외 N명" | `attendance_alerts.py:27, 38-41` | Match |
| 17 | 토스트 제목 `근태 이상 N건` | `attendance_alerts.py:33` | Match |
| 18 | 본문 구분자 ` · ` | `attendance_alerts.py:39, 41` | Match |
| 19 | 404/503/네트워크 오류 조용히 스킵 | `attendance_alerts.py:90-95, 123-129` | Match |
| 20 | 시작 시 1주기 대기 | `attendance_alerts.py:80-82` | Partial (capped `min(_interval,60)` — 수용) |

### 2.4 메뉴 / Mute Toggle (Design §4.2)

| # | 설계 항목 | 구현 위치 | 결과 |
|---|-----------|-----------|:----:|
| 21 | `_alert_mute_date: str\|None` | `tray_client/src/main.py:62` | Match |
| 22 | `_alerts_enabled_today()` + 자정 자동 복귀 | `main.py:93-96` + `attendance_alerts.py:134-135` | Match |
| 23 | 메뉴 토글 텍스트 | `main.py:106-112` | Match |
| 24 | `_toggle_alert_mute_today` 동작 | `main.py:143-151` | Match |
| 25 | TrayApp start/stop 통합 | `main.py:77, 89` | Match |

### 2.5 Middleware / 버전

| # | 설계 항목 | 구현 위치 | 결과 |
|---|-----------|-----------|:----:|
| 26 | InternalNetworkOnly prefix에 `/api/public/attendance-alerts` 추가 | `src/main.py:50-56` | Match |
| 27 | installer 버전 1.1.0 | `tray_client/build/installer.iss:9` | Match |
| 28 | README v1.1.0 문서화 | `tray_client/README.md:11, 41-50` | Match |

---

## 3. Gap 목록 (Info 수준, 스코어 영향 없음)

| # | 항목 | Severity | 설명 | 권장 조치 |
|---|------|:--------:|------|-----------|
| G1 | `_last_signature` 미사용 필드 | Info | `attendance_alerts.py:64`에서 선언되고 `:103`에서 None 리셋만 있음. dedup 로직 없음. | 다음 iterate에서 제거 또는 dedup 구현 결정 |
| G2 | 설계 §4.1 예시 `BACKOFF_ON_ERROR` 상수와 본문 지시("백오프 없이 단순") 불일치 | Info | 구현은 본문 지시 따름 | 설계서 예시 블록에서 해당 상수 삭제하여 문서 정합성 확보 |
| G3 | `detect_today_anomalies` docstring 반환 타입 | Info | docstring은 `list[dict]`처럼 보이나 실제 `tuple[str, list[dict]]` 반환 | 구현이 정답(응답에 day_type 필요). docstring 업데이트 권장 |

High/Medium gap 0건. 구현 재작업 불필요.

---

## 4. Accepted Variances (스코어 영향 없음)

| 항목 | 설계 | 구현 | 판정 |
|------|------|------|------|
| API item에 `department` 추가 | 3개 키만 | 4개 키 | 수용 — 추가 정보, 기존 키 불변 |
| 시작 대기 시간 | 1주기 | `min(_interval, 60)` | 수용 — 빠른 테스트 피드백 |
| `trigger_once` + "근태 알림 테스트" 메뉴 | 없음 | 추가 | 수용 — 무해한 진단 도구 |
| `_last_signature` 필드 | 없음 | 선언만 | 수용 — cleanup 후보 |
| 연차/월차/휴가 행 제외 로직 | 미명시 | 추가 | 수용 — false positive 방지 |

---

## 5. 검증 한계

정적 분석이므로 아래 항목은 실제 운영 환경에서 확인 필요:

| 항목 | 이유 | 대체 |
|------|------|------|
| Windows 토스트 실제 팝업 | 본 환경 tray 실행 불가 | 개발자 스모크 테스트 (EXE 기동 OK, 프로세스 alive, formatter unit test green) |
| 30분 주기 실측 | 장시간 구동 필요 | 상수값 정적 확인 |
| 자정 경과 자동 복귀 | 타이밍 재현 불가 | `today_iso()` 비교 로직 정적 확인 |
| 7대 동시 폴링 부하 | 실배포 필요 | 설계상 14 req/h로 부하 문제 없음 |
| 엑셀 락 503 처리 | ERP 갱신 타이밍 | `FileLocked` 예외 경로 정적 확인 |

---

## 6. 종합 권장

**권장 조치: `/pdca report attendance-alert` 진행 (iterate 불필요).**

- Match Rate 98%는 보고 기준(90%)을 크게 상회
- 실질적 구현 gap 없음 (3건 Info 수준 cleanup 후보만 존재)
- docstring/설계 문서 정정 3건은 리포트에 후속 과제로 기록
- 실제 현장 검증은 7대 PC 배포 후 18:05 이후에 수행

---

## 7. 변경 이력

| 버전 | 날짜 | 작성자 | 비고 |
|:----:|------|--------|------|
| 0.1 | 2026-04-24 | gap-detector (Claude) | 초기 분석. Match Rate 98%. High/Medium gap 없음. |
