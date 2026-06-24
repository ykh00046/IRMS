"""배합일지 스캔효과 PDF — Program-estimation v3 PdfScanRenderer/ExcelWriter 이식(웹용).

원본은 Excel→(win32com)PDF→(PyMuPDF)이미지→스캔효과→PDF 였으나, 웹서버엔 Excel COM이
없으므로 **PIL로 원료배합일지 양식 이미지를 직접 렌더** → 서명 합성(signature_processor)
→ 스캔효과(블러/노이즈/대비/밝기) → PDF 저장. Pillow + numpy 만 의존.
"""

import io
import os
import tempfile
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

from . import signature_config
from .signature_processor import ImageProcessor

_RES = os.path.join(os.path.dirname(__file__), "..", "resources")
_SIG_DIR = os.path.join(_RES, "signature")
_FONT_CANDIDATES = [
    "C:/Windows/Fonts/malgun.ttf",
    "C:/Windows/Fonts/malgunbd.ttf",
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
]

# A4 세로 @ ~150dpi
_W, _H = 1240, 1754
_MARGIN = 70


def _font(size: int):
    for path in _FONT_CANDIDATES:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _num(v: Any) -> str:
    if v is None or v == "":
        return ""
    try:
        f = float(v)
        return str(int(f)) if f == int(f) else f"{f:.2f}"
    except (TypeError, ValueError):
        return str(v)


