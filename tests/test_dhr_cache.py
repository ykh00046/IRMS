"""배합일지 PDF 캐시 — 마커 기반 자동 무효화."""

import importlib


def _fresh(tmp_path, monkeypatch):
    monkeypatch.setenv("IRMS_DATA_DIR", str(tmp_path))
    import src.config as cfg
    importlib.reload(cfg)
    from src.services import signature_config
    importlib.reload(signature_config)
    from src.services import dhr_cache
    importlib.reload(dhr_cache)
    return dhr_cache, signature_config


_REC = {"id": 1, "product_lot": "X", "worker": "김도현", "details": [{"material_name": "A"}]}


def test_miss_then_hit(tmp_path, monkeypatch):
    cache, _ = _fresh(tmp_path, monkeypatch)
    assert cache.get(_REC) is None
    cache.put(_REC, b"%PDF-DATA")
    assert cache.get(_REC) == b"%PDF-DATA"


def test_invalidate_on_record_change(tmp_path, monkeypatch):
    cache, _ = _fresh(tmp_path, monkeypatch)
    cache.put(_REC, b"%PDF-DATA")
    changed = dict(_REC, worker="다른사람")
    assert cache.get(changed) is None


def test_invalidate_on_config_change(tmp_path, monkeypatch):
    cache, sigcfg = _fresh(tmp_path, monkeypatch)
    cache.put(_REC, b"%PDF-DATA")
    assert cache.get(_REC) == b"%PDF-DATA"
    sigcfg.save({"rotation_angle": 12})
    assert cache.get(_REC) is None


def test_no_id_is_safe(tmp_path, monkeypatch):
    cache, _ = _fresh(tmp_path, monkeypatch)
    assert cache.get({"product_lot": "X"}) is None
    cache.put({"product_lot": "X"}, b"data")  # no-op, no error
