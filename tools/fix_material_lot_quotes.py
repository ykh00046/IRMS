"""자재 LOT 앞뒤에 붙은 작은따옴표·공백을 찾아 고친다(운영 서버 API 경유).

배경(2026-09-09): 엑셀에서 LOT 을 복사해 붙일 때 앞자리 0 을 지키려는 작은따옴표
("'0029227498")가 그대로 저장돼 LOT 이력에서 같은 LOT 이 둘로 갈라졌다. 6건 발견.

사용:
  .venv\\Scripts\\python tools\\fix_material_lot_quotes.py            # 점검만(기본)
  .venv\\Scripts\\python tools\\fix_material_lot_quotes.py --apply    # 책임자 로그인 후 수정
옵션:
  --api http://192.168.11.194:9000   운영 서버(기본은 IRMS_API_URL 또는 위 주소)
  --user admin --password ...        책임자 계정. 환경변수 IRMS_MANAGER_PASSWORD 도 된다.
                                     비번을 안 주면 묻지 않고 안내만 하고 끝난다.

수정은 화면의 '수정'과 같은 PUT /api/blend/records/{id} 경로를 쓴다. 따라서 감사 로그에
남고, 제품 LOT·서명·생성 정보는 보존되며 허용 편차 검증도 그대로 받는다.
"""
from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_API = os.environ.get("IRMS_API_URL", "http://192.168.11.194:9000")
QUOTES = "'’‘\""
DETAIL_KEYS = (
    "material_id", "material_code", "material_name", "ratio", "theory_amount",
    "actual_amount", "sequence_order", "manual_entry", "carried_over",
)


def clean_lot(value: str) -> str:
    return value.strip().strip(QUOTES).strip()


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


def scan(api: Api) -> list[dict]:
    windows = [("2000-01-01", "2025-06-30"), ("2025-07-01", "2025-12-31"),
               ("2026-01-01", "2026-06-30"), ("2026-07-01", "2099-12-31")]
    ids: set[int] = set()
    for start, end in windows:
        q = urllib.parse.urlencode({"start_date": start, "end_date": end, "limit": 1000, "include_canceled": "true"})
        for row in api.call("GET", f"/api/blend/records?{q}").get("items") or []:
            ids.add(int(row["id"]))
    found: list[dict] = []
    for rid in sorted(ids):
        rec = api.call("GET", f"/api/blend/records/{rid}")
        for item in rec.get("details") or []:
            lot = str(item.get("material_lot") or "")
            if lot and clean_lot(lot) != lot:
                found.append({
                    "record_id": rid, "product_lot": rec.get("product_lot"), "work_date": rec.get("work_date"),
                    "status": rec.get("status"), "material": item.get("material_name"),
                    "lot": lot, "fixed": clean_lot(lot),
                })
    return found


def apply_fix(api: Api, target: dict) -> list[tuple]:
    rid = target["record_id"]
    before = api.call("GET", f"/api/blend/records/{rid}")
    details = []
    for d in before["details"]:
        lot = d.get("material_lot")
        if d.get("material_name") == target["material"] and lot == target["lot"]:
            lot = target["fixed"]
        row = {k: d.get(k) for k in DETAIL_KEYS}
        row["material_lot"] = lot
        details.append(row)
    body = {
        "recipe_id": before.get("recipe_id"), "product_name": before["product_name"],
        "worker": before["worker"], "work_date": before["work_date"], "work_time": before.get("work_time"),
        "total_amount": before["total_amount"], "scale": before.get("scale"), "note": before.get("note"),
        "reactor": before.get("reactor"), "details": details,
    }
    api.call("PUT", f"/api/blend/records/{rid}", body)
    after = api.call("GET", f"/api/blend/records/{rid}")
    diffs: list[tuple] = []
    for a, b in zip(before["details"], after["details"]):
        for k in ("material_name", "material_lot", "ratio", "theory_amount", "actual_amount", "manual_entry", "loss_comp_g"):
            if a.get(k) != b.get(k):
                diffs.append((a.get("material_name"), k, a.get(k), b.get(k)))
    return diffs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--api", default=DEFAULT_API)
    parser.add_argument("--apply", action="store_true", help="실제로 수정한다(기본은 점검만)")
    parser.add_argument("--user", default="admin")
    parser.add_argument("--password", default=None)
    args = parser.parse_args()

    api = Api(args.api)
    try:
        api.call("GET", "/api/blend/records?limit=1")
    except (urllib.error.URLError, OSError) as exc:
        print(f"서버에 연결하지 못했습니다: {args.api} ({exc})")
        return 2

    found = scan(api)
    if not found:
        print("따옴표·공백이 붙은 자재 LOT 이 없습니다.")
        return 0
    print(f"발견 {len(found)}건")
    for t in found:
        print(f"  {t['work_date']} {t['product_lot']} (#{t['record_id']}, {t['status']}) {t['material']}: {t['lot']!r} -> {t['fixed']}")
    if not args.apply:
        print("\n점검만 했습니다. 고치려면 --apply 를 붙여 다시 실행하세요.")
        return 0

    # Windows 의 getpass 는 콘솔을 직접 읽어 창 없는 실행에서 멈춘다(2026-09-10). 묻지 않는다.
    password = args.password or os.environ.get("IRMS_MANAGER_PASSWORD") or ""
    if not password:
        print("책임자 비밀번호가 필요합니다.")
        print("  IRMS_MANAGER_PASSWORD=비밀번호 .venv/Scripts/python tools/fix_material_lot_quotes.py --apply")
        return 4
    try:
        api.call("POST", "/api/auth/management-login", {"username": args.user, "password": password})
    except urllib.error.HTTPError as exc:
        print(f"책임자 로그인 실패 ({exc.code}).")
        return 3

    failed = 0
    for t in found:
        try:
            diffs = apply_fix(api, t)
        except urllib.error.HTTPError as exc:
            failed += 1
            print(f"  실패 {t['product_lot']}: {exc.code} {exc.read().decode('utf-8', 'replace')[:200]}")
            continue
        extra = [d for d in diffs if d[1] != "material_lot"]
        note = "" if not extra else f"  주의: LOT 외 변경 {extra}"
        print(f"  수정 {t['product_lot']} {t['material']}: {t['lot']!r} -> {t['fixed']}{note}")
    remaining = scan(api)
    print(f"\n남은 건수: {len(remaining)} · 실패: {failed}")
    return 1 if (remaining or failed) else 0


if __name__ == "__main__":
    sys.exit(main())
