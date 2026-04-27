import queue
import json
import time
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
from tray_client.src.poller import Poller
from tray_client.src.attendance_popup import (
    PopupPayload,
    build_live_popup_payload,
)
import tray_client.src.tts as tts_module
from tray_client.src.tts import TTSQueue


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


class TTSQueueTests(unittest.TestCase):
    def test_queue_drops_oldest_message_when_full(self) -> None:
        tts = TTSQueue(chime_path=Path("missing.wav"), max_queue_size=2)

        tts.enqueue_message({"id": 1, "message_text": "one"})
        tts.enqueue_message({"id": 2, "message_text": "two"})
        tts.enqueue_message({"id": 3, "message_text": "three"})

        queued_ids = []
        while True:
            try:
                queued_ids.append(tts._queue.get_nowait()["id"])
            except queue.Empty:
                break

        self.assertEqual(queued_ids, [2, 3])

    def test_worker_sets_volume_and_waits_after_chime_before_speaking(self) -> None:
        events: list[tuple] = []

        class FakeEngine:
            def getProperty(self, _name):
                return []

            def setProperty(self, name, value):
                events.append(("set", name, value))

            def say(self, text):
                events.append(("say", text))

            def runAndWait(self):
                events.append(("run",))

        class FakeWinsound:
            SND_FILENAME = 1
            SND_ASYNC = 2

            @staticmethod
            def PlaySound(path, flags):
                events.append(("chime", path, flags))

        fake_engine = FakeEngine()
        with (
            patch("tray_client.src.tts.pyttsx3.init", return_value=fake_engine),
            patch("tray_client.src.tts.winsound", FakeWinsound),
            patch.object(
                tts_module,
                "time",
                SimpleNamespace(sleep=lambda seconds: events.append(("sleep", seconds))),
                create=True,
            ),
            patch.object(Path, "exists", return_value=True),
        ):
            tts = TTSQueue(chime_path=Path("ding.wav"), volume=0.4)
            tts.start()
            tts.enqueue_message({"message_text": "hello"})

            deadline = time.time() + 2
            while ("run",) not in events and time.time() < deadline:
                time.sleep(0.01)
            tts.stop()

        self.assertIn(("set", "volume", 0.4), events)
        chime_index = next(i for i, event in enumerate(events) if event[0] == "chime")
        sleep_index = next(i for i, event in enumerate(events) if event[0] == "sleep")
        say_index = next(i for i, event in enumerate(events) if event[0] == "say")
        self.assertLess(chime_index, sleep_index)
        self.assertLess(sleep_index, say_index)
        self.assertEqual(events[sleep_index][1], 0.2)


class PollerTests(unittest.TestCase):
    def test_default_notice_poll_interval_is_ten_seconds(self) -> None:
        self.assertEqual(Config().poll_interval_seconds, 10)

    def test_config_load_migrates_legacy_notice_poll_interval_to_ten_seconds(self) -> None:
        path = Path(__file__).resolve().parent / "_tmp_notice_config.json"
        try:
            path.write_text(
                json.dumps(
                    {
                        "server_url": "http://192.168.11.147:9000",
                        "poll_interval_seconds": 5,
                        "muted": False,
                        "last_message_id": 12,
                        "tts_rate": 180,
                        "volume": 1.0,
                    }
                ),
                encoding="utf-8",
            )

            with patch.object(tray_config, "config_path", return_value=path):
                config = Config.load()

            self.assertEqual(config.poll_interval_seconds, 10)
            saved = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(saved["poll_interval_seconds"], 10)
        finally:
            path.unlink(missing_ok=True)

    def test_status_tokens_are_displayed_in_korean(self) -> None:
        statuses: list[str] = []
        poller = Poller(
            config=Config(),
            on_message=lambda _item: None,
            on_status=statuses.append,
        )

        poller._safe_status("connected")
        poller._safe_status("offline")

        self.assertEqual(statuses, ["연결됨", "오프라인"])

    def test_success_payload_errors_do_not_escape_the_poller_loop(self) -> None:
        class SaveFailingConfig(Config):
            def save(self) -> None:
                raise OSError("disk full")

        config = SaveFailingConfig(last_message_id=0)
        poller = Poller(
            config=config,
            on_message=lambda _item: None,
            on_status=lambda _status: (_ for _ in ()).throw(
                RuntimeError("status callback failed")
            ),
        )

        poller._handle_success({"latest_id": "7", "items": []})

        self.assertEqual(config.last_message_id, 7)

    def test_invalid_message_ids_do_not_stop_message_processing(self) -> None:
        config = Config(last_message_id=1)
        received: list[dict] = []
        poller = Poller(config=config, on_message=received.append)

        poller._handle_success(
            {"latest_id": "2", "items": [{"id": "bad", "message_text": "hello"}]}
        )

        self.assertEqual(received, [{"id": "bad", "message_text": "hello"}])
        self.assertEqual(config.last_message_id, 1)


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

    def test_test_notification_uses_popup_payload(self) -> None:
        presented: list[PopupPayload] = []
        poller = AttendanceAlertPoller(
            config=Config(),
            present_alert=presented.append,
            is_enabled_getter=lambda: True,
        )

        poller.show_test_notification()

        self.assertEqual(len(presented), 1)
        self.assertEqual(presented[0].title, "근태 알림 테스트")
        self.assertEqual(presented[0].badge_text, "TEST")
        self.assertEqual(presented[0].summary, "팝업 표시와 버튼 동작을 확인하세요.")
        self.assertEqual(presented[0].lines[0], "홍길동 인원 근태 확인")

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
        self.assertEqual(payload.summary, "이번 달 확인이 필요한 인원이 있습니다.")
        self.assertEqual(
            payload.lines,
            [
                "김철수 인원 근태 확인",
                "이영희 인원 근태 확인",
                "박민수 인원 근태 확인",
                "외 2명",
            ],
        )

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
        self.assertEqual(presented[0].badge_text, "1명")
        self.assertEqual(presented[0].summary, "이번 달 확인이 필요한 인원이 있습니다.")
        self.assertEqual(presented[0].lines[0], "김철수 인원 근태 확인")


class TrayAttendanceNavigationTests(unittest.TestCase):
    def test_attendance_page_url_uses_server_root(self) -> None:
        self.assertEqual(
            tray_main.attendance_page_url("http://192.168.11.147:9000/"),
            "http://192.168.11.147:9000/attendance",
        )

    def test_test_alert_only_shows_popup(self) -> None:
        events: list[str] = []

        class FakeAlertPoller:
            def show_test_notification(self) -> None:
                events.append("popup")

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
            app._test_alert(None, None)

        self.assertEqual(events, ["popup"])

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
