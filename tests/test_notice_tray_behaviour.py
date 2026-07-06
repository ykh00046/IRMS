import datetime as dt
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import tray_client.src.main as tray_main
from tray_client.src.attendance_alerts import AttendanceAlertPoller
from tray_client.src.attendance_popup import (
    PopupPayload,
    build_live_popup_payload,
    build_viscosity_popup_payload,
)
from tray_client.src.config import Config
from tray_client.src.viscosity_alerts import ViscosityAlertPoller


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
                    "name": "김민호",
                    "department": "생산1팀",
                    "shift_time": "주간",
                    "issues": ["출근 누락"],
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

        self.assertEqual(
            poller._stale_slot_key_on_startup(dt.datetime(2026, 4, 26, 9, 35)),
            "2026-04-26T09",
        )
        self.assertIsNone(
            poller._stale_slot_key_on_startup(dt.datetime(2026, 4, 26, 9, 25))
        )
        self.assertIsNone(
            poller._stale_slot_key_on_startup(dt.datetime(2026, 4, 26, 8, 0))
        )

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
                        "name": "박종원",
                        "department": "원료생산팀",
                        "details": [
                            {
                                "display_date": "05-06",
                                "code": "1",
                                "content": "출퇴근 미처리",
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
        self.assertEqual(presented[0].confirm_text, "근태 확인")
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
                                "content": "출퇴근 미처리",
                                "extra_content": "출근 누락 / 퇴근 누락",
                            }
                        ],
                    },
                    {"name": "박종원", "details": [{"display_date": "05-06"}]},
                    {"name": "김태근", "details": [{"display_date": "05-10"}]},
                    {"name": "이시훈", "details": [{"display_date": "05-11"}]},
                    {"name": "김현민", "details": [{"display_date": "05-12"}]},
                    {"name": "정윤근", "details": [{"display_date": "05-13"}]},
                    {"name": "서강호", "details": [{"display_date": "05-14"}]},
                    {"name": "최선미", "details": [{"display_date": "05-15"}]},
                    {"name": "장도훈", "details": [{"display_date": "05-16"}]},
                ],
            }
        )

        self.assertEqual(payload.title, "근태 확인 필요")
        self.assertEqual(payload.badge_text, "9건")
        self.assertEqual(payload.summary, "이번 달 미처리 근태 특이사항을 확인해주세요.")
        self.assertEqual(len(payload.table_rows), 8)
        self.assertEqual(payload.lines, ["+1건 추가"])
        self.assertEqual(payload.table_rows[0]["emp_id"], "240910")
        self.assertEqual(payload.table_rows[0]["date"], "05-04")
        self.assertEqual(payload.table_rows[0]["code"], "1")

    def test_live_popup_payload_uses_privacy_safe_copy_without_table(self) -> None:
        payload = build_live_popup_payload(
            {
                "total": 1,
                "items": [
                    {
                        "emp_id": "171013",
                        "name": "김철수",
                        "department": "생산1팀",
                        "issues": ["지각 미처리", "조퇴 미처리"],
                    }
                ],
            }
        )

        self.assertEqual(payload.table_rows[0]["emp_id"], "171013")
        self.assertIn("지각 미처리", payload.table_rows[0]["content"])


class ViscosityAlertPollerTests(unittest.TestCase):
    def test_poll_once_requests_server_without_product_codes(self) -> None:
        # 알림 대상은 서버(remind_daily)가 정한다 — 트레이는 codes 를 보내지 않는다.
        captured: dict[str, object] = {}

        class FakeResponse:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict[str, object]:
                return {"date": "2026-07-01", "total": 0, "items": []}

        class FakeSession:
            def get(self, url, params=None, headers=None, timeout=None):
                captured["url"] = url
                captured["params"] = params
                return FakeResponse()

        poller = ViscosityAlertPoller(
            config=Config(server_url="http://192.168.11.147:9000"),
            present_alert=lambda _payload: None,
            is_enabled_getter=lambda: True,
            today_provider=lambda: "2026-07-01",
        )
        poller._session = FakeSession()

        result = poller._poll_once("2026-07-01")

        self.assertEqual(result, {"date": "2026-07-01", "total": 0, "items": []})
        self.assertEqual(captured["params"], {"target_date": "2026-07-01"})
        self.assertNotIn("codes", captured["params"])

    def test_viscosity_payload_points_to_viscosity_action(self) -> None:
        payload = build_viscosity_popup_payload(
            {
                "total": 1,
                "items": [{"code": "PB", "name": "PB"}],
            }
        )

        self.assertEqual(payload.title, "점도 입력 필요")
        self.assertEqual(payload.badge_text, "1개")
        self.assertEqual(payload.lines, ["PB 점도를 입력하세요."])
        self.assertEqual(payload.action_key, "viscosity")
        self.assertEqual(payload.confirm_text, "점도 등록")

    def test_viscosity_duplicate_signature_is_suppressed_per_day(self) -> None:
        presented: list[PopupPayload] = []
        poller = ViscosityAlertPoller(
            config=Config(),
            present_alert=presented.append,
            is_enabled_getter=lambda: True,
            today_provider=lambda: "2026-07-01",
        )
        payload = {
            "date": "2026-07-01",
            "total": 1,
            "items": [{"code": "PB", "name": "PB"}],
        }

        with patch.object(poller, "_poll_once", return_value=payload):
            poller._poll_and_notify()
            poller._poll_and_notify()
            poller._poll_and_notify(force=True)

        self.assertEqual(len(presented), 2)


