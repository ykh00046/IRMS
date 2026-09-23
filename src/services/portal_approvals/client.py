"""포털(그룹웨어) 결재 문서 조회 클라이언트 — requests 만 쓴다.

브라우저는 필요 없다(2026-09-22 실제 포털 조사). 로그인은 평범한 폼 POST 이고
세션은 `JSESSIONID` 쿠키 하나다. CSRF 필드는 어디에도 없다.

    1) GET  /portal/main/portalMain.do        JSESSIONID 를 받는다
    2) POST /login.do                          j_username · j_password · j_gwIdCheck
       성공 = portalMain 으로 302, 실패 = 200 인데 최종 URL 이 /loginForm.do?error=1
    3) POST /approval/work/apprlist/listApprDeptOpen.do   부서공개함 목록(HTML)
    4) GET  /approval/work/apprWorkDoc/viewApprDoc.do?apprId=…   본문(HTML)

경계:
  - **자격증명은 로그에도 감사에도 응답에도 싣지 않는다.** 이 모듈은 값을 받아
    쓰기만 하고 어디에도 기록하지 않는다.
  - 목록 조회 범위를 회사 전체로 넓히지 않는다(`searchGroupId` 를 비우면 그렇게
    된다). 수집 계정이 실제로 볼 수 있는 부서 기본값을 그대로 쓴다 — 사용자 결정.
  - 남의 부서 문서는 500 + '접근 권한이 없습니다'다. 그 건은 건너뛰고 세되,
    제목만 보고 값을 지어내지 않는다.
"""

from __future__ import annotations

import logging
from typing import Any

from . import parser

logger = logging.getLogger(__name__)

LOGIN_PAGE_PATH = "/portal/main/portalMain.do"
LOGIN_PATH = "/login.do"
LIST_PATH = "/approval/work/apprlist/listApprDeptOpen.do"
BODY_PATH = "/approval/work/apprWorkDoc/viewApprDoc.do"

LIST_TYPE = "listApprDeptOpen"
# 양식명 기본 검색어. **문서제목이 아니라 양식명으로 찾는다** — 실측(2026-09-23,
# 270일 같은 부서): 문서제목=근태허가원 10건 / 양식명=근태허가원 328건 / 양식명=근태 344건.
# 제목으로 찾으면 344건 중 334건을 놓친다(제목에 '근태허가원'이라는 말이 없는 평범한
# 휴가 결재들). 양식명은 부분일치(LIKE)다.
DEFAULT_FORM_NAME = "근태허가원"
PAGE_SIZE = 50

DEFAULT_TIMEOUT = 20.0


class PortalError(Exception):
    """포털 조회가 끝까지 가지 못했다(수집 실패). 화면에는 사유만 알린다."""


class PortalLoginFailed(PortalError):
    """아이디·비밀번호가 맞지 않거나 로그인 화면이 되돌아왔다."""


class PortalPermissionDenied(PortalError):
    """그 문서를 볼 권한이 없다(다른 부서) — 한 건만 건너뛰면 된다."""


class PortalClient:
    """한 번의 수집 동안 살아 있는 세션. 세션이 끊기면 한 번 다시 로그인한다."""

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        session: Any | None = None,
    ) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self._username = username
        self._password = password
        self.timeout = timeout
        if session is None:
            import requests  # 지연 import — 테스트는 가짜 세션을 넣어 쓴다

            session = requests.Session()
        self.session = session
        self.logged_in = False

    # ── 내부 ────────────────────────────────────────────────────────────
    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    @property
    def _origin(self) -> str:
        return self.base_url

    def login(self) -> None:
        """폼 POST 로그인. 실패 판정은 최종 URL 과 로그인 폼 표식 둘 다로 한다."""
        if not self._username or not self._password:
            raise PortalLoginFailed("PORTAL_CREDENTIALS_MISSING")
        login_page = self._url(LOGIN_PAGE_PATH)
        self.session.get(login_page, timeout=self.timeout)  # JSESSIONID 수령
        response = self.session.post(
            self._url(LOGIN_PATH),
            data={
                "j_username": self._username,
                "j_password": self._password,
                "j_gwIdCheck": "",
            },
            headers={"Referer": login_page, "Origin": self._origin},
            timeout=self.timeout,
            allow_redirects=True,
        )
        final_url = str(getattr(response, "url", "") or "")
        body = getattr(response, "text", "") or ""
        if "loginForm.do" in final_url or parser.looks_like_login_page(body):
            raise PortalLoginFailed("PORTAL_LOGIN_REJECTED")
        self.logged_in = True

    def _ensure_login(self) -> None:
        if not self.logged_in:
            self.login()

    def _request(self, method: str, url: str, **kwargs: Any) -> str:
        """한 번 보내고, 로그인 화면이 돌아오면 다시 로그인해 한 번만 재시도한다."""
        self._ensure_login()
        response = self.session.request(method, url, timeout=self.timeout, **kwargs)
        body = getattr(response, "text", "") or ""
        status = int(getattr(response, "status_code", 200) or 200)
        if parser.looks_like_login_page(body):
            # 유휴 만료 — 세션 하나뿐이라 다시 로그인하면 그대로 이어진다.
            self.logged_in = False
            self.login()
            response = self.session.request(
                method, url, timeout=self.timeout, **kwargs
            )
            body = getattr(response, "text", "") or ""
            status = int(getattr(response, "status_code", 200) or 200)
            if parser.looks_like_login_page(body):
                raise PortalLoginFailed("PORTAL_SESSION_LOST")
        if status >= 500 and parser.looks_like_permission_denied(body):
            raise PortalPermissionDenied("PORTAL_DOC_FORBIDDEN")
        if status >= 400:
            raise PortalError(f"PORTAL_HTTP_{status}")
        return body

    # ── 공개 ────────────────────────────────────────────────────────────
    def fetch_list_page(
        self,
        *,
        start_date: str,
        end_date: str,
        page_index: int,
        form_name: str = DEFAULT_FORM_NAME,
    ) -> str:
        """부서공개함 목록 한 쪽(HTML). 날짜 형식은 포털 그대로 `YYYY.MM.DD`.

        **양식명으로 찾고 문서제목은 비운다**(위 DEFAULT_FORM_NAME 주석의 실측).
        **날짜 필터는 완료일 기준**이다(기안일이 아니다).
        `searchGroupId` 는 보내지 않는다 — 비워 보내면 회사 전체로 넓어진다.
        """
        payload = {
            "listType": LIST_TYPE,
            "searchApprTitle": "",
            "searchUserName": "",
            "searchFormName": form_name,
            "searchStartDate": start_date,
            "searchEndDate": end_date,
            "searchApprDocType": "",
            "searchIsGroupUse": "",
            "pageIndex": str(int(page_index)),
            "pagePerRecord": str(PAGE_SIZE),
            "sortColumn": "apprEndDate",
            "sortType": "DESC",
            "curRowIndex": "0",
            "apprId": "",
            "linkType": "",
            "entrustUserId": "",
            "apprIds": "",
        }
        return self._request(
            "POST",
            self._url(LIST_PATH),
            data=payload,
            headers={"Referer": self._url(LIST_PATH), "Origin": self._origin},
        )

    def fetch_body(self, appr_id: str) -> str:
        """문서 본문(HTML). 남의 부서면 PortalPermissionDenied."""
        return self._request(
            "GET",
            self._url(BODY_PATH),
            params={"apprId": appr_id, "listType": LIST_TYPE},
            headers={"Referer": self._url(LIST_PATH)},
        )
