"""연속 숫자 비밀번호 판정 계약 — 인접 자릿수 차이 규칙 (9↔0 wrap-around 없음)."""

from __future__ import annotations

from src.attendance_auth import _is_sequential_digits


# --- True: 진짜 연속 (오름/내림차순, 길이 >= 4) ---
def test_sequential_ascending_1234() -> None:
    assert _is_sequential_digits("1234") is True


def test_sequential_ascending_leading_zero_0123() -> None:
    assert _is_sequential_digits("0123") is True


def test_sequential_descending_4321() -> None:
    assert _is_sequential_digits("4321") is True


def test_sequential_descending_9876() -> None:
    assert _is_sequential_digits("9876") is True


def test_sequential_long_123456() -> None:
    assert _is_sequential_digits("123456") is True


# --- False: 9↔0 wrap-around 는 연속이 아님 ---
def test_wrap_around_8901_not_sequential() -> None:
    assert _is_sequential_digits("8901") is False


def test_wrap_around_9012_not_sequential() -> None:
    assert _is_sequential_digits("9012") is False


def test_wrap_around_1098_not_sequential() -> None:
    assert _is_sequential_digits("1098") is False


# --- False: 비연속 패턴 ---
def test_odds_13579_not_sequential() -> None:
    assert _is_sequential_digits("13579") is False


def test_pair_repeats_1122_not_sequential() -> None:
    assert _is_sequential_digits("1122") is False


def test_all_same_1111_not_sequential() -> None:
    assert _is_sequential_digits("1111") is False


def test_non_digit_abc123_not_sequential() -> None:
    assert _is_sequential_digits("abc123") is False


def test_too_short_123_not_sequential() -> None:
    assert _is_sequential_digits("123") is False


def test_empty_string_not_sequential() -> None:
    assert _is_sequential_digits("") is False
