import datetime as dt
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import requests

import tray_client.src.main as tray_main
from tray_client.src.attendance_alerts import AttendanceAlertPoller
from tray_client.src.attendance_popup import (
    FEEDBACK_ALERTED,
    FEEDBACK_EMPTY,
    FEEDBACK_FAILED,
    AttendanceAlertPopupManager,
    PopupPayload,
    build_live_popup_payload,
    build_viscosity_popup_payload,
)
from tray_client.src.config import Config
from tray_client.src.rescale_alerts import RescaleAlertPoller
from tray_client.src.viscosity_alerts import ViscosityAlertPoller, reminder_signature
from tray_client.src.attendance_popup import _detail_content as _detail_content_for_test


class AttendanceAlertPollerTests(unittest.TestCase):
    def test_default_attendance_poll_interval_is_one_hour(self) -> None:
        poller = AttendanceAlertPoller(
            config=Config(),
            present_alert=lambda _payload: None,
            is_enabled_getter=lambda: True,
        )

        self.assertEqual(poller._interval, 60 * 60)

    def test_schedule_slot_keys_follow_9_13_16(self) -> None:
        import tray_client.src.schedule as _sched
        _sched.set_alert_hours((9, 13, 16))  # 이 테스트는 주간 3슬롯 구성 기준
        self.addCleanup(_sched.set_alert_hours, _sched.SCHEDULED_ALERT_HOURS)
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
        import tray_client.src.schedule as _sched
        _sched.set_alert_hours((9, 13, 16))  # 이 테스트는 주간 3슬롯 구성 기준
        self.addCleanup(_sched.set_alert_hours, _sched.SCHEDULED_ALERT_HOURS)
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
        # 구분 번호·추가 내용 칸은 사라지고(2026-08-19), 원문 사유는 내용에 합쳐진다.
        self.assertNotIn("code", payload.table_rows[0])
        self.assertNotIn("extra_content", payload.table_rows[0])
        self.assertEqual(
            payload.table_rows[0]["content"], "출퇴근 미처리 - 출근 누락 / 퇴근 누락"
        )
        # 실제 서버 제목('미타각')이면 누락 사유는 같은 말이라 접힌다.
        self.assertEqual(
            _detail_content_for_test(
                {"content": "출/퇴근 미타각", "extra_content": "출근 누락 / 퇴근 누락"}
            ),
            "출/퇴근 미타각",
        )

    def test_detail_content_merges_only_when_reason_adds_information(self) -> None:
        _detail_content = _detail_content_for_test

        # 같은 말이면 한 번만
        self.assertEqual(
            _detail_content({"content": "출근 미타각", "extra_content": "출근 미타각"}),
            "출근 미타각",
        )
        # 서버가 숨긴 경우(빈 추가 내용)
        self.assertEqual(
            _detail_content({"content": "근태 이상", "extra_content": ""}), "근태 이상"
        )
        # 제목에 이미 포함된 사유는 반복하지 않는다
        self.assertEqual(
            _detail_content(
                {"content": "근태코드 누락(조퇴 미처리)", "extra_content": "조퇴 미처리"}
            ),
            "근태코드 누락(조퇴 미처리)",
        )
        # 새 정보가 있을 때만 덧붙인다
        self.assertEqual(
            _detail_content(
                {"content": "근태코드 누락(반차/조퇴 예상)", "extra_content": "조퇴 미처리"}
            ),
            "근태코드 누락(반차/조퇴 예상) - 조퇴 미처리",
        )

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
        )
        poller._session = FakeSession()

        result = poller._poll_once("2026-07-01")

        self.assertEqual(result, {"date": "2026-07-01", "total": 0, "items": []})
        self.assertEqual(captured["params"], {"target_date": "2026-07-01"})
        self.assertNotIn("codes", captured["params"])

    def test_viscosity_payload_points_to_viscosity_action(self) -> None:
        """구서버 호환 — pending_lots 키가 없는 응답도 종전 문구로 팝업이 나온다."""
        payload = build_viscosity_popup_payload(
            {
                "total": 1,
                "items": [{"code": "PB", "name": "PB"}],
            }
        )

        self.assertEqual(payload.title, "점도 미측정 LOT")
        self.assertEqual(payload.badge_text, "1건")   # 구서버는 품목당 1건 폴백
        self.assertEqual(payload.lines, ["PB 점도를 입력하세요."])
        self.assertEqual(payload.action_key, "viscosity")
        self.assertEqual(payload.confirm_text, "점도 등록")

    def test_viscosity_popup_lists_pending_lots_and_badge_counts_lots(self) -> None:
        """LOT 단위 알림(2026-08-19) — 줄에 미측정 LOT 번호, 뱃지에 LOT 총수."""
        payload = build_viscosity_popup_payload(
            {
                "total": 4,
                "items": [
                    {
                        "code": "PB",
                        "name": "PB",
                        "pending_count": 2,
                        "pending_lots": [
                            {"blend_record_id": 1, "product_lot": "26061801",
                             "work_date": "2026-06-18", "reactor": 2},
                            {"blend_record_id": 2, "product_lot": "26061901",
                             "work_date": "2026-06-19", "reactor": None},
                        ],
                    },
                    {
                        "code": "SBCT",
                        "name": "SBCT",
                        "pending_count": 4,   # 서버는 pending_lots 를 10건까지만 실음
                        "pending_lots": [
                            {"blend_record_id": 3, "product_lot": "26061701",
                             "work_date": "2026-06-17", "reactor": 1},
                            {"blend_record_id": 4, "product_lot": "26061802",
                             "work_date": "2026-06-18", "reactor": 1},
                            {"blend_record_id": 5, "product_lot": "26061903",
                             "work_date": "2026-06-19", "reactor": None},
                        ],
                    },
                    {
                        "code": "SCRA",
                        "name": "SCRA",
                        "pending_count": 1,
                        "pending_lots": [
                            {"blend_record_id": 6, "product_lot": "26061904",
                             "work_date": "2026-06-19", "reactor": None},
                        ],
                    },
                    {
                        "code": "ZINC",
                        "name": "ZINC",
                        "pending_count": 1,
                        "pending_lots": [
                            {"blend_record_id": 7, "product_lot": "26061905",
                             "work_date": "2026-06-19", "reactor": None},
                        ],
                    },
                ],
            }
        )

        self.assertEqual(payload.title, "점도 미측정 LOT")
        self.assertEqual(
            payload.summary,
            "측정하지 않은 배합 LOT 이 있습니다. 측정 후 등록하거나 측정 불가로 기록하세요.",
        )
        # 뱃지는 품목 수(4)가 아니라 미측정 LOT 총수(2+4+1+1).
        self.assertEqual(payload.badge_text, "8건")
        self.assertEqual(
            payload.lines,
            [
                "PB 미측정 2건: 26061801, 26061901",
                "SBCT 미측정 4건: 26061701, 26061802, 26061903 외 1건",
                "SCRA 미측정 1건: 26061904",
                "+1개 품목 추가",   # 4번째 품목(ZINC)은 POPUP_MAX_NAMES 초과
            ],
        )

    def test_viscosity_signature_changes_when_new_lot_appears(self) -> None:
        """서명은 품목 코드 + 미측정 LOT — 새 LOT 이 생기면 다르고, 같은 LOT 이면 같다."""
        lots_one = [
            {
                "code": "PB",
                "pending_lots": [{"product_lot": "26061801"}],
            }
        ]
        lots_two = [
            {
                "code": "PB",
                "pending_lots": [{"product_lot": "26061801"}, {"product_lot": "26061902"}],
            }
        ]
        lots_two_reversed = [
            {
                "code": "PB",
                "pending_lots": [{"product_lot": "26061902"}, {"product_lot": "26061801"}],
            }
        ]

        self.assertNotEqual(reminder_signature(lots_one), reminder_signature(lots_two))
        self.assertEqual(reminder_signature(lots_two), reminder_signature(lots_two_reversed))
        # 구서버(키 없음)는 코드만으로 서명 — LOT 있는 서명과는 다르다.
        self.assertEqual(reminder_signature([{"code": "PB"}]), "PB")
        self.assertNotEqual(
            reminder_signature([{"code": "PB"}]), reminder_signature(lots_one)
        )

    def test_viscosity_new_lot_in_same_slot_is_not_suppressed(self) -> None:
        """같은 품목·같은 슬롯이라도 새 미측정 LOT 이 생기면 다시 알린다."""
        presented: list[PopupPayload] = []
        poller = ViscosityAlertPoller(
            config=Config(),
            present_alert=presented.append,
            is_enabled_getter=lambda: True,
        )
        one_lot_payload = {
            "date": "2026-07-01",
            "total": 1,
            "items": [
                {
                    "code": "PB",
                    "name": "PB",
                    "pending_count": 1,
                    "pending_lots": [
                        {"blend_record_id": 1, "product_lot": "26061801",
                         "work_date": "2026-06-18", "reactor": None},
                    ],
                }
            ],
        }
        two_lots_payload = {
            "date": "2026-07-01",
            "total": 1,
            "items": [
                {
                    "code": "PB",
                    "name": "PB",
                    "pending_count": 2,
                    "pending_lots": [
                        {"blend_record_id": 1, "product_lot": "26061801",
                         "work_date": "2026-06-18", "reactor": None},
                        {"blend_record_id": 9, "product_lot": "26061902",
                         "work_date": "2026-06-19", "reactor": None},
                    ],
                }
            ],
        }

        with patch.object(poller, "_poll_once", return_value=one_lot_payload):
            poller._poll_and_notify(slot_key="2026-07-01T09")
        with patch.object(poller, "_poll_once", return_value=two_lots_payload):
            poller._poll_and_notify(slot_key="2026-07-01T09")   # 새 LOT → 재알림

        self.assertEqual(len(presented), 2)
        self.assertIn("26061902", presented[1].lines[0])

    def test_viscosity_duplicate_signature_is_suppressed_in_same_slot(self) -> None:
        presented: list[PopupPayload] = []
        poller = ViscosityAlertPoller(
            config=Config(),
            present_alert=presented.append,
            is_enabled_getter=lambda: True,
        )
        payload = {
            "date": "2026-07-01",
            "total": 1,
            "items": [{"code": "PB", "name": "PB"}],
        }

        with patch.object(poller, "_poll_once", return_value=payload):
            poller._poll_and_notify(slot_key="2026-07-01T09")
            poller._poll_and_notify(slot_key="2026-07-01T09")   # 같은 슬롯·내용 → 억제
            poller._poll_and_notify(slot_key="2026-07-01T13")   # 다음 슬롯 → 다시 표시

        self.assertEqual(len(presented), 2)

    def test_viscosity_uses_configured_slots(self) -> None:
        ViscosityAlertPoller(
            config=Config(), present_alert=lambda _p: None, is_enabled_getter=lambda: True,
        )
        # 근태와 동일한 슬롯 로직(schedule 모듈)을 쓰는지 확인 — 주간 3슬롯 구성 기준.
        import tray_client.src.schedule as sched
        sched.set_alert_hours((9, 13, 16))
        try:
            self.assertIsNone(sched.current_slot_key(dt.datetime(2026, 7, 1, 8, 59)))
            self.assertEqual(sched.current_slot_key(dt.datetime(2026, 7, 1, 9, 0)), "2026-07-01T09")
            self.assertEqual(sched.current_slot_key(dt.datetime(2026, 7, 1, 14, 30)), "2026-07-01T13")
            self.assertEqual(sched.current_slot_key(dt.datetime(2026, 7, 1, 16, 5)), "2026-07-01T16")
            # 재시작 유예: 09:35(35분 경과)면 그 슬롯은 처리된 것으로 → 다시 안 뜸
            self.assertEqual(sched.stale_slot_key_on_startup(dt.datetime(2026, 7, 1, 9, 35)), "2026-07-01T09")
            self.assertIsNone(sched.stale_slot_key_on_startup(dt.datetime(2026, 7, 1, 9, 25)))
        finally:
            sched.set_alert_hours(sched.SCHEDULED_ALERT_HOURS)

    def test_night_slots_default_and_config_normalization(self) -> None:
        """야간 슬롯(21시·01시) 도입(2026-08-14) — 기본값·자정 경계·설정 정규화."""
        import tray_client.src.schedule as sched

        # 기본값에 야간 슬롯 포함, 오름차순.
        self.assertEqual(sched.SCHEDULED_ALERT_HOURS, (1, 9, 13, 16, 21))
        sched.set_alert_hours(sched.SCHEDULED_ALERT_HOURS)
        try:
            # 자정 경계: 00:30 은 아직 첫 슬롯(01시) 전 → 슬롯 없음. 01:10 → T01.
            self.assertIsNone(sched.current_slot_key(dt.datetime(2026, 7, 2, 0, 30)))
            self.assertEqual(sched.current_slot_key(dt.datetime(2026, 7, 2, 1, 10)), "2026-07-02T01")
            # 21시 슬롯. 그리고 22시의 다음 슬롯은 '내일 01시'.
            self.assertEqual(sched.current_slot_key(dt.datetime(2026, 7, 1, 21, 5)), "2026-07-01T21")
            secs = sched.seconds_until_next_slot(dt.datetime(2026, 7, 1, 22, 0))
            self.assertEqual(secs, 3 * 3600)
            # 정규화: 문자·범위 밖·중복은 걸러지고 정렬. 전부 무효면 기본으로.
            self.assertEqual(sched.normalize_hours(["21", 1, 1, 99, "x"]), (1, 21))
            self.assertEqual(sched.normalize_hours([]), sched.SCHEDULED_ALERT_HOURS)
        finally:
            sched.set_alert_hours(sched.SCHEDULED_ALERT_HOURS)

    def test_config_carries_alert_hours_default(self) -> None:
        cfg = Config()
        self.assertEqual(list(cfg.alert_hours), [1, 9, 13, 16, 21])


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

            def trigger_once(self, on_feedback=None) -> None:
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


