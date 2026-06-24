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
