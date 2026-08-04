from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from ..auth import ACCESS_LEVEL_LABEL
from ..db import row_to_dict
from ..services.blend_service import BLEND_TOTAL_MAX_G


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=50)
    password: str = Field(min_length=1, max_length=100)


class ImportRequest(BaseModel):
    # 상한 200,000자 — 정상적인 배합표 붙여넣기는 수천 자 규모이므로, 병적으로 큰
    # 붙여넣기(파서 과부하)를 Pydantic 검증 단계에서 422 로 거르는 안전장치.
    raw_text: str = Field(min_length=1, max_length=200_000)
    created_by: str = Field(default="책임자")
    revision_of: int | None = None
    force: bool = False
    effective_from: str | None = Field(default=None, max_length=10)  # 사용 시작일(YYYY-MM-DD), 미지정 시 등록일
    # 기준 배합량(g, 선택) — 배합 화면 '기준' 버튼이 채울 총량. 최대 3개, 미지정 시 버튼 없음.
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
    # reactor-ownership: 반응기 진행 여부(recipes.use_reactor) — 명시 값이 최우선.
    # None(기본)이면 수정 등록 때 부모 레시피의 use_reactor 를 승계(tolerance_g/category 와 동일),
    # 비개정 신규 레시피면 0(반응기 아님)으로 시작한다.
    use_reactor: bool | None = None
    # 파생(derived): 앞 단계 총량을 이월받아 다시 계량하지 않는 레시피(recipes.is_derived).
    # use_reactor 와 독립 — 반응기 이월(carry-over) 허용 여부는 이 값으로 결정된다.
    # 명시 값이 최우선, None(기본)이면 수정 등록 시 부모 승계(use_reactor 와 동일 구조),
    # 비개정 신규 레시피면 0(파생 아님)으로 시작한다.
    is_derived: bool | None = None
    # 1차→2차 레시피 연계(recipes.stage1_recipe_id) — 2차 레시피가 명시적으로 자신의 1차 레시피를
    # 가리킨다. None(기본)이면 수정 등록 시 부모 승계(use_reactor/is_derived 와 동일 구조),
    # 비개정 신규 레시피면 NULL(1차 링크 없음).
    stage1_recipe_id: int | None = None

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
    # restore: 취소된 레시피를 되돌린다(2026-07-26). 예전에는 canceled 에서 빠져나오는
    # 전이가 없어, 등록 취소를 누르면 배합 화면 목록에서 영영 사라졌다(DB 직접 수정 외 복구 불가).
    action: str = Field(pattern="^(start|complete|cancel|restore)$")
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


class ViscosityExcludeBody(BaseModel):
    # 통계 제외 사유(필수) — 왜 이 측정을 σ/평균 계산에서 뺐는지 기록으로 남긴다.
    reason: str = Field(min_length=1, max_length=500)


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
    # 반응기 이월(carry-over) 행 — 1차 배합 총량을 2차 기준 자재 실제량으로 가져온 행.
    # 서버가 반응기·기준자재·1차 LOT 일치를 모두 검증한 뒤 actual_amount 를 강제 채운다.
    carried_over: bool = False


class LotOverrideBody(BaseModel):
    """앞 단계 배합 기록에 없는 반제품 LOT 로 '확인하고 진행' 한 건.

    2026-08-04 이전에는 사유가 **필수**였다(사유 없으면 저장 400). 1차 배합을 만들고
    곧바로 2차에 투입하는 정당한 경우에도 매번 걸려, 작업자가 사유란에 아무 글자나
    치고 넘어가면서 통제가 형해화됐다 — 지금은 막지 않고 확인 창만 띄운다.

    따라서 reason 은 **선택**(빈 문자열 허용)이다. 대신 acknowledged 로 "작업자가
    확인 창을 보고 계속을 눌렀다"는 사실을 남긴다. 사유가 비어도 이 항목이 존재하는
    것 자체가 신호다 — 서버는 이를 blend_lot_acks 에 구조화 저장해 나중에 "그 LOT 이
    결국 생겼는지" 자동 대사할 수 있게 한다.
    """
    material_name: str = Field(min_length=1, max_length=200)
    material_lot: str = Field(min_length=1, max_length=100)
    # 사유는 선택 — 빈 값이어도 '확인하고 진행함' 사실은 남는다.
    reason: str = Field(default="", max_length=500)
    # 작업자가 확인 창의 '계속' 을 눌렀는가. 화면을 거치지 않은 경로(저장 시점 일괄
    # 수집·서버 단독 감지)는 False 로 남겨 대사 화면이 두 경우를 구분할 수 있게 한다.
    acknowledged: bool = True