class ManualCheckFeedbackTests(unittest.TestCase):
    """수동 '바로 확인' 피드백 — 0건과 서버 연결 실패를 구분해 알린다."""

    def test_viscosity_empty_result_reports_empty_feedback(self) -> None:
        presented: list[PopupPayload] = []
        statuses: list[str] = []

        class FakeResponse:
            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict[str, object]:
                return {"date": "2026-08-04", "total": 0, "items": []}

        class FakeSession:
            def get(self, url, params=None, headers=None, timeout=None):
                return FakeResponse()

        poller = ViscosityAlertPoller(
            config=Config(),
            present_alert=presented.append,
            is_enabled_getter=lambda: True,
        )
        poller._session = FakeSession()

        poller._poll_and_notify(force=True, on_feedback=statuses.append)

        self.assertEqual(statuses, [FEEDBACK_EMPTY])
        self.assertEqual(presented, [])

    def test_viscosity_connection_error_reports_failed_feedback(self) -> None:
        statuses: list[str] = []

        class FakeSession:
            def get(self, url, params=None, headers=None, timeout=None):
                raise requests.ConnectionError("no route to host")

        poller = ViscosityAlertPoller(
            config=Config(),
            present_alert=lambda _payload: None,
            is_enabled_getter=lambda: True,
        )
        poller._session = FakeSession()

        completed = poller._poll_and_notify(force=True, on_feedback=statuses.append)

        self.assertFalse(completed)
        self.assertEqual(statuses, [FEEDBACK_FAILED])

    def test_viscosity_unexpected_error_is_swallowed_not_raised(self) -> None:
        """팝업 빌더 등 예기치 못한 예외가 폴러 스레드를 죽이지 않아야 한다."""
        statuses: list[str] = []
        poller = ViscosityAlertPoller(
            config=Config(),
            present_alert=lambda _payload: None,
            is_enabled_getter=lambda: True,
        )

        with patch.object(poller, "_poll_once", side_effect=ValueError("boom")):
            completed = poller._poll_and_notify(force=True, on_feedback=statuses.append)

        self.assertFalse(completed)
        self.assertEqual(statuses, [FEEDBACK_FAILED])

    def test_attendance_reports_alerted_when_popup_raised(self) -> None:
        presented: list[PopupPayload] = []
        statuses: list[str] = []
        poller = AttendanceAlertPoller(
            config=Config(),
            present_alert=presented.append,
            is_enabled_getter=lambda: True,
        )

        with patch.object(
            poller,
            "_poll_once",
            return_value={"total": 1, "items": [{"emp_id": "1", "name": "홍길동"}]},
        ):
            poller._poll_and_notify(force=True, on_feedback=statuses.append)

        self.assertEqual(statuses, [FEEDBACK_ALERTED])
        self.assertEqual(len(presented), 1)

    def test_rescale_zero_count_reports_empty_feedback(self) -> None:
        statuses: list[str] = []
        poller = RescaleAlertPoller(
            config=Config(),
            present_alert=lambda _payload: None,
            is_enabled_getter=lambda: True,
        )

        with patch.object(poller, "_poll_once", return_value={"count": 0, "items": []}):
            poller._poll_and_notify(force=True, on_feedback=statuses.append)

        self.assertEqual(statuses, [FEEDBACK_EMPTY])

    def _feedback_app(self, shown: list[PopupPayload]):
        app = tray_main.TrayApp.__new__(tray_main.TrayApp)
        app.config = Config(server_url="http://192.168.11.194:9000")
        app.logger = SimpleNamespace(
            info=lambda *a, **k: None, warning=lambda *a, **k: None,
        )
        app.alert_popup = SimpleNamespace(show=shown.append)
        return app

    def test_empty_feedback_shows_no_alerts_info_popup(self) -> None:
        shown: list[PopupPayload] = []
        app = self._feedback_app(shown)

        app._manual_check_feedback("점도 알림", FEEDBACK_EMPTY)

        self.assertEqual(len(shown), 1)
        self.assertEqual(shown[0].action_key, "info")
        self.assertIn("현재 알림이 없습니다", shown[0].summary)
        # 팝업에 실제로 그려지는 건 lines 다 — 안내 문구가 첫 줄에 있어야 보인다.
        self.assertEqual(shown[0].lines[0], "현재 알림이 없습니다.")

    def test_failed_feedback_points_at_server_setting(self) -> None:
        shown: list[PopupPayload] = []
        app = self._feedback_app(shown)

        app._manual_check_feedback("근태 알림", FEEDBACK_FAILED)

        self.assertEqual(len(shown), 1)
        self.assertIn("서버에 연결하지 못했습니다", shown[0].summary)
        self.assertIn("http://192.168.11.194:9000", shown[0].lines)

    def test_alerted_feedback_shows_nothing_extra(self) -> None:
        shown: list[PopupPayload] = []
        app = self._feedback_app(shown)

        app._manual_check_feedback("증량 알림", FEEDBACK_ALERTED)

        self.assertEqual(shown, [])

    def test_info_popup_confirm_opens_no_page(self) -> None:
        opened: list[str] = []
        app = tray_main.TrayApp.__new__(tray_main.TrayApp)
        app.config = Config()
        app.logger = SimpleNamespace(
            info=lambda *a, **k: None, warning=lambda *a, **k: None,
        )

        with patch(
            "tray_client.src.main.open_in_browser",
            side_effect=lambda url: opened.append(url),
        ):
            app._open_popup_target(
                tray_main.build_info_popup_payload("안내", "현재 알림이 없습니다.")
            )

        self.assertEqual(opened, [])