class TrayNavigationTests(unittest.TestCase):
    def test_page_urls_use_server_root(self) -> None:
        self.assertEqual(
            tray_main.attendance_page_url("http://192.168.11.147:9000/"),
            "http://192.168.11.147:9000/attendance",
        )
        self.assertEqual(
            tray_main.blend_page_url("http://192.168.11.147:9000/"),
            "http://192.168.11.147:9000/blend",
        )
        self.assertEqual(
            tray_main.viscosity_page_url("http://192.168.11.147:9000/"),
            "http://192.168.11.147:9000/viscosity",
        )
        self.assertEqual(
            tray_main.home_page_url("http://192.168.11.147:9000/"),
            "http://192.168.11.147:9000/",
        )

    def test_attendance_and_viscosity_menu_trigger_pollers(self) -> None:
        events: list[str] = []

        class FakePoller:
            def __init__(self, name: str) -> None:
                self._name = name

            def trigger_once(self) -> None:
                events.append(self._name)

        app = tray_main.TrayApp.__new__(tray_main.TrayApp)
        app.alert_poller = FakePoller("attendance")
        app.viscosity_poller = FakePoller("viscosity")
        app.logger = SimpleNamespace(info=lambda *args, **kwargs: None)

        app._show_attendance_anomalies(None, None)
        app._show_viscosity_reminders(None, None)

        self.assertEqual(events, ["attendance", "viscosity"])

    def test_popup_action_routes_to_expected_page(self) -> None:
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
            app._open_popup_target(
                PopupPayload(
                    title="점도 입력 필요",
                    badge_text="1개",
                    summary="",
                    lines=[],
                    action_key="viscosity",
                )
            )
            app._open_popup_target(
                PopupPayload(
                    title="근태 확인 필요",
                    badge_text="1건",
                    summary="",
                    lines=[],
                    action_key="attendance",
                )
            )

        self.assertEqual(
            opened,
            [
                "http://192.168.11.147:9000/viscosity",
                "http://192.168.11.147:9000/attendance",
            ],
        )

    def test_today_mute_reenables_after_midnight(self) -> None:
        app = tray_main.TrayApp.__new__(tray_main.TrayApp)
        app._alert_mute_date = "2026-05-27"

        with patch("tray_client.src.main.today_iso", return_value="2026-05-27"):
            self.assertFalse(app._alerts_enabled_today())

        with patch("tray_client.src.main.today_iso", return_value="2026-05-28"):
            self.assertTrue(app._alerts_enabled_today())

    def test_attendance_and_viscosity_gate_independently(self) -> None:
        app = tray_main.TrayApp.__new__(tray_main.TrayApp)
        app._alert_mute_date = None
        app.config = Config(attendance_alerts_enabled=True, viscosity_alerts_enabled=False)

        with patch("tray_client.src.main.today_iso", return_value="2026-05-27"):
            self.assertTrue(app._attendance_active())     # 근태만 켜짐
            self.assertFalse(app._viscosity_active())      # 점도는 꺼짐
            self.assertTrue(app._any_alert_enabled())

    def test_today_mute_suppresses_both_alert_types(self) -> None:
        app = tray_main.TrayApp.__new__(tray_main.TrayApp)
        app.config = Config(attendance_alerts_enabled=True, viscosity_alerts_enabled=True)
        app._alert_mute_date = "2026-05-27"

        with patch("tray_client.src.main.today_iso", return_value="2026-05-27"):
            self.assertFalse(app._attendance_active())
            self.assertFalse(app._viscosity_active())


if __name__ == "__main__":
    unittest.main()
