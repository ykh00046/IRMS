"""작업자 서명 샘플 관리 — 시드/업로드/목록/삭제."""

import importlib
import io

from PIL import Image


def _fresh(tmp_path, monkeypatch):
    monkeypatch.setenv("IRMS_DATA_DIR", str(tmp_path))
    import src.config as cfg
    importlib.reload(cfg)
    from src.services import signature_samples
    importlib.reload(signature_samples)
    return signature_samples


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", (120, 40), (255, 255, 255, 0)).save(buf, "PNG")
    return buf.getvalue()


def test_seeded_from_bundled(tmp_path, monkeypatch):
    ss = _fresh(tmp_path, monkeypatch)
    groups = ss.list_samples()
    bases = {g["base"] for g in groups}
    assert "review" in bases
    assert "approve" in bases
    assert any(b.endswith("_charge") for b in bases)


def test_add_charge_for_new_worker(tmp_path, monkeypatch):
    ss = _fresh(tmp_path, monkeypatch)
    fname = ss.add_sample("charge", "홍길동", _png())
    assert fname == "홍길동_charge_1.png"
    g = [x for x in ss.list_samples() if x["worker"] == "홍길동"]
    assert g and g[0]["count"] == 1
    # 두 번째는 _2
    assert ss.add_sample("charge", "홍길동", _png()) == "홍길동_charge_2.png"


def test_charge_requires_worker(tmp_path, monkeypatch):
    ss = _fresh(tmp_path, monkeypatch)
    try:
        ss.add_sample("charge", "", _png())
        raised = False
    except ValueError:
        raised = True
    assert raised


def test_delete_sample(tmp_path, monkeypatch):
    ss = _fresh(tmp_path, monkeypatch)
    fname = ss.add_sample("review", "", _png())
    assert ss.delete_sample(fname) is True
    assert ss.delete_sample("nope.png") is False


def test_reject_non_image(tmp_path, monkeypatch):
    ss = _fresh(tmp_path, monkeypatch)
    try:
        ss.add_sample("approve", "", b"not-an-image")
        raised = False
    except ValueError:
        raised = True
    assert raised
