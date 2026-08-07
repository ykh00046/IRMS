"""작업자 명단 라우트.

접근: 조회·등록은 무로그인 개방(현장에서 이름 입력). 명단 정리(비활성/이름변경)는
관리자(admin) 전용.

Endpoints:
    GET    /workers                 활성 작업자 목록 (무로그인)
    POST   /workers                 이름 등록(처음 보는 이름) (무로그인)
    GET    /workers/all             전체 목록 (admin, 정리용)
    PATCH  /workers/{id}            비활성/이름변경 (admin)
"""

import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from ..auth import get_current_user, has_access_level, require_access_level
from ..db import get_db, utc_now_text, write_audit_log
from ..limiter import limiter
from ..security import hash_password
from ..services import worker_service
from .models import WorkerCreateBody, WorkerManagerBody, WorkerUpdateBody


def build_router() -> tuple[APIRouter, APIRouter]:
    open_router = APIRouter()
    admin_router = APIRouter(dependencies=[Depends(require_access_level("manager"))])

    @open_router.get("/workers")
    def list_workers(connection: sqlite3.Connection = Depends(get_db)) -> dict[str, Any]:
        return {"items": worker_service.list_workers(connection)}

    @open_router.post("/workers")
    @limiter.limit("10/minute")
    def register_worker(
        request: Request,
        body: WorkerCreateBody,
        connection: sqlite3.Connection = Depends(get_db),
    ) -> dict[str, Any]:
        # category 검증 — None(생략) 은 미지정. 값이 있으면 허용 파트만(PATCH 와 동일).
        category = None
        if body.category is not None:
            clean = body.category.strip()
            if clean and clean not in ("약품", "합성", "잉크", "용수"):
                raise HTTPException(
                    status_code=400,
                    detail="분류는 약품·합성·잉크·용수 중 하나이거나 빈 값이어야 합니다.",
                )
            category = clean or None
        # 되살리기는 책임자만 — 이 라우트는 배합 로그인이 무인증으로 부른다(위 주석 참조).
        actor = get_current_user(request, required=False)
        is_manager = bool(actor) and has_access_level(actor, "manager")
        try:
            result = worker_service.register(
                connection, body.name, utc_now_text(), category=category,
                allow_reactivate=is_manager,
            )
        except worker_service.InactiveWorkerError as exc:
            raise HTTPException(
                status_code=403,
                detail=f"'{exc.name}' 님은 명단에서 내려간 담당자입니다. "
                       "책임자에게 다시 등록을 요청하세요.",
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        if result["created"] or result.get("reactivated"):
            write_audit_log(
                connection,
                action="worker_register" if result["created"] else "worker_reactivated",
                actor=actor,
                target_type="worker",
                target_label=result["name"],
                details={"category": result.get("category")},
            )
        connection.commit()
        return result

    @admin_router.get("/workers/all")
    def list_all_workers(connection: sqlite3.Connection = Depends(get_db)) -> dict[str, Any]:
        return {"items": worker_service.list_workers(connection, active_only=False)}

    @admin_router.patch("/workers/{worker_id}")
    def update_worker(
        worker_id: int,
        body: WorkerUpdateBody,
        request: Request,
        connection: sqlite3.Connection = Depends(get_db),
    ) -> dict[str, Any]:
        rename_result = None
        if body.name is not None:
            try:
                rename_result = worker_service.rename(connection, worker_id, body.name)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc))
            except sqlite3.IntegrityError:
                raise HTTPException(
                    status_code=400, detail="이미 같은 이름의 담당자가 있습니다."
                )
        if body.is_active is not None:
            # 마지막 책임자 보호 — 비활성화하면 관리 권한자가 0명이 되어 레시피·기록 관리와
            # 사용자 관리가 통째로 잠긴다(복구는 레거시 admin 비밀번호를 아는 경우뿐).
            # users 테이블 쪽에는 이미 같은 보호가 있는데 workers 쪽만 비어 있었다.
            if not body.is_active:
                # 본인 비활성화 차단 — 누르는 즉시 자기 세션이 죽고(auth 가 is_active 를 본다)
                # 로그인 화면으로 튕겨 스스로는 되돌릴 수 없다. 해제에만 있던 본인 보호를
                # 비활성화에도 맞춘다(2026-08-08 감사).
                current = get_current_user(request, required=False)
                if (
                    current
                    and current.get("is_worker_manager")
                    and int(current["id"]) == int(worker_id)
                ):
                    raise HTTPException(
                        status_code=400,
                        detail="본인 계정은 비활성화할 수 없습니다. "
                               "다른 책임자에게 요청하세요.",
                    )
                target = worker_service.get_worker(connection, worker_id)
                if target and target.get("is_manager"):
                    if worker_service.active_manager_count(connection) <= 1:
                        raise HTTPException(
                            status_code=400,
                            detail="마지막 책임자는 비활성화할 수 없습니다. "
                                   "다른 담당자를 먼저 책임자로 지정하세요.",
                        )
            worker_service.set_active(connection, worker_id, body.is_active)
        # 분류(파트) 처리 — None 은 "변경 안 함", 빈 문자열은 미지정(NULL) 해제.
        # worker_service.set_category 가 실제 갱신을 수행한다.
        if body.category is not None:
            clean = body.category.strip()
            if clean and clean not in ("약품", "합성", "잉크", "용수"):
                raise HTTPException(
                    status_code=400,
                    detail="분류는 약품·합성·잉크·용수 중 하나이거나 빈 값이어야 합니다.",
                )
            worker_service.set_category(connection, worker_id, clean or None)
        write_audit_log(
            connection,
            action="worker_update",
            actor=get_current_user(request, required=False),
            target_type="worker",
            target_id=str(worker_id),
            details={
                "name": body.name,
                "is_active": body.is_active,
                # 분류(파트) — 요청된 원문(body.category). None=변경 없음, ""=해제.
                "category": body.category,
                # 이름 변경 시 과거 배합 기록 동기화 건수(rename 이 함께 갱신)
                **({"records_updated": rename_result["records_updated"],
                    "old_name": rename_result["old"]} if rename_result else {}),
            },
        )
        connection.commit()
        return {"updated": worker_id}

    def _worker_or_404(connection: sqlite3.Connection, worker_id: int) -> dict[str, Any]:
        worker = worker_service.get_worker(connection, worker_id)
        if not worker:
            raise HTTPException(status_code=404, detail="WORKER_NOT_FOUND")
        return worker

    @admin_router.post("/workers/{worker_id}/manager")
    def grant_manager(
        worker_id: int,
        body: WorkerManagerBody,
        request: Request,
        connection: sqlite3.Connection = Depends(get_db),
    ) -> dict[str, Any]:
        """이용자를 책임자로 지정(개인 비밀번호 설정)."""
        worker = _worker_or_404(connection, worker_id)
        worker_service.set_manager(connection, worker_id, hash_password(body.password))
        write_audit_log(
            connection, action="worker_manager_granted",
            actor=get_current_user(request, required=False),
            target_type="worker", target_id=str(worker_id), target_label=worker["name"],
        )
        connection.commit()
        return {"ok": True}

    @admin_router.post("/workers/{worker_id}/manager/password")
    def reset_manager_password(
        worker_id: int,
        body: WorkerManagerBody,
        request: Request,
        connection: sqlite3.Connection = Depends(get_db),
    ) -> dict[str, Any]:
        worker = _worker_or_404(connection, worker_id)
        if not worker["is_manager"]:
            raise HTTPException(status_code=400, detail="NOT_A_MANAGER")
        worker_service.reset_manager_password(connection, worker_id, hash_password(body.password))
        write_audit_log(
            connection, action="worker_manager_password_reset",
            actor=get_current_user(request, required=False),
            target_type="worker", target_id=str(worker_id), target_label=worker["name"],
        )
        connection.commit()
        return {"ok": True}

    @admin_router.delete("/workers/{worker_id}/manager")
    def revoke_manager(
        worker_id: int,
        request: Request,
        connection: sqlite3.Connection = Depends(get_db),
    ) -> dict[str, Any]:
        """책임자 해제 — 다시 이름만 쓰는 이용자로. 본인 계정은 해제 불가."""
        worker = _worker_or_404(connection, worker_id)
        current = get_current_user(request, required=False)
        if current and current.get("is_worker_manager") and int(current["id"]) == int(worker_id):
            raise HTTPException(status_code=400, detail="CANNOT_REVOKE_SELF")
        # 마지막 책임자 보호 — 비활성화·삭제에는 있는데 해제에만 빠져 있었다. 레거시 admin
        # 으로 로그인하면 워커 책임자를 전원 해제할 수 있었고(2026-08-08 재현: 마지막 1명
        # 해제가 200), 그러면 이름 기반 책임자는 0명이 되어 admin 비밀번호에만 매달리게 된다.
        if worker.get("is_manager") and worker_service.active_manager_count(connection) <= 1:
            raise HTTPException(
                status_code=400,
                detail="마지막 책임자는 해제할 수 없습니다. "
                       "다른 담당자를 먼저 책임자로 지정하세요.",
            )
        worker_service.revoke_manager(connection, worker_id)
        write_audit_log(
            connection, action="worker_manager_revoked",
            actor=current,
            target_type="worker", target_id=str(worker_id), target_label=worker["name"],
        )
        connection.commit()
        return {"ok": True}

    @admin_router.delete("/workers/{worker_id}")
    def delete_worker(
        worker_id: int,
        request: Request,
        connection: sqlite3.Connection = Depends(get_db),
    ) -> dict[str, Any]:
        """명단에서 완전 삭제(오타 정리용). 책임자·배합 기록 있는 이름은 막고 비활성화 안내."""
        worker = _worker_or_404(connection, worker_id)
        if worker["is_manager"]:
            raise HTTPException(status_code=400, detail="CANNOT_DELETE_MANAGER")
        if worker_service.has_blend_records(connection, worker["name"]):
            raise HTTPException(status_code=400, detail="HAS_RECORDS")
        worker_service.delete_worker(connection, worker_id)
        write_audit_log(
            connection, action="worker_deleted",
            actor=get_current_user(request, required=False),
            target_type="worker", target_id=str(worker_id), target_label=worker["name"],
        )
        connection.commit()
        return {"ok": True}

    return open_router, admin_router
