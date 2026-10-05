"""점도 반제품 코드가 레시피 제품명과 공백만 달라 배합 기록이 숨은 경우를 찾아 고친다.

배경(2026-10-05): 배합 기록과 점도 제품은 제품명을 글자 그대로 비교해 묶는다. 레시피
제품명은 '6-1 TOP'(공백 있음)인데 점도 Excel 임포트가 공백을 모두 지워 점도 제품 코드가
'6-1TOP' 으로 만들어졌다. 그래서 배합 기록 67건이 점도 화면과 트레이 알림에서 보이지
않았다. 코드를 레시피 제품명과 같게 고치면 그 기록들이 '미등록'으로 드러난다.

사용:
  .venv\\Scripts\\python tools\\fix_viscosity_product_code.py            # 점검만(기본)
  .venv\\Scripts\\python tools\\fix_viscosity_product_code.py --apply    # 책임자 로그인 후 수정
옵션:
  --api http://192.168.11.194:9000   운영 서버(기본은 IRMS_API_URL 또는 위 주소)
  --user admin --password ...        책임자 계정. 환경변수 IRMS_MANAGER_PASSWORD 도 된다.
                                     비번을 안 주면 묻지 않고 안내만 하고 끝난다.

수정은 PUT /api/viscosity/products/{id}/code 경로를 쓴다. 감사 로그에 남고, 측정 기록은
제품 번호로 묶여 있어 그대로 따라온다. 공백을 지운 비교로 레시피가 둘 이상 겹치면
어느 쪽인지 정할 수 없으므로 목록만 보여 주고 고치지 않는다.
"""
from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_API = os.environ.get("IRMS_API_URL", "http://192.168.11.194:9000")


def _key(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "")).upper()


def find_mismatches(products: list[dict], recipe_names) -> tuple[list[dict], list[dict]]:
    """레시피 제품명과 정확히 같지 않은 활성 점도 제품을 찾는다(서버 없이 판정).

    반환: (fixes, ambiguous)
      fixes     - 공백·대소문자만 다른 레시피가 하나뿐인 제품. new_code 로 고치면 된다.
      ambiguous - 같은 비교 키의 레시피가 둘 이상이라 고를 수 없는 제품.
    레시피와 비교 키가 전혀 안 맞는 제품은 이 도구의 대상이 아니라 어느 쪽에도 넣지 않는다.
    """
    names = {str(n) for n in recipe_names if n}
    by_key: dict[str, set[str]] = {}
    for name in names:
        by_key.setdefault(_key(name), set()).add(name)
    fixes: list[dict] = []
    ambiguous: list[dict] = []
    for product in products:
        if not product.get("is_active", True):
            continue
        code = str(product.get("code") or "")
        if not code or code in names:
            continue
        candidates = sorted(by_key.get(_key(code), set()))
        if len(candidates) == 1:
            fixes.append({
                "product_id": int(product["id"]), "code": code,
                "name": product.get("name"), "new_code": candidates[0],
            })
        elif len(candidates) > 1:
            ambiguous.append({
                "product_id": int(product["id"]), "code": code,
                "name": product.get("name"), "candidates": candidates,
            })
    return fixes, ambiguous


class Api:
    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def csrf(self) -> str:
        for cookie in self.jar:
            if cookie.name == "csrftoken":
                return cookie.value
        return ""

    def call(self, method: str, path: str, body=None):
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        headers = {"content-type": "application/json; charset=utf-8"}
        if method != "GET":
            headers["x-csrftoken"] = self.csrf()
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
        with self.opener.open(req, timeout=60) as resp:
            return json.load(resp)


def _items(payload) -> list[dict]:
    if isinstance(payload, list):
        return payload
    return list((payload or {}).get("items") or [])


def scan(api: Api) -> tuple[list[dict], list[dict]]:
    products = _items(api.call("GET", "/api/viscosity/products"))
    recipes = _items(api.call("GET", "/api/blend/recipes"))
    fixes, ambiguous = find_mismatches(products, [r.get("product_name") for r in recipes])
    for fix in fixes:
        q = urllib.parse.urlencode({"product": fix["new_code"], "limit": 1})
        body = api.call("GET", f"/api/blend/records?{q}")
        total = body.get("total_available")
        if total is None:
            total = body.get("total")
        fix["records"] = total
    return fixes, ambiguous


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--api", default=DEFAULT_API)
    parser.add_argument("--apply", action="store_true", help="실제로 수정한다(기본은 점검만)")
    parser.add_argument("--user", default="admin")
    parser.add_argument("--password", default=None)
    args = parser.parse_args()

    api = Api(args.api)
    try:
        api.call("GET", "/api/viscosity/products")
    except (urllib.error.URLError, OSError) as exc:
        print(f"서버에 연결하지 못했습니다: {args.api} ({exc})")
        return 2

    fixes, ambiguous = scan(api)
    for a in ambiguous:
        print(f"  판정 불가 #{a['product_id']} {a['code']!r}: 레시피 후보 {a['candidates']} (고치지 않음)")
    if not fixes:
        print("레시피 제품명과 공백만 다른 점도 제품이 없습니다.")
        return 0
    print(f"발견 {len(fixes)}건")
    for f in fixes:
        print(f"  #{f['product_id']} {f['code']!r} (이름 {f['name']!r}) -> {f['new_code']!r}"
              f"  고치면 드러나는 배합 기록 {f['records']}건")
    if not args.apply:
        print("\n점검만 했습니다. 고치려면 --apply 를 붙여 다시 실행하세요.")
        return 0

    # Windows 의 getpass 는 콘솔을 직접 읽어 창 없는 실행에서 멈춘다(2026-09-10). 묻지 않는다.
    password = args.password or os.environ.get("IRMS_MANAGER_PASSWORD") or ""
    if not password:
        print("책임자 비밀번호가 필요합니다.")
        print("  IRMS_MANAGER_PASSWORD=비밀번호 .venv/Scripts/python tools/fix_viscosity_product_code.py --apply")
        return 4
    try:
        api.call("POST", "/api/auth/management-login", {"username": args.user, "password": password})
    except urllib.error.HTTPError as exc:
        print(f"책임자 로그인 실패 ({exc.code}).")
        return 3

    failed = 0
    for f in fixes:
        try:
            result = api.call("PUT", f"/api/viscosity/products/{f['product_id']}/code", {"code": f["new_code"]})
        except urllib.error.HTTPError as exc:
            failed += 1
            print(f"  실패 #{f['product_id']} {f['code']!r}: {exc.code} {exc.read().decode('utf-8', 'replace')[:200]}")
            continue
        print(f"  수정 #{f['product_id']} {f['code']!r} -> 코드 {result.get('code')!r}, 이름 {result.get('name')!r}")
    remaining, _ambiguous = scan(api)
    print(f"\n남은 건수: {len(remaining)} · 실패: {failed}")
    return 1 if (remaining or failed) else 0


if __name__ == "__main__":
    sys.exit(main())
