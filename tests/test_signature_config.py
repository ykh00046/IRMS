"""서명 합성 설정(관리자 튜닝) 저장/로드/검증."""

import importlib


def _fresh(tmp_path, monkeypatch):
    monkeypatch.setenv("IRMS_DATA_DIR", str(tmp_path))
    import src.config as cfg
    importlib.reload(cfg)
    from src.services import signature_config
    importlib.reload(signature_config)
    return signature_config


def test_defaults_when_unset(tmp_path, monkeypatch):
    sc = _fresh(tmp_path, monkeypatch)
    cfg = sc.load()
    assert cfg["rotation_angle"] == sc.DEFAULTS["rotation_angle"]
    assert set(cfg) == set(sc.DEFAULTS)


def test_save_and_load_roundtrip(tmp_path, monkeypatch):
    sc = _fresh(tmp_path, monkeypatch)
    sc.save({"rotation_angle": 12, "scan_noise_range": 30})
    cfg = sc.load()
    assert cfg["rotation_angle"] == 12.0
    assert cfg["scan_noise_range"] == 30.0


def test_unknown_key_ignored(tmp_path, monkeypatch):
    sc = _fresh(tmp_path, monkeypatch)
    sc.save({"bad_key": 9})
    assert "bad_key" not in sc.load()


def test_value_clamped_to_range(tmp_path, monkeypatch):
    sc = _fresh(tmp_path, monkeypatch)
    sc.save({"rotation_angle": 999})  # max 30
    assert sc.load()["rotation_angle"] == 30.0
