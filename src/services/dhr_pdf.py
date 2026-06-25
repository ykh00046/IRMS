"""배합일지 스캔효과 PDF — Program-estimation v3 PdfScanRenderer/ExcelWriter 이식.

정확 경로(원본과 동일): 공식 양식 xlsx 를 채워 Excel(win32com)→PDF→(PyMuPDF)이미지 로
렌더 → 서명 합성(signature_processor) → 스캔효과 → PDF. 공식 양식과 픽셀 단위로 일치.
폴백 경로: Excel/win32com/PyMuPDF 가 없으면 PIL 로 양식을 재현(타 환경/개발용).
"""

import io
import os
import tempfile
import threading
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

from . import dhr_excel, signature_config, signature_samples
from .signature_processor import ImageProcessor

# 정확 경로 의존성(운영 PC: Excel + pywin32 + PyMuPDF). 없으면 PIL 폴백.
try:
    import fitz  # PyMuPDF
    _FITZ_OK = True
except ImportError:
    fitz = None
    _FITZ_OK = False

try:
    import pythoncom  # noqa: F401
    import win32com.client  # noqa: F401
    _WIN32_OK = True
except ImportError:
    _WIN32_OK = False

_excel_lock = threading.Lock()

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


_TEMPLATE_PATH = os.path.join(_RES, "dhr_template.xlsx")
_footer_cache: str | None = None


def _official_footer() -> str:
    """공식 양식의 바닥글(양식번호)을 템플릿 페이지 설정에서 그대로 가져온다."""
    global _footer_cache
    if _footer_cache is None:
        try:
            import openpyxl
            wb = openpyxl.load_workbook(_TEMPLATE_PATH)
            seg = wb.active.HeaderFooter.oddFooter.center
            _footer_cache = (seg.text or "").strip() if seg else ""
        except Exception:
            _footer_cache = "양식번호 : F706-4(Rev.1)"
    return _footer_cache


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

    # 바닥글: 양식번호(공식 양식 페이지 설정과 동일) — 페이지 하단 가운데
    footer = _official_footer()
    if footer:
        f_footer = _font(20)
        fw = d.textlength(footer, font=f_footer)
        d.text(((_W - fw) / 2, _H - 72), footer, fill=black, font=f_footer)

    return img, positions


def apply_scan_effects(image: Image.Image, params: dict | None = None) -> Image.Image:
    """스캔 효과: 블러 + 노이즈 + 대비 + 밝기 (원본 _apply_scan_effects 이식)."""
    p = params or {}
    blur = p.get("blur_radius", 1.1)
    noise = p.get("noise_range", 12)
    contrast = p.get("contrast_factor", 1.4)
    brightness = p.get("brightness_factor", 1.0)

    proc = image.convert("RGB").filter(ImageFilter.GaussianBlur(radius=blur))
    arr = np.array(proc)
    noise_arr = np.random.randint(-noise, noise, arr.shape, dtype="int16")
    arr = np.clip(arr + noise_arr, 0, 255).astype("uint8")
    proc = Image.fromarray(arr)
    proc = ImageEnhance.Contrast(proc).enhance(contrast)
    proc = ImageEnhance.Brightness(proc).enhance(brightness)
    return proc


def exact_available() -> bool:
    """원본과 동일한 Excel→PDF 정확 경로 사용 가능 여부."""
    return _WIN32_OK and _FITZ_OK


_EXCEL_TIMEOUT = 90  # 초 — Excel COM 변환 최대 대기


def _excel_convert(xlsx_bytes: bytes, out: dict) -> None:
    """(작업 스레드) Excel COM 으로 xlsx→PDF. 결과를 out['pdf'] 에 담는다."""
    import pythoncom
    import win32com.client
    pythoncom.CoInitialize()
    xl = None
    wb = None
    try:
        with tempfile.TemporaryDirectory() as tmp:
            xlsx = os.path.abspath(os.path.join(tmp, "dhr.xlsx"))
            pdf = os.path.abspath(os.path.join(tmp, "dhr.pdf"))
            with open(xlsx, "wb") as fh:
                fh.write(xlsx_bytes)
            xl = win32com.client.DispatchEx("Excel.Application")
            # 대화상자/매크로/링크 갱신 등 멈춤 유발 요소 차단
            xl.Visible = False
            xl.DisplayAlerts = False
            xl.ScreenUpdating = False
            xl.EnableEvents = False
            xl.AskToUpdateLinks = False
            try:
                xl.AlertBeforeOverwriting = False
                xl.AutomationSecurity = 3  # msoAutomationSecurityForceDisable (매크로 차단)
            except Exception:
                pass
            wb = xl.Workbooks.Open(xlsx, UpdateLinks=0, ReadOnly=True)
            wb.ExportAsFixedFormat(0, pdf)  # 0 = xlTypePDF
            wb.Close(False)
            wb = None
            xl.Quit()
            xl = None
            with open(pdf, "rb") as fh:
                out["pdf"] = fh.read()
    except Exception as exc:  # noqa: BLE001
        out["error"] = str(exc)
    finally:
        try:
            if wb is not None:
                wb.Close(False)
        except Exception:
            pass
        try:
            if xl is not None:
                xl.Quit()
        except Exception:
            pass
        pythoncom.CoUninitialize()


