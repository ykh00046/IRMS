# Attendance Alert Design

> 당일 근태 이상을 30분 주기로 트레이 팝업에 표시하는 기능 상세 설계서

## 1. Overview

| Item | Detail |
|------|--------|
| Feature | attendance-alert |
| Plan | `docs/01-plan/features/attendance-alert.plan.md` |
| Components | Server (감지 + 엔드포인트) + Tray client (30분 폴러 + Windows Toast) |

## 2. Architecture

```
┌─────────────────────────────────────────────────┐
│ Server (192.168.11.147:9000)                    │
│                                                  │
│   attendance_excel.py ─ detect_today_anomalies  │
│                ▲                                 │
│   public_attendance_alert_routes.py              │
│     └ GET /api/public/attendance-alerts/today   │
│                                                  │
│   InternalNetworkOnlyMiddleware                  │
│     └ 보호 prefix 추가: /api/public/attendance-alerts │
└─────────────────────────────────────────────────┘
              ▲
              │ HTTP GET (30분 간격)
              │
┌─────────────┴───────────────────────────────────┐
│ Tray Client                                      │
│                                                  │
│   main.py ─ AttendanceAlertPoller (신규)        │
│     └ threading, backoff, day-scoped mute flag   │
│                                                  │
│   pystray.Icon.notify(message, title)           │
│     └ Windows 토스트                             │
│                                                  │
│   Menu: "오늘 근태 알림 끄기" (자정 자동 해제)    │
└─────────────────────────────────────────────────┘
```

## 3. Server API

### 3.1 GET /api/public/attendance-alerts/today

**Request:** 없음
**Response 200:**
```json
{
  "date": "2026-04-23",
  "day_type": "평일",
  "total": 2,
  "items": [
    {
      "emp_id": "171013",
      "name": "김민호",
      "issues": ["지각 0.25시간"]
    },
    {
      "emp_id": "240518",
      "name": "전명옥",
      "issues": ["퇴근 누락"]
    }
  ]
}
```

**Response (휴일/주말 또는 파일 없음):**
```json
{
  "date": "2026-04-26",
  "day_type": "토요일",
  "total": 0,
  "items": []
}
```

**동작 규칙:**
- 서버 로컬 날짜의 `monthly_attendance_YYYY-MM.xlsx` 열어서 오늘 일자 행 집계
- `day_type != '평일'` → `items=[], total=0`
- 파일 없음 → 404 `MONTH_FILE_NOT_FOUND` (폴러는 조용히 스킵)
- 파일 락 → 503 `FILE_LOCKED_RETRY` (폴러는 조용히 스킵)

**Auth:** 없음 (InternalNetworkOnlyMiddleware 경유)
**Rate limit:** 불필요 (30분 × 7대 = 14req/h)

### 3.2 감지 함수 스펙

**위치:** `src/services/attendance_excel.py` 에 추가

```python
def detect_today_anomalies(
    year_month: str, target_date: str
) -> list[dict[str, Any]]:
    """Return list of anomalies for target_date in the given month file.

    Detection rules (평일 only):
    - check_in missing → '출근 누락'
    - check_out missing → '퇴근 누락'
    - late_hours > 0 → '지각 {hours}시간'
    - early_leave_hours > 0 → '조퇴 {hours}시간'

    Returns [] for non-weekday rows. Raises MonthFileNotFound /
    FileLocked on I/O issues.
    """
```

**포맷 세부:**
- 지각/조퇴 시간은 `:g` 포매터로 불필요한 0 제거 (`0.25`, `1.5`, `2`)
- 출근·퇴근·지각·조퇴가 함께 있으면 한 사람당 복수 issue 반환 가능

## 4. Tray Client

### 4.1 신규 모듈: `tray_client/src/attendance_alerts.py`

```python
class AttendanceAlertPoller:
    """Polls /attendance-alerts/today on an interval and raises
    toast notifications when anomalies exist.

    Silent (no sound), visual-only. Auto-clears by simply trusting
    the server response on the next cycle — if the late worker now
    has a check-in, the name drops out of the list; at midnight the
    date rolls over and yesterday's overdue items disappear.
    """

    INTERVAL_SECONDS = 30 * 60   # 30 minutes
    BACKOFF_ON_ERROR = 60 * 60   # 1h after repeated failure

    def __init__(self, config, icon_provider, is_enabled_getter):
        ...

    def start(self): ...
    def stop(self): ...
```

**내부 동작:**
1. `stop_event.wait(INTERVAL_SECONDS)` 루프
2. `is_enabled_getter()` 가 False 면 스킵 (사용자가 "오늘 알림 끄기" 선택)
3. GET 요청 → 200이고 items 있으면 `_emit_notification()` 호출
4. 네트워크 오류 → 로그, 다음 주기 재시도 (백오프 없이 단순)

### 4.2 main.py 통합

기존 `TrayApp.run()` 에 다음 추가:

```python
self.alert_poller = AttendanceAlertPoller(
    config=self.config,
    icon_provider=lambda: self._icon,
    is_enabled_getter=lambda: self._alerts_enabled_today(),
)
self.alert_poller.start()
```

**하루 단위 음소거 플래그:**
```python
self._alert_mute_date: str | None = None  # ISO date; 이 날짜 == 오늘이면 음소거
```

`_alerts_enabled_today()` 구현:
```python
def _alerts_enabled_today(self) -> bool:
    today = datetime.date.today().isoformat()
    return self._alert_mute_date != today
```

