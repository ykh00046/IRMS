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


class StatusUpdateRequest(BaseModel):
    action: str = Field(pattern="^(start|complete|cancel)$")
    reason: str | None = None


class StockAmountBody(BaseModel):
    amount: float
    note: str | None = None


class StockAdjustBody(BaseModel):
    new_quantity: float
    note: str


class StockDiscardBody(BaseModel):
    amount: float
    note: str


class StockThresholdBody(BaseModel):
    threshold: float


class ForecastParamsBody(BaseModel):
    lead_time_days: float = Field(ge=0)
    reorder_cycle_days: float = Field(ge=0)


class LotCreateBody(BaseModel):
    lot_no: str | None = Field(default=None, max_length=100)
    quantity: float = Field(gt=0)
    received_at: str | None = None
    expiry_date: str | None = None
    note: str | None = None


class LotConsumeBody(BaseModel):
    amount: float = Field(gt=0)
    note: str | None = None


class LotDiscardBody(BaseModel):
    note: str = Field(min_length=1)


class OrderCreateBody(BaseModel):
    window_days: int = Field(default=30, ge=7, le=365)


class OrderItemEditBody(BaseModel):
    id: int = Field(gt=0)
    order_qty: float = Field(ge=0)
    note: str | None = None


class OrderUpdateBody(BaseModel):
    note: str | None = None
    items: list[OrderItemEditBody] = Field(default_factory=list)


class ReceiptLineBody(BaseModel):
    order_item_id: int = Field(gt=0)
    received_qty: float = Field(ge=0)
    lot_no: str | None = Field(default=None, max_length=100)
    expiry_date: str | None = None
    note: str | None = None


class ReceiptCreateBody(BaseModel):
    note: str | None = None
    lines: list[ReceiptLineBody] = Field(default_factory=list)


class WeighingStepRequest(BaseModel):
    recipe_id: int = Field(gt=0)
    material_id: int | None = Field(default=None, gt=0)
    recipe_item_id: int | None = Field(default=None, gt=0)


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


NOTICE_MESSAGE_MAX_LENGTH = 300


class ChatMessageCreateRequest(BaseModel):
    room_key: Literal["notice", "mass_response", "liquid_ink_response", "sample_mass_production"]
    message_text: str = Field(min_length=1, max_length=1000)
    stage: Literal["registered", "in_progress", "completed"] | None = None

    @model_validator(mode="after")
    def validate_notice_message_length(self) -> "ChatMessageCreateRequest":
        if self.room_key == "notice" and len(self.message_text.strip()) > NOTICE_MESSAGE_MAX_LENGTH:
            raise ValueError(f"notice messages must be {NOTICE_MESSAGE_MAX_LENGTH} characters or fewer")
        return self


CHAT_STAGE_OPTIONS = ("registered", "in_progress", "completed")


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
    return f"{row.get('product_name', '-')}/{row.get('ink_name', '-')}"


def serialize_chat_room(row: Any) -> dict[str, Any]:
    payload = row_to_dict(row)
    payload["is_active"] = bool(payload.get("is_active"))
    payload["stage_required"] = payload.get("scope") == "workflow"
    payload["stage_options"] = list(CHAT_STAGE_OPTIONS) if payload["stage_required"] else []
    return payload


def serialize_chat_message(row: Any) -> dict[str, Any]:
    return row_to_dict(row)
