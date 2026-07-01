from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from ..auth import ACCESS_LEVEL_LABEL
from ..db import row_to_dict


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=50)
    password: str = Field(min_length=1, max_length=100)


class OperatorSelectRequest(BaseModel):
    user_id: int = Field(gt=0)


class ImportRequest(BaseModel):
    raw_text: str = Field(min_length=1)
    created_by: str = Field(default="책임자")
    revision_of: int | None = None
    force: bool = False
    effective_from: str | None = Field(default=None, max_length=10)  # 사용 시작일(YYYY-MM-DD), 미지정 시 등록일


class StatusUpdateRequest(BaseModel):
    action: str = Field(pattern="^(start|complete|cancel)$")
    reason: str | None = None


class ViscosityReadingBody(BaseModel):
    product_id: int = Field(gt=0)
    lot_no: str = Field(min_length=1, max_length=100)
    viscosity: float = Field(gt=0, le=100000)
    measured_date: str | None = Field(default=None, max_length=10)
    memo: str | None = Field(default=None, max_length=1000)
    recipe_material: str | None = Field(default=None, max_length=200)
    material_lot: str | None = Field(default=None, max_length=100)


class ViscosityProductCreateBody(BaseModel):
    code: str = Field(min_length=1, max_length=50, pattern=r"^[A-Za-z0-9._-]+$")
    name: str = Field(min_length=1, max_length=100)
    target: float | None = Field(default=None, gt=0, le=100000)
    lower_limit: float | None = Field(default=None, ge=0, le=100000)
    upper_limit: float | None = Field(default=None, gt=0, le=100000)
    sigma_k: float = Field(default=3, ge=1, le=6)

    @model_validator(mode="after")
    def validate_limits(self) -> "ViscosityProductCreateBody":
        if (
            self.lower_limit is not None
            and self.upper_limit is not None
            and self.lower_limit >= self.upper_limit
        ):
            raise ValueError("lower_limit must be less than upper_limit")
        return self


class ViscosityProductUpdateBody(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    target: float | None = Field(default=None, gt=0, le=100000)
    lower_limit: float | None = Field(default=None, ge=0, le=100000)
    upper_limit: float | None = Field(default=None, gt=0, le=100000)
    sigma_k: float = Field(default=3, ge=1, le=6)
    rpm: float | None = Field(default=None, ge=0, le=100000)
    temperature: float | None = Field(default=None, ge=-50, le=300)
    remind_daily: bool = False
    is_active: bool = True

    @model_validator(mode="after")
    def validate_limits(self) -> "ViscosityProductUpdateBody":
        if (
            self.lower_limit is not None
            and self.upper_limit is not None
            and self.lower_limit >= self.upper_limit
        ):
            raise ValueError("lower_limit must be less than upper_limit")
        return self


class BlendDetailBody(BaseModel):
    material_id: int | None = None
    material_code: str | None = Field(default=None, max_length=100)
    material_name: str = Field(min_length=1, max_length=200)
    material_lot: str | None = Field(default=None, max_length=100)
    ratio: float | None = Field(default=None, ge=0, le=100)
    theory_amount: float | None = Field(default=None, ge=0)
    actual_amount: float | None = Field(default=None, ge=0)
    sequence_order: int | None = Field(default=None, ge=0)


class BlendCreateBody(BaseModel):
    recipe_id: int | None = Field(default=None, gt=0)
    product_name: str = Field(min_length=1, max_length=200)
    ink_name: str | None = Field(default=None, max_length=200)
    position: str | None = Field(default=None, max_length=200)
    worker: str = Field(min_length=1, max_length=100)
    work_date: str = Field(min_length=8, max_length=10)
    work_time: str | None = Field(default=None, max_length=8)
    total_amount: float = Field(gt=0, le=10_000_000)
    scale: str | None = Field(default=None, max_length=100)
    note: str | None = Field(default=None, max_length=1000)
    worker_sign: str | None = Field(default=None, max_length=300_000)
    details: list[BlendDetailBody] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_worker_sign(self) -> "BlendCreateBody":
        self.worker_sign = _validate_signature(self.worker_sign)
        return self


class BlendViscosityBody(BaseModel):
    # 제품은 배합 기록의 제품(레시피)명으로 자동 확보 — product_id 입력 불필요.
    viscosity: float = Field(gt=0, le=100000)
    memo: str | None = Field(default=None, max_length=1000)


def _validate_signature(value: str | None) -> str | None:
    """전자서명 data URL 검증: PNG base64 + 크기 상한(~220KB)."""
    if value is None or value == "":
        return None
    if not value.startswith("data:image/png;base64,"):
        raise ValueError("signature must be a PNG data URL")
    if len(value) > 300_000:
        raise ValueError("signature too large")
    return value


class BlendApprovalBody(BaseModel):
    role: Literal["review", "approve"]
    name: str = Field(min_length=1, max_length=100)
    signature: str | None = Field(default=None, max_length=300_000)

    @model_validator(mode="after")
    def _check_sign(self) -> "BlendApprovalBody":
        self.signature = _validate_signature(self.signature)
        return self


class BlendBulkEntryBody(BaseModel):
    work_date: str = Field(min_length=8, max_length=10)
    total_amount: float = Field(gt=0, le=10_000_000)
    work_time: str | None = Field(default=None, max_length=8)
    note: str | None = Field(default=None, max_length=1000)


class BlendBulkBody(BaseModel):
    recipe_id: int = Field(gt=0)
    worker: str = Field(min_length=1, max_length=100)
    scale: str | None = Field(default=None, max_length=100)
    entries: list[BlendBulkEntryBody] = Field(default_factory=list)


class WorkerCreateBody(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class WorkerUpdateBody(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    is_active: bool | None = None


class WeighingStepRequest(BaseModel):
    recipe_id: int = Field(gt=0)
    material_id: int | None = Field(default=None, gt=0)
    recipe_item_id: int | None = Field(default=None, gt=0)
    actual_weight: float | None = Field(default=None, ge=0)


class WeighingStepUndoRequest(BaseModel):
    recipe_id: int = Field(gt=0)
    material_id: int | None = Field(default=None, gt=0)
    recipe_item_id: int | None = Field(default=None, gt=0)


class WeighingRecipeCompleteRequest(BaseModel):
    recipe_id: int = Field(gt=0)


class AdminUserCreateRequest(BaseModel):
    username: str = Field(min_length=3, max_length=50, pattern=r"^[A-Za-z0-9._-]+$")
    display_name: str = Field(min_length=1, max_length=50)
    access_level: Literal["operator", "manager", "admin"]
    password: str = Field(min_length=8, max_length=100)


class AdminUserUpdateRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=50)
    access_level: Literal["operator", "manager", "admin"]
    is_active: bool


class AdminUserPasswordResetRequest(BaseModel):
    password: str = Field(min_length=8, max_length=100)


def actor_name(current_user: dict[str, Any]) -> str:
    return str(current_user.get("display_name") or current_user.get("username") or "사용자")


def role_for_access_level(access_level: str) -> str:
    return "user"


def serialize_admin_user(row: Any) -> dict[str, Any]:
    payload = row_to_dict(row)
    payload["role_label"] = ACCESS_LEVEL_LABEL.get(str(payload.get("access_level")), "User")
    payload["is_active"] = bool(payload.get("is_active"))
    return payload


def recipe_label(row: dict[str, Any]) -> str:
    return str(row.get("product_name") or "-")
