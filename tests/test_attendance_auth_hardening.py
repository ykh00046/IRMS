"""Security contracts for attendance credential provisioning."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src import attendance_auth


def test_missing_account_is_not_auto_created_and_uses_generic_error() -> None:
    with (
        patch.object(attendance_auth, "_fetch", return_value=None),
        patch.object(attendance_auth, "_create") as create,
        patch.object(attendance_auth, "_log_failed_login") as audit,
        patch(
            "src.services.attendance_excel.employee_exists_in_any_month",
            return_value=True,
        ),
    ):
        with pytest.raises(attendance_auth.AttendanceAuthError) as raised:
            attendance_auth.authenticate("171013", "171013")

    assert raised.value.code == "INVALID_CREDENTIALS"
    assert raised.value.status_code == 401
    create.assert_not_called()
    audit.assert_called_once_with("171013", "account_not_provisioned")


def test_unknown_employee_uses_same_public_error_contract() -> None:
    with (
        patch.object(attendance_auth, "_fetch", return_value=None),
        patch.object(attendance_auth, "_log_failed_login") as audit,
        patch(
            "src.services.attendance_excel.employee_exists_in_any_month",
            return_value=False,
        ),
    ):
        with pytest.raises(attendance_auth.AttendanceAuthError) as raised:
            attendance_auth.authenticate("missing", "anything")

    assert raised.value.code == "INVALID_CREDENTIALS"
    assert raised.value.status_code == 401
    audit.assert_called_once_with("missing", "employee_not_in_excel")


def test_ensure_account_requires_manager_provisioning() -> None:
    with patch.object(attendance_auth, "_fetch", return_value=None):
        with pytest.raises(attendance_auth.AttendanceAuthError) as raised:
            attendance_auth.ensure_account("171013")

    assert raised.value.code == "ACCOUNT_NOT_PROVISIONED"
    assert raised.value.status_code == 401


def test_manager_provisioning_uses_random_temporary_password() -> None:
    with (
        patch(
            "src.services.attendance_excel.employee_exists_in_any_month",
            return_value=True,
        ),
        patch.object(attendance_auth, "_fetch", return_value=None),
        patch.object(
            attendance_auth,
            "generate_temporary_password",
            return_value="random-temp-password",
        ),
        patch.object(attendance_auth, "_create") as create,
    ):
        password = attendance_auth.reset_password_to_temporary("171013")

    assert password == "random-temp-password"
    create.assert_called_once_with("171013", "random-temp-password", reset_required=1)


# ── 임시 비밀번호는 사람이 읽어 전달한다(2026-08-28) ─────────────────────────
# 종전 token_urlsafe(18) 은 24자에 대소문자와 -_ 가 섞여, 책임자가 불러 주고 직원이
# 받아 적는 실제 전달 경로에서 오타가 났다. 8자 · 헷갈리는 글자 제외로 바꾼다.
def test_temporary_password_is_short_and_unambiguous() -> None:
    password = attendance_auth.generate_temporary_password()

    assert len(password) == 8
    assert set(password) <= set(attendance_auth.TEMP_PASSWORD_ALPHABET)
    # 눈으로 구분이 안 되는 글자는 알파벳 자체에 없어야 한다.
    for ambiguous in "0O1IL":
        assert ambiguous not in attendance_auth.TEMP_PASSWORD_ALPHABET
    # 구분자 없이 그대로 타이핑하는 값이다.
    assert password.isalnum()


def test_temporary_passwords_are_not_predictable() -> None:
    assert (
        attendance_auth.generate_temporary_password()
        != attendance_auth.generate_temporary_password()
    )
