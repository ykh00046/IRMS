from typing import Any, Literal

from pydantic import BaseModel, Field

from ..auth import ACCESS_LEVEL_LABEL
from ..database import row_to_dict


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


class WeighingStepRequest(BaseModel):
    recipe_id: int = Field(gt=0)
    material_id: int = Field(gt=0)


class WeighingStepUndoRequest(BaseModel):
    recipe_id: int = Field(gt=0)
    material_id: int = Field(gt=0)


class WeighingRecipeCompleteRequest(BaseModel):
    recipe_id: int = Field(gt=0)


class AdminUserCreateRequest(BaseModel):
    username: str = Field(min_length=3, max_length=50, pattern=r"^[A-Za-z0-9._-]+$")
    display_name: str = Field(min_length=1, max_length=50)
    access_level: Literal["operator", "manager"]
    password: str = Field(min_length=6, max_length=100)


class AdminUserUpdateRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=50)
    access_level: Literal["operator", "manager"]
    is_active: bool


class AdminUserPasswordResetRequest(BaseModel):
    password: str = Field(min_length=6, max_length=100)


class ChatMessageCreateRequest(BaseModel):
    room_key: Literal["notice", "mass_response", "liquid_ink_response", "sample_mass_production"]
    message_text: str = Field(min_length=1, max_length=1000)
    stage: Literal["registered", "in_progress", "completed"] | None = None


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