**트레이 메뉴 항목 추가:**
```python
MenuItem(
    lambda _item: ("오늘 근태 알림 끄기" if self._alerts_enabled_today()
                   else "오늘 근태 알림 켜기 (자정 자동 복귀)"),
    self._toggle_alert_mute_today,
)
```

`_toggle_alert_mute_today(self, _icon, _item)`:
- 오늘 꺼져있으면 → mute_date를 None으로 (즉시 활성)
- 오늘 켜져있으면 → mute_date를 오늘로 설정 (자정에 자동 복귀)
- `_icon.update_menu()` 호출

### 4.3 토스트 메시지 포맷

```python
def _format_notification(items: list[dict]) -> tuple[str, str]:
    total = len(items)
    title = f"근태 이상 {total}건"
    shown = items[:3]
    tail = ""
    if total > 3:
        tail = f" 외 {total - 3}명"
    names = " · ".join(i["name"] for i in shown) + tail
    return title, names
```

**예시:**
- 1건: `title="근태 이상 1건", body="김민호"`
- 3건: `title="근태 이상 3건", body="전명옥 · 김민호 · 박효빈"`
- 5건: `title="근태 이상 5건", body="전명옥 · 김민호 · 박효빈 외 2명"`

### 4.4 알림 표시

`pystray.Icon.notify(message, title)` 호출.
- `message` = body
- `title` = title
- Windows 10/11에서 시스템 트레이 토스트로 나타남 (4~5초 자동 닫힘, 알림 센터에 누적)

## 5. Request Flow Example

```
14:00  ERP가 아직 오늘 데이터 없음
14:00  트레이 폴링 → 404 MONTH_FILE_NOT_FOUND (또는 오늘 행 없음)
       → 조용히 스킵, 아무 동작 없음
...
18:02  ERP가 엑셀 갱신 완료
18:30  트레이 폴링 → 200, items=[
          {name: "김민호", issues: ["지각 0.25시간"]},
          {name: "전명옥", issues: ["퇴근 누락"]}
        ]
       → Toast: "근태 이상 2건 / 김민호 · 전명옥"
19:00  동일 폴링 → 전명옥이 뒤늦게 퇴근 기록 → items=[
          {name: "김민호", issues: ["지각 0.25시간"]}
        ]
       → Toast: "근태 이상 1건 / 김민호"
...
00:30  다음 날: 오늘 = 2026-04-24. 새 날짜에 아직 데이터 없음
       → items=[], 아무 토스트 없음
```

## 6. Error Handling

| 상황 | 처리 |
|------|------|
| 네트워크 오류 | 로그 경고만, 다음 주기에 재시도 |
| 404 MONTH_FILE_NOT_FOUND | 조용히 스킵 |
| 503 FILE_LOCKED_RETRY | 조용히 스킵 |
| 200 items=[] | 아무 동작 없음 |
| 알림 표시 중 `pystray.Icon.notify` 실패 | 로그만, 다음 주기 계속 |
| 서버 404 (엔드포인트 자체 없음) | 로그 경고, 다음 주기 계속 (구버전 서버 호환) |

## 7. 버전 관리

**트레이 앱:** 1.0.0 → **1.1.0**
- `installer.iss` `MyAppVersion` 업데이트
- 설치 파일명 `IRMS-Notice-Setup-1.1.0.exe`
- AppId 동일하므로 기존 설치 위에 덮어쓰기

**호환성:**
- 서버가 새 엔드포인트를 제공해야 알림 작동 — 서버 먼저 배포 권장
- 서버가 구버전이면 트레이는 404를 받고 조용히 스킵 (에러 스팸 없음)

## 8. Test Plan (수동)

**서버 단위:**
1. 오늘 = 평일, 출근 누락 1명 → `/today` 응답에 그 이름만 포함
2. 오늘 = 토요일 → `items=[], day_type=토요일`
3. 오늘 파일 없음 → 404
4. 지각 0.25 + 조퇴 0.5 가 동시 → issues 2개

**트레이 단위:**
1. 알림 켜진 상태 + 서버 응답 2건 → 토스트 표시
2. "오늘 알림 끄기" → 토스트 안 뜸
3. 자정 경과 후 자동 복귀 (시뮬레이션: `_alert_mute_date`를 어제로 수동 설정 후 확인)
4. 네트워크 끊김 → 로그에 경고, 토스트는 안 뜸

**End-to-End:**
1. 실제 서버 배포 후 7대 PC에서 18:30 이후 팝업 확인
2. 누락자가 뒤늦게 출근 기록 → 다음 주기에 사라짐

## 9. 구현 순서

1. `src/services/attendance_excel.py` — `detect_today_anomalies` 추가
2. `src/routers/public_attendance_alert_routes.py` 신규 또는 `public_notice_routes.py` 확장 — 엔드포인트
3. `src/middleware/internal_only.py` 또는 `main.py` — `/api/public/attendance-alerts` prefix 추가
4. `src/routers/api.py` — 라우터 등록
5. 서버 스모크 테스트
6. `tray_client/src/attendance_alerts.py` 신규
7. `tray_client/src/main.py` — 통합, 메뉴 추가, mute 로직
8. `tray_client/build/installer.iss` — 버전 1.1.0
9. 빌드 (PyInstaller + Inno Setup)
10. 커밋 분리 (서버 / 클라이언트 / 문서) + 푸시
11. `/pdca analyze` → `/pdca report` → `/pdca archive`

## 10. Non-Goals

- 개인별 필터링 (각 PC 사번 등록)
- 알림 클릭 → 브라우저 열기
- 이상 사유 상세 (시간값) 토스트 본문 포함
- 관리자 웹 대시보드 카드
- 감사 로그
- 외부 메신저 연동