class ServerCheckTests(unittest.TestCase):
    """설정 저장 시 서버 주소 확인 — 성공/실패 모두 안내 팝업으로 알린다(저장은 차단 안 함)."""

    def _app(self, shown: list[PopupPayload]):
        app = tray_main.TrayApp.__new__(tray_main.TrayApp)
        app.config = Config(server_url="http://192.168.11.194:9000")
        app.logger = SimpleNamespace(
            info=lambda *a, **k: None, warning=lambda *a, **k: None,
        )
        app.alert_popup = SimpleNamespace(show=shown.append)
        return app

    def test_version_api_url(self) -> None:
        self.assertEqual(
            tray_main.version_api_url("http://192.168.11.194:9000/"),
            "http://192.168.11.194:9000/api/version",
        )

    def test_reachable_server_reports_ok(self) -> None:
        shown: list[PopupPayload] = []
        app = self._app(shown)

        with patch(
            "tray_client.src.main.requests.get",
            return_value=SimpleNamespace(raise_for_status=lambda: None),
        ):
            app._verify_server_connection("http://192.168.11.194:9000")

        self.assertEqual(len(shown), 1)
        self.assertIn("연결 확인", shown[0].title)

    def test_unreachable_server_reports_failure(self) -> None:
        shown: list[PopupPayload] = []
        app = self._app(shown)

        with patch(
            "tray_client.src.main.requests.get",
            side_effect=requests.ConnectionError("refused"),
        ):
            app._verify_server_connection("http://10.0.0.9:9000")

        self.assertEqual(len(shown), 1)
        self.assertIn("응답 없음", shown[0].title)
        self.assertIn("http://10.0.0.9:9000", shown[0].lines)


