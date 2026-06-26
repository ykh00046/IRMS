"""배합일지 스캔효과 PDF(서명 합성) 검증."""

import io

from PIL import Image

from src.services import dhr_pdf


def _rec(worker="김도현"):
    return {
        "product_lot": "HGS-260625-01",
        "worker": worker,
        "work_date": "2026-06-25",
        "work_time": "10:30",
        "scale": "M-65",
        "total_amount": 1000,
        "details": [
            {"material_name": "HEMA", "material_lot": "MN-101", "ratio": 71.43,
             "theory_amount": 714.3, "actual_amount": 714.5},
            {"material_name": "NVP", "material_lot": "MN-102", "ratio": 28.57,
             "theory_amount": 285.7, "actual_amount": 285.5},
        ],
    }


def test_render_form_image_returns_image_and_positions():
    img, positions = dhr_pdf.render_form_image(_rec())
    assert isinstance(img, Image.Image)
    assert img.size[0] > 800
    assert set(positions) == {"charge", "review", "approve"}


def test_apply_scan_effects_keeps_size():
    img, _ = dhr_pdf.render_form_image(_rec())
    out = dhr_pdf.apply_scan_effects(img)
    assert out.size == img.size


def test_build_scanned_dhr_pdf_returns_pdf(monkeypatch):
    # PIL 폴백 경로를 결정적으로 검증(Excel COM 비의존)
    monkeypatch.setattr(dhr_pdf, "exact_available", lambda: False)
    pdf = dhr_pdf.build_scanned_dhr_pdf(_rec())
    assert isinstance(pdf, (bytes, bytearray))
    assert pdf[:5] == b"%PDF-"
    assert len(pdf) > 5000


def test_build_pdf_unknown_worker_no_charge_sample(monkeypatch):
    # 서명 샘플이 없는 작업자도 PDF는 정상 생성(charge 건너뜀, review/approve 합성)
    monkeypatch.setattr(dhr_pdf, "exact_available", lambda: False)
    pdf = dhr_pdf.build_scanned_dhr_pdf(_rec(worker="없는사람"))
    assert pdf[:5] == b"%PDF-"


def test_worker_sign_override_uses_canvas(tmp_path):
    # 작업자 캔버스 서명(worker_sign)이 담당칸 override 로 잡히고 파란 잉크로 통일되는지
    import base64
    import io as _io

    buf = _io.BytesIO()
    Image.new("RGBA", (240, 70), (0, 0, 0, 0)).save(buf, "PNG")
    # 실제로 획이 있어야 유효 → 한 픽셀이라도 불투명하게
    canvas = Image.new("RGBA", (240, 70), (0, 0, 0, 0))
    canvas.putpixel((10, 10), (17, 17, 17, 255))
    b = _io.BytesIO()
    canvas.save(b, "PNG")
    rec = _rec()
    rec["worker_sign"] = "data:image/png;base64," + base64.b64encode(b.getvalue()).decode()
    ov = dhr_pdf._worker_sign_override(rec, "김도현", str(tmp_path))
    assert ov.get("김도현_charge")  # 담당칸이 캔버스 서명으로 지정됨


def test_worker_sign_override_empty_without_sign(tmp_path):
    assert dhr_pdf._worker_sign_override(_rec(), "김도현", str(tmp_path)) == {}


def test_batch_dhr_pdf_multipage(monkeypatch):
    # 여러 기록 → 한 PDF(기록당 1장). PIL 폴백으로 결정적 검증.
    monkeypatch.setattr(dhr_pdf, "exact_available", lambda: False)
    pdf = dhr_pdf.build_batch_dhr_pdf([_rec(), _rec(worker="없는사람")])
    assert pdf[:5] == b"%PDF-"
    assert len(pdf) > 10000


def test_exact_path_when_available():
    # Excel + PyMuPDF 가용 환경에서만 정확 경로(Excel→PDF) 검증
    if not dhr_pdf.exact_available():
        import pytest
        pytest.skip("Excel/PyMuPDF 미지원 환경")
    img = dhr_pdf.render_exact_form_image(_rec())
    assert img is not None
    assert isinstance(img, Image.Image)
    assert img.size[0] > 1000