def render_form_image(record: dict[str, Any]) -> tuple[Image.Image, dict[str, list[int]]]:
    """원료배합일지 양식을 흰 배경 이미지로 렌더. (이미지, 서명위치) 반환."""
    img = Image.new("RGB", (_W, _H), "white")
    d = ImageDraw.Draw(img)
    f_title = _font(46)
    f_label = _font(24)
    f_cell = _font(22)
    f_small = _font(20)
    black = (0, 0, 0)

    # 제목
    title = "원 료 배 합 일 지"
    tw = d.textlength(title, font=f_title)
    d.text(((_W - tw) / 2, 40), title, fill=black, font=f_title)

    # 메타: 작업일 / 작업자 / 작업시간 / 저울
    y = 120
    d.text((_MARGIN, y), f"작업일 : {record.get('work_date', '')}", fill=black, font=f_label)
    d.text((_MARGIN + 380, y), f"작업자 : {record.get('worker', '')}", fill=black, font=f_label)
    d.text((_MARGIN, y + 36), f"작업시간 : {record.get('work_time') or ''}", fill=black, font=f_label)
    d.text((_MARGIN + 380, y + 36), f"사용저울 : {record.get('scale') or ''}", fill=black, font=f_label)

    # 결재 박스 (우상단): 담당 / 검토 / 승인
    box_w, box_h = 110, 90
    bx = _W - _MARGIN - box_w * 3
    by = 110
    roles = ["담당", "검토", "승인"]
    positions: dict[str, list[int]] = {}
    keymap = {"담당": "charge", "검토": "review", "승인": "approve"}
    for i, role in enumerate(roles):
        x0 = bx + i * box_w
        d.rectangle([x0, by, x0 + box_w, by + box_h], outline=black, width=2)
        d.rectangle([x0, by, x0 + box_w, by + 28], outline=black, width=2)
        lw = d.textlength(role, font=f_small)
        d.text((x0 + (box_w - lw) / 2, by + 4), role, fill=black, font=f_small)
        # 서명 합성 위치(셀 중앙, 라벨 아래)
        positions[keymap[role]] = [x0 + box_w // 2 - 55, by + 30 + (box_h - 28) // 2 - 22]

    # 표 헤더 (5행)
    headers = ["약품번호", "배합량(100g)", "배합원료명", "원료 LOT NO", "배합비율", "배합량(g)", "실제배합량(g)"]
    col_x = [_MARGIN, 205, 320, 560, 760, 900, 1025, _W - _MARGIN]
    top = 250
    rowh = 46
    f_hdr = _font(18)
    # 헤더 배경 + 텍스트
    d.rectangle([col_x[0], top, col_x[-1], top + rowh], outline=black, width=2)
    for c, h in enumerate(headers):
        d.line([(col_x[c], top), (col_x[c], top + rowh)], fill=black, width=2)
        hw = d.textlength(h, font=f_hdr)
        d.text((col_x[c] + (col_x[c + 1] - col_x[c] - hw) / 2, top + 13), h, fill=black, font=f_hdr)

    # 데이터 행
    details = record.get("details", []) or []
    n = max(len(details), 1)
    body_top = top + rowh
    for r in range(n):
        ry = body_top + r * rowh
        d.rectangle([col_x[0], ry, col_x[-1], ry + rowh], outline=black, width=1)
        for c in range(1, len(col_x) - 1):
            d.line([(col_x[c], ry), (col_x[c], ry + rowh)], fill=black, width=1)
        if r < len(details):
            dd = details[r]
            cells = [
                "", "",
                str(dd.get("material_name", "")),
                str(dd.get("material_lot") or ""),
                _num(dd.get("ratio")),
                _num(dd.get("theory_amount")),
                _num(dd.get("actual_amount")),
            ]
            for c, val in enumerate(cells):
                if not val:
                    continue
                vw = d.textlength(val, font=f_cell)
                d.text((col_x[c] + (col_x[c + 1] - col_x[c] - vw) / 2, ry + 10), val, fill=black, font=f_cell)

    # 제품 LOT(A 병합)·총량/100(B 병합) 세로 중앙
    merge_mid = body_top + (n * rowh) // 2 - 12
    lot = str(record.get("product_lot", ""))
    lw = d.textlength(lot, font=f_cell)
    d.text((col_x[0] + (col_x[1] - col_x[0] - lw) / 2, merge_mid), lot, fill=black, font=f_cell)
    total100 = _num((record.get("total_amount") or 0) / 100)
    bw = d.textlength(total100, font=f_cell)
    d.text((col_x[1] + (col_x[2] - col_x[1] - bw) / 2, merge_mid), total100, fill=black, font=f_cell)

    return img, positions


def apply_scan_effects(image: Image.Image, params: dict | None = None) -> Image.Image:
    """스캔 효과: 블러 + 노이즈 + 대비 + 밝기 (원본 _apply_scan_effects 이식)."""
    p = params or {}
    blur = p.get("blur_radius", 0.3)
    noise = p.get("noise_range", 18)
    contrast = p.get("contrast_factor", 1.25)
    brightness = p.get("brightness_factor", 1.05)

    proc = image.convert("RGB").filter(ImageFilter.GaussianBlur(radius=blur))
    arr = np.array(proc)
    noise_arr = np.random.randint(-noise, noise, arr.shape, dtype="int16")
    arr = np.clip(arr + noise_arr, 0, 255).astype("uint8")
    proc = Image.fromarray(arr)
    proc = ImageEnhance.Contrast(proc).enhance(contrast)
    proc = ImageEnhance.Brightness(proc).enhance(brightness)
    return proc


def render_signed_dhr(record: dict[str, Any], *, scan: bool = True) -> Image.Image:
    """원료배합일지 양식 렌더 → 서명 합성 → (선택)스캔효과 → PIL 이미지.

    작성자(record['worker'])의 서명 샘플({worker}_charge_*.png)이 있으면 합성,
    검토/승인은 공용 review/approve 샘플로 합성. 샘플이 없는 역할은 건너뜀.
    합성 파라미터·스캔효과는 signature_config(관리자 튜닝)에서 읽는다.
    """
    base_img, positions = render_form_image(record)
    sc = signature_config.load()

    config = {
        "upsample_factor": 4,
        "target_width": 150,
        "target_height": 60,
        "dpi": 200,
        "gaussian_blur_sigma": sc["gaussian_blur_sigma"],
        "unsharp_mask": {"radius": 1.0, "percent": 120, "threshold": 2},
        "pressure_noise_strength": sc["pressure_noise_strength"],
        "mesh_warp": {"grid_size": 3, "jitter_amount": 2},
        "ink_alpha_factor": sc["ink_alpha_factor"],
        "signature_brightness_factor": sc["signature_brightness_factor"],
        "final_contrast_factor": sc["final_contrast_factor"],
        "randomization": {"rotation_angle": sc["rotation_angle"], "offset_x": 3, "offset_y": 5,
                          "scale_min": sc["scale_min"], "scale_max": sc["scale_max"]},
        "include": {"charge": True, "review": True, "approve": True},
        "positions": positions,
    }
    processor = ImageProcessor(resources_path=_SIG_DIR, config=config)
    worker = str(record.get("worker") or "").strip()

    with tempfile.TemporaryDirectory() as tmp:
        base_path = os.path.join(tmp, "base.png")
        signed_path = os.path.join(tmp, "signed.png")
        base_img.save(base_path)
        ok, _msg = processor.create_signed_image(base_path, signed_path, worker)
        result = Image.open(signed_path).convert("RGB") if ok and os.path.exists(signed_path) else base_img

    if scan:
        result = apply_scan_effects(result, {
            "noise_range": int(sc["scan_noise_range"]),
            "blur_radius": sc["scan_blur_radius"],
            "contrast_factor": sc["scan_contrast"],
            "brightness_factor": sc["scan_brightness"],
        })
    return result.convert("RGB")


def build_scanned_dhr_pdf(record: dict[str, Any], *, scan: bool = True) -> bytes:
    """배합 기록 → 서명 합성 + 스캔효과 원료배합일지 PDF 바이트."""
    img = render_signed_dhr(record, scan=scan)
    buf = io.BytesIO()
    img.save(buf, format="PDF", resolution=200.0)
    return buf.getvalue()


def build_preview_png(record: dict[str, Any], *, scan: bool = True) -> bytes:
    """서명 설정 미리보기용 — 샘플 배합일지 PNG 바이트."""
    img = render_signed_dhr(record, scan=scan)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


_PREVIEW_RECORD = {
    "product_lot": "MISC-PREVIEW", "worker": "김도현", "work_date": "2026-06-25",
    "work_time": "10:30", "scale": "M-65", "total_amount": 1000,
    "details": [
        {"material_name": "HEMA 모노머", "material_lot": "MN-101", "ratio": 71.43,
         "theory_amount": 714.3, "actual_amount": 714.5},
        {"material_name": "NVP", "material_lot": "MN-102", "ratio": 28.57,
         "theory_amount": 285.7, "actual_amount": 285.5},
    ],
}


def build_signature_preview_png(worker: str | None = None) -> bytes:
    """현재 서명 설정으로 합성한 샘플 미리보기 PNG."""
    rec = dict(_PREVIEW_RECORD)
    if worker:
        rec["worker"] = worker
    return build_preview_png(rec)
