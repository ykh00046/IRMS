import sqlite3
import unittest

from fastapi import HTTPException

from src.database import utc_now_text
from src.routers.chat_routes import (
    NOTICE_POST_LIMIT_PER_USER,
    _enforce_notice_post_rate_limit,
    _normalize_chat_stage,
)


class NoticePostRateLimitTests(unittest.TestCase):
    def test_notice_rate_limit_blocks_repeated_posts_by_same_user(self) -> None:
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.execute(
            """
            CREATE TABLE chat_messages (
                room_key TEXT NOT NULL,
                created_by_user_id INTEGER NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        for _ in range(NOTICE_POST_LIMIT_PER_USER):
            connection.execute(
                "INSERT INTO chat_messages (room_key, created_by_user_id, created_at) VALUES (?, ?, ?)",
                ("notice", 10, utc_now_text()),
            )

        with self.assertRaises(HTTPException) as raised:
            _enforce_notice_post_rate_limit(connection, user_id=10, now_text=utc_now_text())

        self.assertEqual(raised.exception.status_code, 429)


class ChatStageValidationTests(unittest.TestCase):
    def test_workflow_rooms_require_stage(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            _normalize_chat_stage({"scope": "workflow"}, None)

        self.assertEqual(raised.exception.status_code, 400)

    def test_notice_rooms_discard_stage(self) -> None:
        self.assertIsNone(_normalize_chat_stage({"scope": "notice"}, "completed"))


if __name__ == "__main__":
    unittest.main()
