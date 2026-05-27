import json
import unittest
import datetime as dt
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from pydantic import ValidationError

from src.routers.models import ChatMessageCreateRequest
import tray_client.src.main as tray_main
import tray_client.src.config as tray_config
from tray_client.src.attendance_alerts import AttendanceAlertPoller
from tray_client.src.config import Config
from tray_client.src.attendance_popup import (
    PopupPayload,
    build_live_popup_payload,
)


class NoticeMessageValidationTests(unittest.TestCase):
    def test_notice_messages_have_a_shorter_tts_safe_limit(self) -> None:
        with self.assertRaises(ValidationError):
            ChatMessageCreateRequest(room_key="notice", message_text="x" * 301)

    def test_workflow_messages_keep_existing_limit(self) -> None:
        request = ChatMessageCreateRequest(
            room_key="mass_response",
            message_text="x" * 1000,
        )

        self.assertEqual(len(request.message_text), 1000)


class AttendanceAlertPollerTests(unittest.TestCase):
    def test_default_attendance_poll_interval_is_one_hour(self) -> None:
        poller = AttendanceAlertPoller(
            config=Config(),
            present_alert=lambda _payload: None,
            is_enabled_getter=lambda: True,
        )

        self.assertEqual(poller._interval, 60 * 60)

    def test_schedule_slot_keys_follow_9_13_16(self) -> None:
        poller = AttendanceAlertPoller(
            config=Config(),
            present_alert=lambda _payload: None,
            is_enabled_getter=lambda: True,
        )

        self.assertIsNone(poller._current_schedule_slot_key(dt.datetime(2026, 4, 26, 8, 59)))
        self.assertEqual(
            poller._current_schedule_slot_key(dt.datetime(2026, 4, 26, 9, 0)),
            "2026-04-26T09",
        )
        self.assertEqual(
            poller._current_schedule_slot_key(dt.datetime(2026, 4, 26, 14, 30)),
            "2026-04-26T13",
        )
        self.assertEqual(
            poller._current_schedule_slot_key(dt.datetime(2026, 4, 26, 16, 5)),
            "2026-04-26T16",
        )

    def test_duplicate_signature_is_suppressed_within_same_slot_only(self) -> None:
        presented: list[PopupPayload] = []
        poller = AttendanceAlertPoller(
            config=Config(),
            present_alert=presented.append,
            is_enabled_getter=lambda: True,
        )
        payload = {
            "month": "2026-04",
            "total": 1,
            "items": [
                {
                    "emp_id": "171013",
                    "name": "\uAE40\uBBFC\uD638",
                    "department": "\uC0DD\uC0B01\uD300",
                    "shift_time": "\uC8FC\uAC04",
                    "issues": ["\uCD9C\uADFC \uB204\uB77D"],
                }
            ],
        }

        with patch.object(poller, "_poll_once", return_value=payload):
            poller._poll_and_notify(slot_key="2026-04-26T09")
            poller._poll_and_notify(slot_key="2026-04-26T09")
            poller._poll_and_notify(slot_key="2026-04-26T13")

        self.assertEqual(len(presented), 2)

    def test_stale_slot_on_startup_marks_recent_slot_as_processed(self) -> None:
        poller = AttendanceAlertPoller(
            config=Config(),
            present_alert=lambda _payload: None,
            is_enabled_getter=lambda: True,
        )

        # 09:35 → 09 슬롯 시작 후 35분 경과 → 스킬 대상
        self.assertEqual(
            poller._stale_slot_key_on_startup(dt.datetime(2026, 4, 26, 9, 35)),
            "2026-04-26T09",
        )
        # 09:25 → 그레이스 내 → 팔린다
        self.assertIsNone(
            poller._stale_slot_key_on_startup(dt.datetime(2026, 4, 26, 9, 25))
        )
        # 08:00 → 안 띄는 시간
        self.assertIsNone(
            poller._stale_slot_key_on_startup(dt.datetime(2026, 4, 26, 8, 0))
        )

    def test_disabled_state_does_not_consume_slot(self) -> None:
        presented: list[PopupPayload] = []
        enabled_flag = {"value": False}
        poller = AttendanceAlertPoller(
            config=Config(),
            present_alert=presented.append,
            is_enabled_getter=lambda: enabled_flag["value"],
        )
        payload = {
            "month": "2026-04",
            "total": 1,
            "items": [
                {
                    "emp_id": "171013",
                    "name": "김민호",
                    "department": "생산1팀",
                    "shift_time": "주간",
                    "issues": ["출근 누락"],
                }
            ],
        }

        with patch.object(poller, "_poll_once", return_value=payload):
            # 비활성 상태: 폴링되지 않고 슬롯도 소비하지 않음
            self.assertIsNone(poller._last_processed_slot)
            self.assertEqual(len(presented), 0)

            # 사용자가 다시 켜면 같은 슬롯에서도 팝업 발생
            enabled_flag["value"] = True
            poller._poll_and_notify(slot_key="2026-04-26T09")

        self.assertEqual(len(presented), 1)

    def test_manual_check_uses_live_popup_payload(self) -> None:
        presented: list[PopupPayload] = []
        poller = AttendanceAlertPoller(
            config=Config(),
            present_alert=presented.append,
            is_enabled_getter=lambda: True,
        )

        with patch.object(
            poller,
            "_poll_once",
            return_value={
                "month": "2026-05",
                "total": 1,
                "items": [
                    {
                        "emp_id": "260445",
                        "name": "박종휘",
                        "department": "원료생산팀",
                        "details": [
                            {
                                "display_date": "05-06",
                                "code": "1",
                                "content": "출/퇴근 미타각",
                            }
                        ],
                    }
                ],
            },
        ):
            poller._poll_and_notify(force=True)

        self.assertEqual(len(presented), 1)
        self.assertEqual(presented[0].title, "근태 확인 필요")
        self.assertEqual(presented[0].badge_text, "1건")
        self.assertEqual(presented[0].table_rows[0]["emp_id"], "260445")

    def test_live_popup_payload_builds_table_rows_and_remaining_count(self) -> None:
        payload = build_live_popup_payload(
            {
                "total": 5,
                "items": [
                    {
                        "emp_id": "240910",
                        "name": "박효빈",
                        "department": "원료생산팀",
                        "details": [
                            {
                                "display_date": "05-04",
                                "code": "1",
                                "content": "출/퇴근 미타각",
                                "extra_content": "출근 누락 / 퇴근 누락",
                            }
                        ],
                    },
                    {
                        "emp_id": "101001",
                        "name": "최기자",
                        "department": "원료생산팀",
                        "details": [
                            {
                                "display_date": "05-05",
                                "code": "0",
                                "content": "기타",
                                "extra_content": "근무 시간 확인 필요",
                            }
                        ],
                    },
                    {"name": "박종휘", "details": [{"display_date": "05-06"}]},
                    {"name": "김성근", "details": [{"display_date": "05-10"}]},
                    {"name": "이시현", "details": [{"display_date": "05-11"}]},
                    {"name": "김세민", "details": [{"display_date": "05-12"}]},
                    {"name": "정윤규", "details": [{"display_date": "05-13"}]},
                    {"name": "한가람", "details": [{"display_date": "05-14"}]},
                    {"name": "최선미", "details": [{"display_date": "05-15"}]},
                ],
            }
        )

        self.assertEqual(payload.title, "근태 확인 필요")
        self.assertEqual(payload.badge_text, "9건")
        self.assertEqual(payload.summary, "이번 달 시급직 근태 특이사항을 확인해주세요.")
        self.assertEqual(len(payload.table_rows), 8)
        self.assertEqual(payload.lines, ["외 1건 추가"])
        self.assertEqual(payload.table_rows[0]["emp_id"], "240910")
        self.assertEqual(payload.table_rows[0]["date"], "05-04")
        self.assertEqual(payload.table_rows[0]["code"], "1")
        self.assertEqual(payload.table_rows[1]["content"], "기타")

    def test_live_popup_payload_uses_privacy_safe_copy(self) -> None:
        presented: list[PopupPayload] = []
        poller = AttendanceAlertPoller(
            config=Config(),
            present_alert=presented.append,
            is_enabled_getter=lambda: True,
        )

        with patch.object(
            poller,
            "_poll_once",
            return_value={
                "date": "2026-04-24",
                "day_type": "평일2",
                "total": 1,
                "items": [
                    {
                        "emp_id": "171013",
                        "name": "김철수",
                        "department": "생산1팀",
                        "shift_time": "주간",
                        "issues": ["지각 미처리", "조퇴 미처리"],
                    }
                ],
            },
        ):
            poller._poll_and_notify()

        self.assertEqual(len(presented), 1)
        self.assertEqual(presented[0].title, "근태 확인 필요")
        self.assertEqual(presented[0].badge_text, "1건")
        self.assertEqual(presented[0].summary, "이번 달 시급직 근태 특이사항을 확인해주세요.")
        self.assertEqual(presented[0].table_rows[0]["emp_id"], "171013")
        self.assertIn("지각 미처리", presented[0].table_rows[0]["content"])


