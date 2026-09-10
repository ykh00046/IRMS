"""자재 LOT 입력 오타를 찾아 일괄로 바로잡는다(운영 서버 API 경유).

배경(2026-09-09): LOT 이력을 보다가 같은 LOT 이 잠깐 다른 값으로 갔다가 되돌아오는
구간이 여럿 나왔다. 예) NVP 0029567994 -> 29567994 -> 0029567994(앞의 00 누락),
L-HEMA 03166002 -> 0366002 -> 03166002(가운데 1 누락). 실제 교체가 아니라 입력 오타라
LOT 이력에 없는 교체가 생기고 역추적이 갈라진다.

찾는 방법
  (제품, 자재)별로 LOT 을 시간순으로 늘어놓고, 앞뒤가 같은 LOT 인데 가운데만 다른 구간
  (blip)을 고른다. 그 값이 앞뒤 LOT 과 한두 글자 차이면 오타 후보다. 방향(무엇이 오타인지)은
  아래 '오타 꼴'로 판정한다. 어느 쪽도 오타 꼴이 아니면 두 LOT 을 번갈아 쓴 것으로 보고 뺀다.

오타 꼴(정타 -> 오타)
  drop-zero   앞의 0 이 빠짐            0029567994 -> 29567994
  drop-digit  가운데 글자 하나 빠짐      03166002  -> 0366002
  add-digit   글자 하나 더 들어감        2024040601 -> 20241040601
  cut-tail    끝의 몇 글자가 빠짐        99509175L0 -> 99509175
  case        대소문자만 다름            5K75      -> 5k75
  typo-1      한 글자만 다름             0029567994 -> 0029567794

등급
  high  오타 LOT 이 전체에서 2회 이하로만 쓰였다. 사실상 확실.
  mid   3~9회. 같은 오타를 며칠 반복한 경우. 확인 후 적용 권장.
  (그 밖의 후보는 실제 교체로 보고 목록에서 뺀다)

사용
  .venv\\Scripts\\python tools\\fix_material_lot_typos.py                 # 점검만(high+mid)
  .venv\\Scripts\\python tools\\fix_material_lot_typos.py --tier high --apply
  .venv\\Scripts\\python tools\\fix_material_lot_typos.py --groups 3,7 --apply
옵션
  --api URL      운영 서버(기본 IRMS_API_URL 또는 http://192.168.11.194:9000)
  --tier         high | mid | all (기본 all = 점검, --apply 시 기본 high)
  --groups       위 목록의 번호만 골라 적용
  --user/--password  책임자 계정. 비번은 환경변수 IRMS_MANAGER_PASSWORD 로 줘도 된다.
                     비번을 안 주면 묻지 않고 안내만 하고 끝난다(창 없는 실행에서 멈추지 않게).
  --cache PATH   기록 상세 캐시 파일(기본 .tmp-tests/lot_typo_cache.json)
  --refresh      캐시를 무시하고 다시 읽는다

수정은 화면의 '수정'과 같은 PUT /api/blend/records/{id} 경로를 쓴다. 감사 로그에 남고,
제품 LOT·서명·이론량은 그대로 유지된다(총량을 바꾸지 않으므로).
"""
from __future__ import annotations

import argparse
import functools
import http.cookiejar
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

print = functools.partial(print, flush=True)  # noqa: A001 - 백그라운드에서도 바로 보이게

DEFAULT_API = os.environ.get("IRMS_API_URL", "http://192.168.11.194:9000")
DEFAULT_CACHE = Path(".tmp-tests/lot_typo_cache.json")
DETAIL_KEYS = (
    "material_id", "material_code", "material_name", "ratio", "theory_amount",
    "actual_amount", "sequence_order", "manual_entry", "carried_over",
)


# ------------------------------------------------------------------ API
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

    def get(self, path: str, **params):
        query = ("?" + urllib.parse.urlencode(params)) if params else ""
        return self.call("GET", path + query)


# ------------------------------------------------------------- 오타 판정
def edit_distance(a: str, b: str) -> int:
    if abs(len(a) - len(b)) > 3:
        return 9
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb))
        prev = cur
    return prev[-1]


def _drop_one(text: str) -> set[str]:
    return {text[:i] + text[i + 1:] for i in range(len(text))}


def typo_kind(correct: str, typo: str) -> str | None:
    """correct 에서 typo 가 나올 수 있는 흔한 실수인가. 아니면 None."""
    if correct == typo:
        return None
    if correct.casefold() == typo.casefold():
        return "case"
    if correct.startswith("0") and typo == correct.lstrip("0") and typo:
        return "drop-zero"
    if len(typo) == len(correct) - 1 and typo in _drop_one(correct):
        return "drop-digit"
    if len(typo) == len(correct) + 1 and correct in _drop_one(typo):
        return "add-digit"
    if len(typo) < len(correct) and correct.startswith(typo) and len(correct) - len(typo) <= 3:
        return "cut-tail"
    if len(typo) == len(correct) and edit_distance(correct, typo) == 1:
        return "typo-1"
    return None