class BatchDiscardDetailBody(BaseModel):
    """배치 폐기 시점까지 계량돼 있던 자재 1행 — 무엇이 얼마나 버려졌는지의 근거."""
    material_name: str = Field(min_length=1, max_length=200)
    material_code: str = Field(default="", max_length=50)
    material_lot: str = Field(default="", max_length=100)
    actual_amount: float = Field(ge=0)


class BlendBatchDiscardBody(BaseModel):
    """배치 전체 폐기 기록 — 과중량 폐기 권장·3회 증량 차단 뒤 협의 폐기의 흔적.

    저장 없이 화면을 떠나면 이 폐기는 어디에도 남지 않았다(실물 소모 최대의 무기록
    경로). 제품 LOT 을 소비하지 않는 별도 스트림 — blend_records 와 섞지 않는다.
    """
    recipe_id: int | None = Field(default=None, gt=0)
    product_name: str = Field(min_length=1, max_length=200)
    work_date: str = Field(min_length=8, max_length=10)
    total_amount: float | None = Field(default=None, gt=0)
    reason: str = Field(min_length=1, max_length=500)
    source: str = Field(pattern="^(overweight|rescale_limit|manual)$")
    details: list[BatchDiscardDetailBody] = Field(default_factory=list, max_length=100)


class DiscardEventBody(BaseModel):
    """계량 중 자재 폐기 1건 — '처음부터 다시' 재계량에서 담은 자재를 실제로 버린 경우.

    편차 강제 체계에서 최종 기록은 항상 이론량과 일치하므로, 이 목록이 없으면 버린
    자재는 어떤 기록에도 남지 않는다(자재 사용량·DHR 사각). 저장을 막지 않는 순수 기록.
    """
    material_name: str = Field(min_length=1, max_length=200)
    material_code: str = Field(default="", max_length=50)
    amount_g: float = Field(gt=0)


