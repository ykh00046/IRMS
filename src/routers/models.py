from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from ..auth import ACCESS_LEVEL_LABEL
from ..db import row_to_dict


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=50)
    password: str = Field(min_length=1, max_length=100)


class ImportRequest(BaseModel):
    raw_text: str = Field(min_length=1)
    created_by: str = Field(default="책임자")
    revision_of: int | None = None
    force: bool = False
    effective_from: str | None = Field(default=None, max_length=10)  # 사용 시작일(YYYY-MM-DD), 미지정 시 등록일
    # 기준 배합량(g, 선택) — 배합 화면 '기준량' 버튼이 채울 총량. 최대 3개, 미지정 시 버튼 없음.
    base_totals: list[float] | None = Field(default=None, max_length=3)
    # (구) 단일 기준 배합량 — 하위호환용, base_totals 미지정 시 사용.
    base_total: float | None = Field(default=None, gt=0, le=10_000_000)
    # 기준 자재 이름(선택) — 배합 시 먼저 계량할 자재. 임포트 항목 중 정확히 일치하는 이름이어야 함.
    anchor_material: str | None = Field(default=None, max_length=200)
    # 레시피별 계량 허용 편차(g, 선택) — NULL = 기본값 0.05g. 0 < v <= 1000.
    tolerance_g: float | None = Field(default=None)
    # 반제품 ERP 품목코드(code-edit-relocate §3) — 명시 값이 자동 인식·승계보다 우선.
    # 비면 기존 동작(반제품명 → product 마스터 단일 히트 자동 인식, 수정 등록 시 부모 승계).
    # 형식 검사(^[A-Z]{2}[A-Z0-9]{2,8}$)와 체인 중복(409)은 라우트에서 처리.
    product_code: str | None = Field(default=None, max_length=20)
    # item-code P3: 마스터에 없는 신규 자재(unknown)를 허용할지.
    # 기본 False — unknown 이 있으면 차단(errors). True 면 경고(warnings)로 강등하여
    # 코드 없이 자동 등록한다(명시적 확인 경로, spec §1).
    allow_unknown_materials: bool = False

    @model_validator(mode="after")
    def _check_base_totals(self) -> "ImportRequest":
        if self.base_totals:
            cleaned = []
            for v in self.base_totals:
                if not (0 < v <= 10_000_000):
                    raise ValueError("기준 배합량은 0 초과 10,000,000 이하여야 합니다.")
                if v not in cleaned:
                    cleaned.append(v)
            self.base_totals = cleaned
        if self.tolerance_g is not None and not (0 < self.tolerance_g <= 1000):
            raise ValueError("허용 편차는 0 초과 1000 이하여야 합니다.")
        return self


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
    reactor: int | None = Field(default=None, ge=1, le=4)


class ViscosityProductCreateBody(BaseModel):
    # code 는 레시피 제품명과 연동(라우트에서 존재 검증) — 한글 제품명 허용.
    code: str = Field(min_length=1, max_length=100)
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
    use_reactor: bool = False
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
    # 이 자재의 실제량이 저울 연동 중 손입력이었는가(행 단위 추적)
    manual_entry: bool = False


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
    reactor: int | None = Field(default=None, ge=1, le=4)
    worker_sign: str | None = Field(default=None, max_length=300_000)
    # 저울 연동 중 '수동 입력' 토글로 계량값을 직접 입력했는가(추적성 — 기록에 표시).
    manual_entry: bool = False
    details: list[BlendDetailBody] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_worker_sign(self) -> "BlendCreateBody":
        self.worker_sign = _validate_signature(self.worker_sign)
        return self


