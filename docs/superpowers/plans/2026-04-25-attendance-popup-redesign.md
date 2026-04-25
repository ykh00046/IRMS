# Attendance Popup Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tkinter 기반 근태 알림 팝업을 미니멀한 업무용 패널 스타일로 재구성하고, `이름 3명까지 + 외 N명` 규칙과 명확한 버튼 위계를 적용한다.

**Architecture:** 팝업 데이터 구조를 먼저 확장해 헤더/배지/보조 문구/표시 줄을 분리하고, 그 payload를 `attendance_popup.py` UI 계층에서 카드형 레이아웃으로 렌더링한다. 알림 poller와 테스트는 새 payload 규칙을 기준으로 업데이트하되 서버 API나 중복 억제 로직은 유지한다.

**Tech Stack:** Python 3, Tkinter, unittest, PyInstaller one-folder tray app

---

## File Structure

- Modify: `tray_client/src/attendance_popup.py`
  - `PopupPayload` 구조 확장
  - live/test payload 생성 규칙 변경
  - Tkinter 팝업 레이아웃과 버튼 스타일 재구성
- Modify: `tray_client/src/attendance_alerts.py`
  - 새 payload 구조를 그대로 전달하도록 조정
- Modify: `tests/test_notice_tray_behaviour.py`
  - payload 텍스트, 이름 축약, 테스트 알림 문구 회귀 테스트 추가

## Task 1: Redesign Popup Payload Contract

**Files:**
- Modify: `tray_client/src/attendance_popup.py`
- Test: `tests/test_notice_tray_behaviour.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_live_popup_payload_shows_three_names_and_remaining_count(self) -> None:
    payload = build_live_popup_payload(
        {
            "total": 5,
            "items": [
                {"name": "김철수"},
                {"name": "이영희"},
                {"name": "박민수"},
                {"name": "최하늘"},
                {"name": "정다은"},
            ],
        }
    )

    self.assertEqual(payload.title, "근태 확인 필요")
    self.assertEqual(payload.badge_text, "5명")
    self.assertEqual(
        payload.lines,
        [
            "김철수 인원 근태 확인",
            "이영희 인원 근태 확인",
            "박민수 인원 근태 확인",
            "외 2명",
        ],
    )

def test_test_popup_payload_uses_test_badge_and_copy(self) -> None:
    payload = build_test_popup_payload()
    self.assertEqual(payload.badge_text, "TEST")
    self.assertEqual(payload.summary, "팝업 표시와 버튼 동작을 확인하세요.")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m unittest tests.test_notice_tray_behaviour.AttendanceAlertPollerTests -v`
Expected: FAIL because `PopupPayload` does not yet expose `badge_text` and current live/test copy is still old format.

- [ ] **Step 3: Write the minimal implementation**

```python
@dataclass(frozen=True)
class PopupPayload:
    title: str
    badge_text: str
    summary: str
    lines: list[str]
    accent: str = "live"

def build_live_popup_payload(payload: dict[str, Any]) -> PopupPayload:
    items = list(payload.get("items") or [])
    total = int(payload.get("total") or len(items))
    lines = [_privacy_safe_line(item) for item in items[:3]]
    if total > len(lines):
        lines.append(f"외 {total - len(lines)}명")
    if not lines:
        lines.append("근태 확인이 필요한 인원이 있습니다.")
    return PopupPayload(
        title="근태 확인 필요",
        badge_text=f"{total}명" if total else "확인",
        summary="오늘 확인이 필요한 인원이 있습니다.",
        lines=lines,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m unittest tests.test_notice_tray_behaviour.AttendanceAlertPollerTests -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tray_client/src/attendance_popup.py tests/test_notice_tray_behaviour.py
git commit -m "feat: redesign attendance popup payload"
```

## Task 2: Rebuild Tkinter Popup Layout

**Files:**
- Modify: `tray_client/src/attendance_popup.py`
- Test: `tests/test_notice_tray_behaviour.py`

- [ ] **Step 1: Write the failing test**