class BlendCreateBody(BaseModel):
    recipe_id: int | None = Field(default=None, gt=0)
    product_name: str = Field(min_length=1, max_length=200)
    ink_name: str | None = Field(default=None, max_length=200)
    position: str | None = Field(default=None, max_length=200)
    worker: str = Field(min_length=1, max_length=100)
    work_date: str = Field(min_length=8, max_length=10)
    work_time: str | None = Field(default=None, max_length=8)
    # 총량 상한 = BLEND_TOTAL_MAX_G(200,000 g). 예전 10,000,000 g(10톤)은 사실상
    # 제약이 아니어서 자릿수 오타가 그대로 저장됐다. 현장 1회 상한(25,000 g)에서
    # 하드 차단하지 않는 이유는 '그래도 증량'(폐기 권장 무시) 경로를 살려두기
    # 위해서다 — 25,000 g 초과는 막지 않고 기록에 플래그(oversize_total)로 남는다.
    total_amount: float = Field(gt=0, le=BLEND_TOTAL_MAX_G)
    scale: str | None = Field(default=None, max_length=100)
    note: str | None = Field(default=None, max_length=1000)
    reactor: int | None = Field(default=None, ge=1, le=4)
    worker_sign: str | None = Field(default=None, max_length=300_000)
    # 저울 연동 중 '수동 입력' 토글로 계량값을 직접 입력했는가(추적성 — 기록에 표시).
    manual_entry: bool = False
    details: list[BlendDetailBody] = Field(default_factory=list)
    # 앞 단계 기록에 없는 반제품 LOT '확인하고 진행' 기록(None=미전송). 저장을 막지는
    # 않는다 — 서버가 스스로 미등록 LOT 를 감지해 blend_lot_acks 에 남기고, 이 목록은
    # 거기에 사유·확인여부를 채워 넣는 용도다(대사용 데이터 보강).
    lot_overrides: list[LotOverrideBody] | None = Field(default=None)
    # 증량(rescale) 이벤트 — {before_total, after_total, approval_id?, absence_reason?, worker_confirmed?}.
    # None/빈 리스트면 미증량(기존 동작). 최대 2건 — 각 건마다 책임자 승인(approval_id) 또는
    # 미승인 사유(absence_reason) 가 필요하다(서비스 validate_rescale_events 가 검증).
    rescale_events: list[dict[str, Any]] | None = Field(default=None)
    # 수기 입력 '책임자 부재 진행' 사유 — 저울 전용 모드에서 비밀번호 승인 없이 사유만
    # 남기고 손입력한 경우. 값이 있으면 그 기록은 책임자 확인 전까지 미확인으로 남는다.
    manual_absence_reason: str | None = Field(default=None, max_length=300)
    # 계량 중 자재 폐기 목록(None/빈 리스트=없음). 최대 20건 — 서비스가 정규화·저장.
    discard_events: list[DiscardEventBody] | None = Field(default=None, max_length=20)
    # 저장 멱등 키(클라이언트가 만드는 1회용 id). 같은 id 의 재전송은 기록을 두 벌 만들지
    # 않고 첫 결과를 그대로 돌려준다 — 타임아웃 재시도로 같은 계량값이 두 LOT 이 되는
    # 것을 막는다. 미전송(None)이면 종전과 동일하게 매번 새 기록(하위호환).
    request_id: str | None = Field(default=None, max_length=64)

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
    total_amount: float = Field(gt=0, le=BLEND_TOTAL_MAX_G)   # 전 로트 동일 총량(기본)
    scale: str | None = Field(default=None, max_length=100)
    note: str | None = Field(default=None, max_length=1000)
    reactor: int | None = Field(default=None, ge=1, le=4)
    worker_sign: str | None = Field(default=None, max_length=300_000)  # 전 로트 동일 서명
    # 앞 단계 기록에 없는 반제품 LOT '확인하고 진행' 기록 — 전 로트 공통 비고처럼
    # 전 로트에 동일 적용(차단 아님, 대사용 사유·확인여부 보강).
    lot_overrides: list[LotOverrideBody] | None = Field(default=None)
    lots: list[list[BlendDetailBody]] = Field(default_factory=list)
    # 로트별 총량 오버라이드(초과 계량 증량). 미전송·전부 null 이면 기존 동작(total_amount).
    lot_totals: list[float | None] | None = Field(default=None)
    # 로트별 증량(rescale) 이벤트 — lots 와 평행(인덱스 j = 로트 j). 각 원소는 그 로트의
    # 이벤트 목록 [{before_total, after_total, approval_id?, absence_reason?, worker_confirmed?}]
    # 또는 None(그 로트는 증량 없음). 단건(BlendCreateBody.rescale_events)의 로트별 버전이다.
    # 미전송·전부 None 이면 기존 동작(컬럼 기본값 유지). 로트별로 최대 2건 — 3건째는
    # validate_rescale_events 가 로트마다 400("3회 증량은 불가합니다…")으로 막는다.
    lot_rescale_events: list[list[dict[str, Any]] | None] | None = Field(default=None)
    # 수기 입력 '책임자 부재 진행' 사유 — 화면 단위 승인이라 전 로트에 동일 적용(비고와 같은 성격).
    manual_absence_reason: str | None = Field(default=None, max_length=300)
    # 저장 멱등 키 — 단건(BlendCreateBody.request_id)과 동일 규약. 재시도가 N로트를
    # 두 벌 만드는 것을 막는다(미전송이면 종전 동작).
    request_id: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def _check_worker_sign(self) -> "BlendContinuousBody":
        self.worker_sign = _validate_signature(self.worker_sign)
        return self

    @model_validator(mode="after")
    def _check_lot_totals(self) -> "BlendContinuousBody":
        # lot_totals 가 주어지면 (a) 길이 == 로트 수, (b) 각 값 > 0 · ≤ BLEND_TOTAL_MAX_G.
        # null 원소는 허용(해당 로트는 공용 total_amount 사용) — 기존 동작과 자연 정합.
        if self.lot_totals is None:
            return self
        if len(self.lot_totals) != len(self.lots):
            raise ValueError("lot_totals 길이가 로트 수와 다릅니다.")
        for idx, value in enumerate(self.lot_totals):
            if value is None:
                continue
            if not (0 < value <= BLEND_TOTAL_MAX_G):
                raise ValueError(
                    f"lot_totals[{idx}] 는 0 초과 {BLEND_TOTAL_MAX_G:,.0f} 이하여야 합니다."
                )
        return self

    @model_validator(mode="after")
    def _check_lot_rescale_events(self) -> "BlendContinuousBody":
        # lot_rescale_events 가 주어지면 lots 와 길이가 같아야 한다(인덱스 j = 로트 j).
        # 내용 검증(승인 유효성·3회 제한)은 서버 validate_rescale_events 가 로트마다 수행.
        if self.lot_rescale_events is None:
            return self
        if len(self.lot_rescale_events) != len(self.lots):
            raise ValueError("lot_rescale_events 길이가 로트 수와 다릅니다.")
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
    total_amount: float = Field(gt=0, le=BLEND_TOTAL_MAX_G)
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
    # 폴백도 한글 표시 라벨로 — 권한 2단계 용어(담당자/책임자) 통일(2026-08-05).
    payload["role_label"] = ACCESS_LEVEL_LABEL.get(str(payload.get("access_level")), "담당자")
    payload["is_active"] = bool(payload.get("is_active"))
    return payload


def recipe_label(row: dict[str, Any]) -> str:
    return str(row.get("product_name") or "-")