# ------------------------------------------------------------------ 스캔
def load_records(api: Api, cache_path: Path, refresh: bool) -> dict:
    cache: dict = {}
    if cache_path.exists() and not refresh:
        try:
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            cache = {}
    ids: set[int] = set()
    windows = [("2000-01-01", "2025-06-30"), ("2025-07-01", "2025-12-31"),
               ("2026-01-01", "2026-06-30"), ("2026-07-01", "2099-12-31")]
    for start, end in windows:
        page = api.get("/api/blend/records", start_date=start, end_date=end,
                       limit=1000, include_canceled="true")
        for row in page.get("items") or []:
            ids.add(int(row["id"]))
    fetched = 0
    for rid in sorted(ids):
        if str(rid) in cache:
            continue
        rec = api.get(f"/api/blend/records/{rid}")
        cache[str(rid)] = {
            "product_lot": rec.get("product_lot"), "product_name": rec.get("product_name"),
            "work_date": rec.get("work_date"), "status": rec.get("status"),
            "details": [{"material_name": d.get("material_name"), "material_lot": d.get("material_lot")}
                        for d in rec.get("details") or []],
        }
        fetched += 1
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    return cache


def scan(cache: dict) -> list[dict]:
    rows_by_pm: dict[tuple, list] = defaultdict(list)
    global_use: dict[str, dict[str, set]] = defaultdict(lambda: defaultdict(set))
    for rid, rec in cache.items():
        for d in rec["details"]:
            lot = (d["material_lot"] or "").strip()
            if not lot:
                continue
            rows_by_pm[(rec["product_name"], d["material_name"])].append(
                (rec["work_date"], rec["product_lot"], int(rid), lot))
            global_use[d["material_name"]][lot].add(int(rid))

    merged: dict[tuple, dict] = {}
    for (product, material), rows in rows_by_pm.items():
        rows.sort(key=lambda r: (r[0], r[1]))
        runs: list[list] = []
        for row in rows:
            if runs and runs[-1][0] == row[3]:
                runs[-1][1].append(row)
            else:
                runs.append([row[3], [row]])
        for k in range(1, len(runs) - 1):
            typo, before, after = runs[k][0], runs[k - 1][0], runs[k + 1][0]
            if before != after or typo == before:
                continue
            kind = typo_kind(before, typo)
            if kind is None:
                continue                      # 오타 꼴이 아니면 실제 교체로 본다
            if typo_kind(typo, before) is not None and \
                    len(global_use[material][typo]) >= len(global_use[material][before]):
                continue                      # 양방향 모두 오타 꼴이면 덜 쓰인 쪽만 오타
            key = (material, typo, before)
            entry = merged.setdefault(key, {
                "material": material, "typo": typo, "correct": before, "kind": kind,
                "products": set(), "record_ids": set(), "records": {},
            })
            entry["products"].add(product)
            for row in runs[k][1]:
                entry["record_ids"].add(row[2])
                entry["records"][row[2]] = {"record_id": row[2], "product_lot": row[1], "work_date": row[0]}

    out = []
    for entry in merged.values():
        typo_uses = len(global_use[entry["material"]][entry["typo"]])
        correct_uses = len(global_use[entry["material"]][entry["correct"]])
        if typo_uses >= 10 or typo_uses * 3 > correct_uses:
            continue                          # 자주 쓰였으면 실제 LOT 으로 본다
        entry["products"] = sorted(entry["products"])
        entry["record_ids"] = sorted(entry["record_ids"])
        entry["records"] = [entry["records"][i] for i in entry["record_ids"]]
        entry["typo_uses"] = typo_uses
        entry["correct_uses"] = correct_uses
        entry["tier"] = "high" if typo_uses <= 2 else "mid"
        out.append(entry)
    out.sort(key=lambda e: (0 if e["tier"] == "high" else 1, e["material"], e["typo"]))
    return out


