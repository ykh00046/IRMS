"""작업자 서명 샘플 관리 — 합성 서명에 쓰는 인별 샘플(PNG)을 운영 데이터 폴더에서 관리.

번들 샘플(src/resources/signature/*.png)을 최초 1회 data/signature_samples/ 로 시드한 뒤,
관리자가 업로드/삭제한다. ImageProcessor 는 이 폴더를 resources_path 로 사용한다.
역할: 담당(charge, 작업자별) / 검토(review, 공용) / 승인(approve, 공용).
파일명 규칙: {base}_{n}.png  (담당=f"{worker}_charge", 검토="review", 승인="approve")
"""

import glob
import io
import os
import re
import shutil
from typing import Any

from PIL import Image

from .. import config

_BUNDLED = os.path.join(os.path.dirname(__file__), "..", "resources", "signature")
SAMPLES_DIR = config.DATA_DIR / "signature_samples"
ROLES = {"charge": "담당", "review": "검토", "approve": "승인"}


def ensure_seeded() -> None:
    SAMPLES_DIR.mkdir(parents=True, exist_ok=True)
    if not any(SAMPLES_DIR.glob("*.png")):
        for f in glob.glob(os.path.join(_BUNDLED, "*.png")):
            try:
                shutil.copy2(f, SAMPLES_DIR / os.path.basename(f))
            except OSError:
                pass


def samples_dir() -> str:
    """ImageProcessor resources_path 로 쓸 (시드된) 샘플 폴더."""
    ensure_seeded()
    return str(SAMPLES_DIR)


def _base_name(role: str, worker: str | None) -> str:
    if role == "charge":
        return f"{(worker or '').strip()}_charge"
    if role in ("review", "approve"):
        return role
    raise ValueError("알 수 없는 역할입니다.")


def list_samples() -> list[dict[str, Any]]:
    """base 별 샘플 그룹 목록. [{base, role, worker, files:[...], count}]."""
    ensure_seeded()
    groups: dict[str, list[str]] = {}
    for p in sorted(SAMPLES_DIR.glob("*.png")):
        m = re.match(r"^(.*)_(\d+)$", p.stem)
        base = m.group(1) if m else p.stem
        groups.setdefault(base, []).append(p.name)
    out = []
    for base, files in sorted(groups.items()):
        if base.endswith("_charge"):
            role, worker = "charge", base[: -len("_charge")]
        elif base in ("review", "approve"):
            role, worker = base, ""
        else:
            role, worker = "", ""
        out.append({"base": base, "role": role, "worker": worker,
                    "files": files, "count": len(files)})
    return out


def add_sample(role: str, worker: str | None, data: bytes) -> str:
    """업로드 이미지를 PNG 로 정규화해 다음 번호로 저장. 파일명 반환."""
    ensure_seeded()
    base = _base_name(role, worker)
    if base.startswith("_") or base == "_charge":
        raise ValueError("작업자 이름이 필요합니다.")
    try:
        img = Image.open(io.BytesIO(data)).convert("RGBA")
    except Exception as exc:  # noqa: BLE001
        raise ValueError("이미지 파일이 아닙니다.") from exc
    nums = []
    for p in SAMPLES_DIR.glob(f"{base}_*.png"):
        mm = re.match(rf"^{re.escape(base)}_(\d+)$", p.stem)
        if mm:
            nums.append(int(mm.group(1)))
    n = (max(nums) + 1) if nums else 1
    fname = f"{base}_{n}.png"
    img.save(SAMPLES_DIR / fname)
    return fname


def delete_sample(filename: str) -> bool:
    ensure_seeded()
    name = os.path.basename(filename or "")
    if not name.endswith(".png"):
        return False
    p = SAMPLES_DIR / name
    if p.exists():
        try:
            p.unlink()
            return True
        except OSError:
            return False
    return False