class TrayAttendanceNavigationTests(unittest.TestCase):
    def test_attendance_page_url_uses_server_root(self) -> None:
        self.assertEqual(
            tray_main.attendance_page_url("http://192.168.11.147:9000/"),
            "http://192.168.11.147:9000/attendance",
        )

    def test_attendance_anomaly_menu_triggers_real_poll(self) -> None:
        events: list[str] = []

        class FakeAlertPoller:
            def trigger_once(self) -> None:
                events.append("poll")

        app = tray_main.TrayApp.__new__(tray_main.TrayApp)
        app.config = Config(server_url="http://192.168.11.147:9000/")
        app.alert_poller = FakeAlertPoller()
        app.logger = SimpleNamespace(
            info=lambda *args, **kwargs: None,
            warning=lambda *args, **kwargs: None,
        )

        with patch(
            "tray_client.src.main.open_in_browser",
            side_effect=lambda url: events.append(url),
        ):
            app._show_attendance_anomalies(None, None)

        self.assertEqual(events, ["poll"])

    def test_today_mute_reenables_after_midnight(self) -> None:
        app = tray_main.TrayApp.__new__(tray_main.TrayApp)
        app._alert_mute_date = "2026-05-27"

        with patch("tray_client.src.main.today_iso", return_value="2026-05-27"):
            self.assertFalse(app._alerts_enabled_today())

        with patch("tray_client.src.main.today_iso", return_value="2026-05-28"):
            self.assertTrue(app._alerts_enabled_today())

    def test_open_attendance_uses_browser(self) -> None:
        opened: list[str] = []

        app = tray_main.TrayApp.__new__(tray_main.TrayApp)
        app.config = Config(server_url="http://192.168.11.147:9000/")
        app.logger = SimpleNamespace(
            info=lambda *args, **kwargs: None,
            warning=lambda *args, **kwargs: None,
        )

        with patch(
            "tray_client.src.main.open_in_browser",
            side_effect=lambda url: opened.append(url),
        ):
            app._open_attendance_menu(None, None)

        self.assertEqual(opened, ["http://192.168.11.147:9000/attendance"])


if __name__ == "__main__":
    unittest.main()