def _excel_to_pdf_bytes(xlsx_bytes: bytes) -> bytes | None:
    """채워진 xlsx 를 Excel COM 으로 PDF 변환. 실패/미지원/타임아웃 시 None(→PIL 폴백).

    Excel 이 대화상자 등으로 멈춰도 서버가 정지하지 않도록 작업 스레드 + 타임아웃으로 보호.
    """
    if not _WIN32_OK:
        return None
    out: dict = {}
    with _excel_lock:
        worker = threading.Thread(target=_excel_convert, args=(xlsx_bytes, out), daemon=True)
        worker.start()
        worker.join(_EXCEL_TIMEOUT)
        if worker.is_alive():
            # 타임아웃 — Excel 이 멈춤. 폴백으로 진행(서버는 정상).
            return None
    return out.get("pdf")


_STAMP_TEMPLATE = os.path.join(_SIG_DIR, "image.jpeg")


def _stamp_config(sc: dict) -> dict:
    """결재 도장(image.jpeg 350x100)용 ImageProcessor 설정 — 서명 위치는 원본 고정값."""
    return {
        "upsample_factor": 4,
        "target_width": 70,
        "target_height": 28,
        "dpi": 300,
        "gaussian_blur_sigma": sc["gaussian_blur_sigma"],
        "unsharp_mask": {"radius": 1.0, "percent": 120, "threshold": 2},
        "pressure_noise_strength": sc["pressure_noise_strength"],
        "mesh_warp": {"grid_size": 3, "jitter_amount": 1},
        "ink_alpha_factor": sc["ink_alpha_factor"],
        "signature_brightness_factor": sc["signature_brightness_factor"],
        "final_contrast_factor": sc["final_contrast_factor"],
        "randomization": {"rotation_angle": sc["rotation_angle"], "offset_x": 1, "offset_y": 2,
                          "scale_min": sc["scale_min"], "scale_max": sc["scale_max"]},
        "include": {"charge": True, "review": True, "approve": True},
        "positions": {"charge": [160, 57], "review": [222, 54], "approve": [288, 53]},
    }


def _build_signed_stamp(worker: str, sc: dict, out_path: str) -> str | None:
    """결재 도장(image.jpeg)에 담당/검토/승인 서명을 합성. 성공 시 경로 반환."""
    if not os.path.exists(_STAMP_TEMPLATE):
        return None
    proc = ImageProcessor(resources_path=signature_samples.samples_dir(), config=_stamp_config(sc))
    ok, _msg = proc.create_signed_image(_STAMP_TEMPLATE, out_path, worker)
    return out_path if ok and os.path.exists(out_path) else None


def render_exact_form_image(record: dict[str, Any], *, dpi: int = 200) -> Image.Image | None:
    """공식 양식 xlsx(결재 도장 G2 삽입) → Excel→PDF → 이미지(픽셀 일치).

    원본과 동일: 결재 도장(image.jpeg)에 서명 합성 → 양식 G2 셀에 삽입 → Excel 렌더.
    Excel/PyMuPDF 미지원 시 None.
    """
    if not exact_available():
        return None
    sc = signature_config.load()
    worker = str(record.get("worker") or "").strip()
    with tempfile.TemporaryDirectory() as tmp:
        stamp_path = _build_signed_stamp(worker, sc, os.path.join(tmp, "stamp.png"))
        xlsx = dhr_excel.build_official_dhr_xlsx(record, signature_image_path=stamp_path)
        pdf_bytes = _excel_to_pdf_bytes(xlsx)
    if not pdf_bytes:
        return None
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    pix = doc[0].get_pixmap(dpi=dpi)
    img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
    doc.close()
    return img


def render_signed_dhr(record: dict[str, Any], *, scan: bool = True) -> Image.Image:
    """원료배합일지 양식 + 결재 도장 서명 → (선택)스캔효과 → PIL 이미지.

    정확 경로(Excel→PDF, 결재 도장 image.jpeg 를 G2 셀에 삽입) 우선. Excel 미지원 시
    PIL 재현(우상단 결재칸 오버레이)으로 폴백. 합성 파라미터·스캔효과는 signature_config.
    """
    sc = signature_config.load()
    result = render_exact_form_image(record)

    if result is None:
        # 폴백(PIL, 개발/타 환경): 양식 재현 + 결재칸 서명 오버레이
        base_img, positions = render_form_image(record)
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
        processor = ImageProcessor(resources_path=signature_samples.samples_dir(), config=config)
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