```python
def test_test_notification_uses_redesigned_popup_payload(self) -> None:
    presented: list[PopupPayload] = []
    poller = AttendanceAlertPoller(
        config=Config(),
        present_alert=presented.append,
        is_enabled_getter=lambda: True,
    )

    poller.show_test_notification()

    self.assertEqual(presented[0].title, "근태 알림 테스트")
    self.assertEqual(presented[0].badge_text, "TEST")
    self.assertEqual(presented[0].lines[0], "홍길동 인원 근태 확인")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m unittest tests.test_notice_tray_behaviour.AttendanceAlertPollerTests.test_test_notification_uses_popup_payload -v`
Expected: FAIL because the test payload still uses the old title/copy structure.

- [ ] **Step 3: Write minimal implementation**

```python
header = tk.Frame(container, bg=panel_bg)
header.pack(fill="x")

status_dot = tk.Canvas(header, width=10, height=10, bg=panel_bg, highlightthickness=0)
status_dot.create_oval(2, 2, 8, 8, fill="#2563eb", outline="")
status_dot.pack(side="left", pady=(1, 0))

title_label = tk.Label(header, textvariable=self._title_var, ...)
title_label.pack(side="left", padx=(8, 0))

badge = tk.Label(header, textvariable=self._badge_var, ...)
badge.pack(side="right")
```

그리고 버튼 영역은 아래처럼 위계를 나눈다.

```python
confirm_btn = tk.Button(buttons, text="확인", bg="#2563eb", fg="#ffffff", ...)
close_btn = tk.Button(buttons, text="닫기", bg="#ffffff", fg="#334155", ...)
mute_btn = tk.Button(buttons, text="오늘은 그만", bg="#ffffff", fg="#64748b", ...)
```

- [ ] **Step 4: Run targeted tests**

Run: `python -m unittest tests.test_notice_tray_behaviour.AttendanceAlertPollerTests -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tray_client/src/attendance_popup.py tests/test_notice_tray_behaviour.py
git commit -m "feat: restyle attendance popup window"
```

## Task 3: Integrate and Verify End-to-End Behavior

**Files:**
- Modify: `tray_client/src/attendance_alerts.py`
- Modify: `tray_client/src/attendance_popup.py`
- Test: `tests/test_notice_tray_behaviour.py`

- [ ] **Step 1: Write the failing regression test**

```python
def test_live_popup_payload_uses_privacy_safe_copy(self) -> None:
    ...
    self.assertEqual(presented[0].title, "근태 확인 필요")
    self.assertEqual(presented[0].badge_text, "1명")
    self.assertEqual(presented[0].summary, "오늘 확인이 필요한 인원이 있습니다.")
    self.assertEqual(presented[0].lines[0], "김철수 인원 근태 확인")
```

- [ ] **Step 2: Run the regression test to verify it fails**

Run: `python -m unittest tests.test_notice_tray_behaviour.AttendanceAlertPollerTests.test_live_popup_payload_uses_privacy_safe_copy -v`
Expected: FAIL until the poller-facing payload contract and assertions are aligned.

- [ ] **Step 3: Implement the minimal integration**

```python
popup_payload = build_live_popup_payload(payload)
self._present_alert(popup_payload)
```

그리고 `attendance_popup.py`에서는 `show()` 호출 시 기존 창 재사용, 본문 줄 최대 3개 + `외 N명` 규칙을 유지한다.

- [ ] **Step 4: Run full verification**

Run: `python -m unittest discover -s tests`
Expected: PASS

Run: `python -m compileall tray_client`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tray_client/src/attendance_alerts.py tray_client/src/attendance_popup.py tests/test_notice_tray_behaviour.py
git commit -m "feat: ship redesigned attendance popup"
```

## Self-Review

- Spec coverage:
  - 미니멀 업무툴형 톤: Task 2
  - 이름 3명 + 외 N명: Task 1
  - 확인/닫기/오늘은 그만 버튼 위계: Task 2
  - 상세 사유 비노출: Task 1, Task 3
  - 테스트 알림과 실알림 동일 레이아웃: Task 2, Task 3
- Placeholder scan:
  - `TBD`, `TODO`, “적절한 처리” 같은 빈 표현 없음
- Type consistency:
  - `PopupPayload.badge_text`를 plan 전체에서 같은 이름으로 사용
  - live/test payload, popup renderer, tests 모두 같은 필드 집합을 기준으로 함

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-04-25-attendance-popup-redesign.md`.

Inline execution is the only path I will use in this session. I will implement this plan task-by-task in the current workspace.