class BlendContinuousBody(BaseModel):
    """이어서 계량(연속 배합): 한 레시피 · 동일 총량으로 N개 로트를 한 번에 저장.

    lots 는 로트별 자재 상세 목록(각 로트 = 자재 전체 목록). 총량·서명·반응기·작업일은
    전 로트 공유. 자재 LOT·실제량·수동입력 여부만 로트별(사람이 아는 값)로 받는다.

    lot_totals 미전송 시 전 로트 total_amount(기존 동작). 초과 계량 증량이 발생한 로트만
    큰 값을 보낸다 — 그 로트는 lot_totals[j] 기준으로 서버 도출·편차검사가 이뤄지고,
    record.total_amount 도 그 값으로 저장된다.
    """
    recipe_id: int = Field(gt=0)                  # 연속 배합은 레시피 기반만 허용
    product_name: str = Field(min_length=1, max_length=200)
    ink_name: str | None = Field(default=None, max_length=200)
    position: str | None = Field(default=None, max_length=200)
    work_date: str = Field(min_length=8, max_length=10)
    work_time: str | None = Field(default=None, max_length=8)
    total_amount: float = Field(gt=0, le=10_000_000)   # 전 로트 동일 총량(기본)
    scale: str | None = Field(default=None, max_length=100)
    note: str | None = Field(default=None, max_length=1000)
    reactor: int | None = Field(default=None, ge=1, le=4)
    worker_sign: str | None = Field(default=None, max_length=300_000)  # 전 로트 동일 서명
    lots: list[list[BlendDetailBody]] = Field(default_factory=list)
    # 로트별 총량 오버라이드(초과 계량 증량). 미전송·전부 null 이면 기존 동작(total_amount).
    lot_totals: list[float | None] | None = Field(default=None)

    @model_validator(mode="after")
    def _check_worker_sign(self) -> "BlendContinuousBody":
        self.worker_sign = _validate_signature(self.worker_sign)
        return self

    @model_validator(mode="after")
    def _check_lot_totals(self) -> "BlendContinuousBody":
        # lot_totals 가 주어지면 (a) 길이 == 로트 수, (b) 각 값 > 0 · ≤ 10,000,000.
        # null 원소는 허용(해당 로트는 공용 total_amount 사용) — 기존 동작과 자연 정합.
        if self.lot_totals is None:
            return self
        if len(self.lot_totals) != len(self.lots):
            raise ValueError("lot_totals 길이가 로트 수와 다릅니다.")
        for idx, value in enumerate(self.lot_totals):
            if value is None:
                continue
            if not (0 < value <= 10_000_000):
                raise ValueError(f"lot_totals[{idx}] 는 0 초과 10,000,000 이하여야 합니다.")
        return self


class BlendViscosityBody(BaseModel):
    # 제품은 배합 기록의 제품(레시피)명으로 자동 확보 — product_id 입력 불필요.
    # 반응기는 배합 실적에서 물려받으므로 여기서 입력하지 않는다.
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
    # 작업자 분류(파트) — 약품/합성/잉크/용수. 새 작업자 등록 시 선택.
    # None(생략) 은 미지정(NULL). 라우트에서 허용값 검증.
    category: str | None = Field(default=None, max_length=20)


class WorkerUpdateBody(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    is_active: bool | None = None
    # 작업자 분류(파트) — 약품/합성/잉크/용수. 라우트에서 허용값 검증.
    # 규칙: None=변경 안 함(기존 PATCH 규칙과 동일), 빈 문자열 ""=미지정(NULL)으로 해제.
    category: str | None = Field(default=None, max_length=20)


def _check_manager_password(value: str) -> str:
    """책임자 비밀번호 강도 — 근태(8자+반복/연속 차단)와 동일 수준으로 통일."""
    if len(set(value)) == 1:
        raise ValueError("같은 문자만 반복된 비밀번호는 쓸 수 없습니다.")
    if value.isdigit():
        diffs = {ord(b) - ord(a) for a, b in zip(value, value[1:])}
        if diffs in ({1}, {-1}):
            raise ValueError("연속된 숫자(12345678 등)는 비밀번호로 쓸 수 없습니다.")
    return value


class WorkerManagerBody(BaseModel):
    # 이용자를 책임자로 지정/비밀번호 초기화할 때의 개인 비밀번호 (8자 이상 + 강도검사)
    password: str = Field(min_length=8, max_length=100)

    @model_validator(mode="after")
    def _strength(self) -> "WorkerManagerBody":
        _check_manager_password(self.password)
        return self


class ChangePasswordBody(BaseModel):
    # 로그인한 책임자가 본인 비밀번호를 직접 변경(현재 비밀번호 확인)
    current_password: str = Field(min_length=1, max_length=100)
    new_password: str = Field(min_length=8, max_length=100)

    @model_validator(mode="after")
    def _strength(self) -> "ChangePasswordBody":
        _check_manager_password(self.new_password)
        return self


class AdminUserCreateRequest(BaseModel):
    username: str = Field(min_length=3, max_length=50, pattern=r"^[A-Za-z0-9._-]+$")
    display_name: str = Field(min_length=1, max_length=50)
    access_level: Literal["operator", "manager"]
    password: str = Field(min_length=8, max_length=100)


class AdminUserUpdateRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=50)
    access_level: Literal["operator", "manager"]
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