class CombinedPopupTests(unittest.TestCase):
    """근태·점도가 한 창에 섹션으로 합쳐지는 로직 (Tkinter 없이 — 창 미생성 시 렌더는 no-op)."""

    def _manager(self):
        self.confirmed = []
        self.muted = []
        return AttendanceAlertPopupManager(
            on_confirm=lambda p: self.confirmed.append(p.action_key),
            on_dismiss_today=lambda: self.muted.append(1),
        )

    @staticmethod
    def _payload(kind: str) -> PopupPayload:
        return PopupPayload(title=kind, badge_text="1", summary="", lines=[], action_key=kind)

    def test_two_kinds_merge_into_one_window(self) -> None:
        mgr = self._manager()
        mgr._show_payload(self._payload("attendance"))
        mgr._show_payload(self._payload("viscosity"))
        self.assertEqual(list(mgr._sections), ["attendance", "viscosity"])

    def test_confirm_removes_only_that_section(self) -> None:
        mgr = self._manager()
        att = self._payload("attendance")
        mgr._sections = {"attendance": att, "viscosity": self._payload("viscosity")}
        mgr._confirm_section(att)
        self.assertEqual(self.confirmed, ["attendance"])
        self.assertEqual(list(mgr._sections), ["viscosity"])  # 다른 종류는 남는다

    def test_dismiss_clears_all_sections(self) -> None:
        mgr = self._manager()
        mgr._sections = {"attendance": self._payload("attendance"), "viscosity": self._payload("viscosity")}
        mgr._dismiss()
        self.assertEqual(mgr._sections, {})

    def test_dismiss_today_mutes_and_clears(self) -> None:
        mgr = self._manager()
        mgr._sections = {"viscosity": self._payload("viscosity")}
        mgr._dismiss_today()
        self.assertEqual(self.muted, [1])
        self.assertEqual(mgr._sections, {})


class SingleInstanceTest(unittest.TestCase):
    """중복 실행 방지 — 첫 실행은 True, 뮤텍스 잔존 시 두 번째는 False."""

    def test_first_instance_true_second_false(self) -> None:
        import ctypes
        import sys as _sys
        if _sys.platform != "win32":
            self.assertTrue(tray_main.acquire_single_instance())
            return
        # 이미 같은 이름 뮤텍스를 쥔 '다른 인스턴스'를 흉내내 잔존시킴
        held = ctypes.windll.kernel32.CreateMutexW(None, False, "IRMS-Notice-SingleInstance")
        try:
            self.assertFalse(tray_main.acquire_single_instance())  # 두 번째 → 거부
        finally:
            ctypes.windll.kernel32.CloseHandle(held)
            tray_main._INSTANCE_LOCK = None


if __name__ == "__main__":
    unittest.main()
