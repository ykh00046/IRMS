"""2교대(주간/야간) 반차·반반차 baseline 회귀 테스트.

정규 8시간 근무 안에 휴식 30분이 포함되어, 잔업 없이 풀근무하면 출근+8h30m에
퇴근한다(주간 15:30 / 야간 27:30). 휴식은 정규 근무 후반부에 있어 6시간 이하만
일하고 나가는 오후 반차/반반차에는 붙지 않는다.

    - 오전(늦게 출근): 출근 = 출근기준 + 휴가량(반차 4h / 반반차 2h)
    - 오후(일찍 퇴근): 퇴근 = 출근기준 + (정규 8h − 휴가량)

(2026-06-17 현장 확인. project_attendance_shifts 메모리 참조)
"""

import unittest

from src.services import attendance_excel
from src.services.attendance_excel import AttendanceRow


def _row(day_type: str = "평일", note: str = "", attendance_code: str = "") -> AttendanceRow:
    return AttendanceRow(
        date="2026-06-15",
        weekday="월",
        day_type=day_type,
        check_in=None,
        check_out=None,
        next_day=False,
        weekday_early=0.0,
        weekday_normal=8.0,
        weekday_overtime=0.0,
        weekday_night=0.0,
        holiday_early=0.0,
        holiday_normal=0.0,
        holiday_overtime=0.0,
        holiday_night=0.0,
        late_hours=0.0,
        early_leave_hours=0.0,
        outing_hours=0.0,
        note=note,
        attendance_code=attendance_code,
    )


def _hm(h: int, m: int = 0) -> int:
    return h * 60 + m


class Shift2PartialLeaveTests(unittest.TestCase):
    # (shift_time, attendance_code, 기대 출근분, 기대 퇴근분)
    CASES = [
        # 2교대(주간): 출근 07:00, 정규 퇴근 15:45 (8h + 휴식 45분)
        ("2교대(주간)", "", _hm(7), _hm(15, 45)),            # 정규(잔업 X)
        ("2교대(주간)", "반차 오전", _hm(11), _hm(15, 45)),   # 07:00+4h 출근
        ("2교대(주간)", "반차 오후", _hm(7), _hm(11)),        # 07:00+(8-4)h 퇴근
        ("2교대(주간)", "반반차 오전", _hm(9), _hm(15, 45)),  # 07:00+2h 출근
        ("2교대(주간)", "반반차 오후", _hm(7), _hm(13)),      # 07:00+(8-2)h 퇴근
        # 2교대(야간): 출근 19:00, 정규 퇴근 27:45(익일 03:45)
        ("2교대(야간)", "", _hm(19), _hm(27, 45)),
        ("2교대(야간)", "반차 오전", _hm(23), _hm(27, 45)),
        ("2교대(야간)", "반차 오후", _hm(19), _hm(23)),
        ("2교대(야간)", "반반차 오전", _hm(21), _hm(27, 45)),
        ("2교대(야간)", "반반차 오후", _hm(19), _hm(25)),     # 익일 01:00
    ]

    def test_shift2_partial_leave_baselines(self) -> None:
        for shift_time, code, exp_in, exp_out in self.CASES:
            with self.subTest(shift=shift_time, code=code or "정규"):
                row = _row(attendance_code=code)
                self.assertEqual(
                    attendance_excel._compute_row_anomaly_baseline(row, shift_time),
                    (exp_in, exp_out),
                )

    def test_day_shift_partial_leave_unchanged(self) -> None:
        """주간(09-18) 점심 포함 대칭 모델은 그대로 유지."""
        self.assertEqual(
            attendance_excel._compute_row_anomaly_baseline(
                _row(attendance_code="반차 오후"), "주간"
            ),
            (_hm(9), _hm(13)),  # 18:00 - 5h
        )
        self.assertEqual(
            attendance_excel._compute_row_anomaly_baseline(
                _row(attendance_code="반반차 오후"), "주간"
            ),
            (_hm(9), _hm(16)),  # 18:00 - 2h
        )

    def test_half_inference_from_checkout_when_unmarked(self) -> None:
        """오전/오후 표기가 없으면 퇴근 기록으로 추론한다(2교대 주간)."""
        row = _row(attendance_code="반차")
        row.check_out = "11:00"  # 정규 퇴근(15:45) 한참 이전 → 오후 반차로 추론
        _base_in, base_out = attendance_excel._compute_row_anomaly_baseline(
            row, "2교대(주간)"
        )
        self.assertEqual(base_out, _hm(11))

    def test_afternoon_leave_inferred_when_overtime_partially_worked(self) -> None:
        """정시 출근 + 정규 퇴근(15:45) 이전 퇴근이면, 모델상 기대 퇴근(13:00)
        보다 늦더라도 오후 반반차로 추론해 조퇴 오탐을 내지 않는다.

        (2026-06-16 현장 사례: 반반차 표기 없음, 06:55 출근 / 14:42 퇴근)
        """
        row = _row(attendance_code="반반차")
        row.check_in = "06:55"
        row.check_out = "14:42"
        base_in, base_out = attendance_excel._compute_row_anomaly_baseline(
            row, "2교대(주간)"
        )
        self.assertEqual((base_in, base_out), (_hm(7), _hm(13)))
        # 14:42 > 기대 퇴근 13:00 → 조퇴 미처리 없음
        self.assertNotIn(
            "조퇴 미처리",
            attendance_excel._unprocessed_row_issues(row, "2교대(주간)"),
        )


if __name__ == "__main__":
    unittest.main()
