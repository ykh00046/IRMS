"""Manager-scope recipe import endpoints (Excel/TSV bulk import).

Provides 2 endpoints for previewing parsed import payloads and committing
them. Duplicate detection by SHA-256 hash of raw_text; the `force` flag
bypasses it and `revision_of` marks the new batch as a revision chain.

Split from src/routers/recipe_routes.py during the split-large-files
PDCA cycle (2026-05).

Endpoints:
    POST   /recipes/import/preview
    POST   /recipes/import
"""

import hashlib
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from ..auth import get_current_user, require_access_level
from ..db import get_connection, normalize_token, utc_now_text, write_audit_log
from ..services.import_parser import parse_import_text
from ..services.recipe_helpers import resolve_chain_tip
from .item_code_routes import _PRODUCT_CODE_PATTERN, _revision_chain_ids
from .models import ImportRequest, actor_name


def _normalize_explicit_product_code(raw: Any) -> str | None:
    """code-edit-relocate §3: 요청 본문 product_code 정규화·검증.

    반환:
        None  → 명시 값 없음(빈 문자열 또는 None). 자동 인식·승계 경로 유지.
        str   → strip+upper 한 코드.

    item_code_routes 의 완화 형식(^[A-Z]{2}[A-Z0-9]{2,8}$)에 맞지 않으면 400.
    PUT /recipes/{id}/product-code 의 _validate_code 와 동일 규칙.
    """
    if raw is None:
        return None
    text = str(raw).strip()
    if text == "":
        return None
    code = text.upper()
    if not _PRODUCT_CODE_PATTERN.match(code):
        raise HTTPException(
            status_code=400,
            detail="반제품 품목코드 형식이 올바르지 않습니다. (영문 1~2자로 시작 + 영문/숫자 2~8자, 예: B0082, BC1234)",
        )
    return code


def _resolve_product_code(
    connection, product_name: str
) -> tuple[str | None, str | None]:
    """item-code P3: 반제품명 → kind='product' 마스터 단일 히트 매칭.

    반환: (product_code, category_hint). 마스터가 비어 있거나 모호(2코드 이상)·미매칭이면
    (None, None) — 이때 수정 등록이면 부모 product_code 를 승계한다(호출부에서 처리).
    match_item_codes.py(P2) 의 레시피 매칭과 동일 규칙(정규화 단일 히트).
    """
    rows = connection.execute(
        "SELECT code, category_hint FROM item_code_master "
        "WHERE kind='product' AND name=? LIMIT 2",
        (product_name,),
    ).fetchall()
    if len(rows) == 1:
        r = rows[0]
        return r["code"], r["category_hint"]
    # 정규화 매칭 폴백: DB 에 name 이 정확히 일치하지 않아도 정규화 토큰이 같으면 히트.
    # (마스터 임포트 시 name 이 원문 그대로 들어가므로 보통 위 쿼리로 충분하지만,
    #  표기 차(공백/대소문자) 대비 폴백.)
    if not rows:
        token = normalize_token(product_name)
        if not token:
            return None, None
        norm_rows = connection.execute(
            "SELECT code, name, category_hint FROM item_code_master WHERE kind='product'"
        ).fetchall()
        hits = [r for r in norm_rows if normalize_token(r["name"]) == token]
        if len(hits) == 1:
            return hits[0]["code"], hits[0]["category_hint"]
    return None, None