# ------------------------------------------------------------------ 적용
def apply_group(api: Api, group: dict) -> list[tuple]:
    problems: list[tuple] = []
    for rid in group["record_ids"]:
        before = api.get(f"/api/blend/records/{rid}")
        details, changed = [], 0
        for d in before["details"]:
            lot = d.get("material_lot")
            if d.get("material_name") == group["material"] and lot == group["typo"]:
                lot = group["correct"]
                changed += 1
            row = {k: d.get(k) for k in DETAIL_KEYS}
            row["material_lot"] = lot
            details.append(row)
        if not changed:
            problems.append((rid, "이미 바뀌어 있음"))
            continue
        body = {
            "recipe_id": before.get("recipe_id"), "product_name": before["product_name"],
            "worker": before["worker"], "work_date": before["work_date"],
            "work_time": before.get("work_time"), "total_amount": before["total_amount"],
            "scale": before.get("scale"), "note": before.get("note"),
            "reactor": before.get("reactor"), "details": details,
        }
        try:
            api.call("PUT", f"/api/blend/records/{rid}", body)
        except urllib.error.HTTPError as exc:
            problems.append((rid, f"{exc.code} {exc.read().decode('utf-8', 'replace')[:160]}"))
            continue
        after = api.get(f"/api/blend/records/{rid}")
        for a, b in zip(before["details"], after["details"]):
            for key in ("material_name", "ratio", "theory_amount", "actual_amount", "loss_comp_g"):
                if a.get(key) != b.get(key):
                    problems.append((rid, f"LOT 외 변경 {a.get('material_name')} {key} {a.get(key)} -> {b.get(key)}"))
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--api", default=DEFAULT_API)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--tier", choices=("high", "mid", "all"), default=None)
    parser.add_argument("--groups", default="", help="적용할 번호 (예: 3,7,12)")
    parser.add_argument("--user", default="admin")
    parser.add_argument("--password", default=None)
    parser.add_argument("--cache", default=str(DEFAULT_CACHE))
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()

    api = Api(args.api)
    try:
        api.get("/api/blend/records", limit=1)
    except (urllib.error.URLError, OSError) as exc:
        print(f"서버에 연결하지 못했습니다: {args.api} ({exc})")
        return 2

    print("기록을 읽는 중입니다. 처음 실행은 몇 분 걸립니다.")
    cache = load_records(api, Path(args.cache), args.refresh)
    groups = scan(cache)
    if not groups:
        print("오타로 보이는 자재 LOT 이 없습니다.")
        return 0

    tier = args.tier or ("high" if args.apply else "all")
    picked = {int(x) for x in args.groups.replace(" ", "").split(",") if x} if args.groups else None

    print(f"\n전체 후보 {len(groups)}건 (기록 {sum(len(g['record_ids']) for g in groups)}건)")
    for idx, g in enumerate(groups, 1):
        mark = "*" if (picked and idx in picked) or (not picked and (tier == "all" or g["tier"] == tier)) else " "
        print(f"{mark}{idx:3d}. [{g['tier']}] {'/'.join(g['products'])} · {g['material']}  "
              f"{g['typo']} -> {g['correct']}  ({g['kind']}, 사용 {g['typo_uses']}회 vs {g['correct_uses']}회, "
              f"기록 {len(g['record_ids'])}건)")
        print("      " + ", ".join(f"{r['work_date']} {r['product_lot']}" for r in g["records"][:8])
              + (" ..." if len(g["records"]) > 8 else ""))

    def wanted(index: int, group: dict) -> bool:
        if picked is not None:
            return index in picked
        return tier == "all" or group["tier"] == tier

    targets = [g for i, g in enumerate(groups, 1) if wanted(i, g)]
    if not args.apply:
        print(f"\n점검만 했습니다. 고치려면 --apply 를 붙이세요(기본은 high 등급만).")
        return 0
    if not targets:
        print("\n적용할 대상이 없습니다.")
        return 0

    print(f"\n적용 대상 {len(targets)}건 (기록 {sum(len(g['record_ids']) for g in targets)}건)")
    # 비밀번호는 인자나 환경변수로만 받는다. Windows 의 getpass 는 표준 입력이 아니라
    # 콘솔을 직접 읽어서, 창 없이 돌리면(백그라운드) 아무 표시 없이 영영 멈춘다(2026-09-10).
    password = args.password or os.environ.get("IRMS_MANAGER_PASSWORD") or ""
    if not password:
        print("책임자 비밀번호가 필요합니다. 둘 중 하나로 주세요.")
        print("  IRMS_MANAGER_PASSWORD=비밀번호 .venv/Scripts/python tools/fix_material_lot_typos.py "
              f"--tier {tier} --apply")
        print(f"  .venv/Scripts/python tools/fix_material_lot_typos.py --tier {tier} --apply --password 비밀번호")
        return 4
    try:
        api.call("POST", "/api/auth/management-login", {"username": args.user, "password": password})
    except urllib.error.HTTPError as exc:
        print(f"책임자 로그인 실패 ({exc.code}).")
        return 3

    all_problems: list[tuple] = []
    for g in targets:
        problems = apply_group(api, g)
        all_problems += problems
        state = "완료" if not problems else f"문제 {len(problems)}건"
        print(f"  {g['material']} {g['typo']} -> {g['correct']} · 기록 {len(g['record_ids'])}건 · {state}")
        for rid, message in problems:
            print(f"      #{rid}: {message}")
    print(f"\n끝났습니다. 문제 {len(all_problems)}건.")
    print("LOT 이력 화면에서 가짜 교체가 사라졌는지 확인하세요.")
    return 1 if all_problems else 0


if __name__ == "__main__":
    sys.exit(main())