def build_router() -> APIRouter:
    router = APIRouter(dependencies=[Depends(require_access_level("manager"))])

    @router.post("/recipes/import/preview")
    def import_preview(body: ImportRequest) -> dict[str, Any]:
        # 감사 F-3: 미리보기는 무부작용이어야 한다. parse_import_text 는 미등록 자재를
        # 그 자리에서 INSERT 하는데(파싱·등록 결합), sqlite3 Connection 을 `with` 로
        # 감싸면 정상 종료 시 commit 되어 미리보기만으로 자재가 영구 등록됐다.
        # → 명시적 rollback 으로 INSERT 를 폐기한다. 실제 등록은 /recipes/import 만.
        #   (응답의 material_id 는 표시용 임시값 — mappers.js mapPreview 참조)
        connection = get_connection()
        try:
            result = parse_import_text(
                connection, body.raw_text, body.allow_unknown_materials
            )
        finally:
            connection.rollback()
            connection.close()
        return result

    @router.post("/recipes/import")
    def import_recipes(
        body: ImportRequest,
        request: Request,
    ) -> dict[str, Any]:
        current_user = get_current_user(request)
        creator_name = actor_name(current_user)
        with get_connection() as connection:
            parsed = parse_import_text(
                connection, body.raw_text, body.allow_unknown_materials
            )
            if parsed["errors"]:
                raise HTTPException(status_code=400, detail={"errors": parsed["errors"]})

            created_ids = []
            now = utc_now_text()
            effective_from = (body.effective_from or "").strip() or now[:10]
            raw_hash = hashlib.sha256(body.raw_text.encode()).hexdigest()

            if not body.force and body.revision_of is None:
                existing = connection.execute(
                    "SELECT id, product_name, created_at FROM recipes WHERE raw_input_hash = ? AND revision_of IS NULL LIMIT 5",
                    (raw_hash,),
                ).fetchall()
                if existing:
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "code": "DUPLICATE_IMPORT",
                            "message": "동일한 내용이 이미 등록되어 있습니다. 다시 등록하려면 force 옵션을 사용하세요.",
                            "existing": [
                                {"id": r["id"], "product_name": r["product_name"], "created_at": r["created_at"]}
                                for r in existing
                            ],
                        },
                    )

            # GAP 2: 동시 수정 등록 레이스(체인 분기) 방지 — 부모가 여전히 체인의 현재
            # 버전(tip)일 때만 개정을 허용한다. 다른 책임자가 먼저 개정해 tip 이 이동했으면
            # (resolve_chain_tip(parent) != parent) 409 로 거부한다. 배합 저장 409 와 동일한
            # 낙관적 잠금 규칙(recipe_helpers.resolve_chain_tip 단일 소스).
            if body.revision_of is not None:
                if resolve_chain_tip(connection, int(body.revision_of)) != int(body.revision_of):
                    raise HTTPException(
                        status_code=409,
                        detail="레시피가 방금 개정되었습니다 — 새로고침 후 최신 버전에서 다시 수정 등록하세요.",
                    )

            # 수정 등록(revision)이면 원본 체인의 DHR 전용 여부·기준 배합량을 승계한다.
            # 승계하지 않으면 새 버전이 일반 레시피가 되어 배합 화면에 노출되는 회귀 발생.
            inherited_is_dhr = 0
            totals = body.base_totals or ([body.base_total] if body.base_total else None)
            # 기준 자재 승계용 — 요청에 anchor_material 이 없으면 부모의 anchor_material_id 를
            # 새 버전의 자재 중 일치하는 것이 있을 때만 물려받는다(없으면 NULL).
            inherited_anchor_material_id: int | None = None
            # 레시피별 허용 편차 승계용 — 요청에 tolerance_g 가 없으면 부모의 tolerance_g 를
            # 그대로 물려받는다(base_totals / anchor_material_id 승계 구조와 동일).
            inherited_tolerance_g: float | None = None
            # 분류(약품/합성/잉크/용수) 승계 — 수정 등록 때마다 분류가 미분류로 리셋되던
            # 문제 수정(2026-07-16). 부모의 category 를 그대로 물려받는다.
            inherited_category: str | None = None
            # item-code P3: 반제품 코드(product_code) 승계 — 분류 승계와 같은 자리에서.
            # 마스터 매칭 실패 시에도 부모 값을 유지; 매칭 성공하면 그 값으로 덮는다(아래 per-row).
            inherited_product_code: str | None = None
            # reactor-ownership: 반응기 여부 승계 — tolerance_g/category 승계와 같은 자리에서.
            # body.use_reactor 가 명시되지 않았을 때만 부모의 use_reactor 를 물려받는다.
            inherited_use_reactor: int = 0
            # 파생(derived) 승계 — use_reactor 승계와 동일 구조. body.is_derived 미지정 시
            # 부모의 is_derived 를 물려받는다(파생 여부는 반응기와 독립적으로 승계).
            inherited_is_derived: int = 0
            # 1차→2차 연계 승계 — body.stage1_recipe_id 미지정 시 부모의 stage1_recipe_id 를 물려받는다
            # (anchor_material_id 승계와 동일 구조 — nullable 정수).
            inherited_stage1_recipe_id: int | None = None
            if body.revision_of is not None:
                parent_row = connection.execute(
                    "SELECT COALESCE(is_dhr, 0) AS is_dhr, base_total, base_totals, "
                    "anchor_material_id, tolerance_g, category, product_code, "
                    "COALESCE(use_reactor, 0) AS use_reactor, "
                    "COALESCE(is_derived, 0) AS is_derived, "
                    "stage1_recipe_id "
                    "FROM recipes WHERE id = ?",
                    (body.revision_of,),
                ).fetchone()
                if parent_row:
                    inherited_is_dhr = int(parent_row["is_dhr"])
                    if totals is None:
                        if parent_row["base_totals"]:
                            totals = [
                                float(t) for t in str(parent_row["base_totals"]).split(",") if t.strip()
                            ]
                        elif parent_row["base_total"]:
                            totals = [float(parent_row["base_total"])]
                    if parent_row["anchor_material_id"] is not None:
                        inherited_anchor_material_id = int(parent_row["anchor_material_id"])
                    if parent_row["tolerance_g"] is not None:
                        try:
                            inherited_tolerance_g = float(parent_row["tolerance_g"])
                        except (TypeError, ValueError):
                            inherited_tolerance_g = None
                    if parent_row["category"]:
                        inherited_category = str(parent_row["category"])
                    if parent_row["product_code"]:
                        inherited_product_code = str(parent_row["product_code"])
                    inherited_use_reactor = int(parent_row["use_reactor"])
                    inherited_is_derived = int(parent_row["is_derived"])
                    if parent_row["stage1_recipe_id"] is not None:
                        inherited_stage1_recipe_id = int(parent_row["stage1_recipe_id"])
            # reactor-ownership: 명시 값 우선, 없으면 승계값(비개정 신규면 0 유지).
            effective_use_reactor = 1 if body.use_reactor else (1 if inherited_use_reactor else 0)
            # 파생(derived): 명시 값 우선, 없으면 승계값(비개정 신규면 0 유지) — use_reactor 와 동일.
            effective_is_derived = 1 if body.is_derived else (1 if inherited_is_derived else 0)
            # 1차→2차 연계: 명시 값 우선, 없으면 부모 승계(비개정 신규면 None).
            effective_stage1_recipe_id = body.stage1_recipe_id if body.stage1_recipe_id is not None else inherited_stage1_recipe_id
            # GAP 4: stage1 연계 참조 무결성 — 지정/승계된 1차 레시피가 실제로 존재해야 한다.
            # 없으면 400(댕글링 링크 차단). PUT /stage1 의 대상 존재 검증과 동일 규칙.
            if effective_stage1_recipe_id is not None:
                stage1_exists = connection.execute(
                    "SELECT 1 FROM recipes WHERE id = ? LIMIT 1",
                    (int(effective_stage1_recipe_id),),
                ).fetchone()
                if not stage1_exists:
                    raise HTTPException(
                        status_code=400,
                        detail="지정한 1차 레시피를 찾을 수 없습니다.",
                    )
            # 요청의 tolerance_g 가 지정되면 그것을 우선(base_totals·anchor 와 동일한 우선순위).
            effective_tolerance_g = body.tolerance_g
            if effective_tolerance_g is None:
                effective_tolerance_g = inherited_tolerance_g
            # code-edit-relocate §3: 명시 product_code 가 자동 인식·승계보다 우선.
            # 값이 있으면 strip+upper + 형식 검사(불일치 400). 다른 체인이 같은 코드를
            # 쓰고 있으면 409(반제품명 포함) — PUT product-code 와 동일 규칙.
            # 수정 등록(revision_of) 시 부모 체인은 같은 코드를 공유하므로, 체인 id 를
            # 충돌 조회에서 제외한다(BUG 1: 자기 부모 코드로 자신이 409 되는 회귀 방지).
            explicit_product_code = _normalize_explicit_product_code(body.product_code)
            if explicit_product_code is not None:
                # BUG 1: 명시 product_code 는 루프 밖에서 한 번 계산돼 모든 값행에 동일하게
                # 찍힌다. 값행이 2개 이상(서로 다른 반제품)이면 "코드 1개 = 반제품 1개"
                # 불변식이 깨지므로, 명시 코드는 반제품이 정확히 1개일 때만 허용한다.
                if len(parsed["parsed_rows"]) > 1:
                    raise HTTPException(
                        status_code=400,
                        detail="여러 반제품을 한 번에 등록할 때는 품목코드를 비워 두세요(자동 인식/개별 지정).",
                    )
                exclude_clause = ""
                exclude_params: list[Any] = []
                if body.revision_of is not None:
                    chain_ids = _revision_chain_ids(connection, int(body.revision_of))
                    # 부모 체인이 있으면 그 id 들을 제외(자기 체인은 충돌 아님).
                    if chain_ids:
                        placeholders = ",".join("?" for _ in chain_ids)
                        exclude_clause = f" AND id NOT IN ({placeholders})"
                        exclude_params = list(chain_ids)
                conflict_row = connection.execute(
                    f"SELECT product_name FROM recipes "
                    f"WHERE product_code = ?{exclude_clause} LIMIT 1",
                    [explicit_product_code, *exclude_params],
                ).fetchone()
                if conflict_row:
                    raise HTTPException(
                        status_code=409,
                        detail=(
                            f"이미 다른 반제품({conflict_row['product_name']})이"
                            " 사용 중인 코드입니다."
                        ),
                    )
            # 정수는 '.0' 없이 저장(프리필 표시·비교 편의)
            base_totals_text = (
                ",".join(str(int(t)) if float(t) == int(t) else str(t) for t in totals[:3])
                if totals else None
            )

            # BUG 1: 한 임포트 안에서 서로 다른 반제품이 같은 유효 코드로 귀결되면(자동 인식
            # 중복 등) "코드 1개 = 반제품 1개" 불변식 위반이므로 400. DB 충돌 검사는 배치
            # 내부 형제 행을 못 보므로 여기서 별도로 막는다. {유효코드: 최초 반제품명}.
            seen_effective_codes: dict[str, str] = {}

            for parsed_row in parsed["parsed_rows"]:
                # 기준 자재 결정:
                # - 요청에 anchor_material 이 있으면 → 임포트 항목 중 이름이 정확히 일치하는
                #   자재의 id. 일치하는 항목이 없으면 400(잘못된 지정).
                # - 요청에 없고 수정 등록이면 → 부모의 anchor_material_id 를 새 버전 자재 중
                #   여전히 존재할 때만 승계(없으면 NULL). 이것이 base_totals 승계 구조와 동일.
                item_ids = [it["material_id"] for it in parsed_row["items"]]
                item_id_set = set(item_ids)
                anchor_material_id: int | None = None
                if body.anchor_material is not None:
                    anchor_name = body.anchor_material.strip()
                    if anchor_name:
                        # 임포트 항목의 자재 id 로 materials.name 을 조회해 정확히 일치하는 이름을 찾는다.
                        matched_id = None
                        if item_id_set:
                            placeholders = ",".join("?" for _ in item_ids)
                            name_row = connection.execute(
                                f"SELECT id FROM materials WHERE id IN ({placeholders}) "
                                f"AND name = ? LIMIT 1",
                                (*item_ids, anchor_name),
                            ).fetchone()
                            if name_row:
                                matched_id = int(name_row["id"])
                        if matched_id is None:
                            raise HTTPException(
                                status_code=400,
                                detail=(
                                    f"기준 자재 '{anchor_name}'가 임포트 항목에 없습니다. "
                                    "자재 이름을 확인해 주세요."
                                ),
                            )
                        anchor_material_id = matched_id
                elif inherited_anchor_material_id is not None:
                    # 부모의 기준 자재가 새 버전 자재에 여전히 있으면 승계, 아니면 버림(NULL)
                    anchor_material_id = (
                        inherited_anchor_material_id
                        if inherited_anchor_material_id in item_id_set
                        else None
                    )

                # item-code P3 + code-edit-relocate §3: 반제품 코드 결정.
                # - 명시 product_code(요청 본문) → 최우선. 자동 인식·승계 무시.
                # - 명시 값이 없으면 product_name → kind='product' 마스터 단일 히트.
                # - 그래도 없으면 수정 등록 시 부모의 product_code 승계.
                # - category: 마스터 category_hint 를 채움 후보. 단 승계된 category(부모)가 있으면
                #   그것이 우선(이미 사용자가 지정한 값). 둘 다 없을 때만 hint 로 채운다.
                matched_code, matched_hint = _resolve_product_code(
                    connection, parsed_row["product_name"]
                )
                if explicit_product_code is not None:
                    effective_product_code = explicit_product_code
                else:
                    effective_product_code = matched_code or inherited_product_code
                # BUG 1: 배치 내부 코드 중복(서로 다른 반제품 → 동일 유효 코드) 차단.
                if effective_product_code:
                    prior_name = seen_effective_codes.get(effective_product_code)
                    if prior_name is not None and prior_name != parsed_row["product_name"]:
                        raise HTTPException(
                            status_code=400,
                            detail=(
                                f"같은 품목코드({effective_product_code})가 서로 다른 반제품"
                                f"({prior_name}, {parsed_row['product_name']})에 지정되었습니다. "
                                "반제품마다 다른 코드를 쓰거나 코드를 비워 두세요."
                            ),
                        )
                    seen_effective_codes[effective_product_code] = parsed_row["product_name"]
                effective_category = inherited_category
                if effective_category is None and matched_hint:
                    # 신규(비개정) 임포트에서 승계 category 가 없을 때만 hint 로 채움.
                    # match_item_codes.py 와 동일 — 비어있을 때만 채운다(기존값 건드리지 않음).
                    effective_category = matched_hint

                # 등록 즉시 사용 가능(completed). (구) 계량 워크플로의 pending→진행→완료
                # 단계는 /blend 전환으로 폐기 — 승인 단계가 없어 pending 은 영구 정체됨.
                cursor = connection.execute(
                    """
                    INSERT INTO recipes (
                        product_name, position, ink_name, status, created_by, created_at, completed_at,
                        raw_input_hash, raw_input_text, revision_of, remark, effective_from, is_dhr,
                        base_totals, anchor_material_id, tolerance_g, category, product_code, use_reactor,
                        is_derived, stage1_recipe_id
                    ) VALUES (?, ?, ?, 'completed', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        parsed_row["product_name"],
                        parsed_row["position"],
                        # ink 는 폐기 중인 개념 — 열이 없으면(문서상 기본 형식) 반제품명으로 대체.
                        # (ink_name 은 NOT NULL 이라 None 이면 등록이 500 으로 실패했음)
                        parsed_row["ink_name"] or parsed_row["product_name"],
                        creator_name,
                        now,
                        now,
                        raw_hash,
                        body.raw_text,
                        body.revision_of,
                        parsed_row.get("remark"),
                        effective_from,
                        inherited_is_dhr,
                        base_totals_text,
                        anchor_material_id,
                        effective_tolerance_g,
                        effective_category,
                        effective_product_code,
                        effective_use_reactor,
                        effective_is_derived,
                        effective_stage1_recipe_id,
                    ),
                )
                recipe_id = cursor.lastrowid
                created_ids.append(recipe_id)

                for item in parsed_row["items"]:
                    connection.execute(
                        """
                        INSERT INTO recipe_items (recipe_id, material_id, value_weight, value_text)
                        VALUES (?, ?, ?, ?)
                        """,
                        (recipe_id, item["material_id"], item["value_weight"], item["value_text"]),
                    )

                # 공정 설명 줄('설명' 열) — 자재 사이 안내문. 공식 배합일지 출력 미포함.
                for step in parsed_row.get("steps", []):
                    connection.execute(
                        "INSERT INTO recipe_steps (recipe_id, position, note) VALUES (?, ?, ?)",
                        (recipe_id, int(step["position"]), step["note"]),
                    )

            write_audit_log(
                connection,
                action="recipes_imported",
                actor=current_user,
                target_type="recipe_batch",
                target_label=f"{len(created_ids)} recipes",
                details={
                    "created_count": len(created_ids),
                    "created_ids": created_ids,
                    "warnings_count": len(parsed["warnings"]),
                    "raw_hash": raw_hash,
                    "revision_of": body.revision_of,
                },
            )
            connection.commit()

        return {
            "created_count": len(created_ids),
            "created_ids": created_ids,
            "warnings": parsed["warnings"],
        }

    return router
