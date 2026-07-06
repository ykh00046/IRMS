"""통합 현장 도우미(트레이) 설정 계약 — 기능 토글 기본값·영속성.

사용자 요구: 알림/저울을 각각 켜고 끄고, 그 설정이 재부팅해도 유지되며, 기본은 알림만 켜짐.

tray_client 의 패키지명이 IRMS 백엔드와 동일한 ``src`` 라 그대로 import 하면 충돌한다.
그래서 config.py(순수 stdlib 의존, 상대 import 없음)를 파일 경로로 직접 로드해 검증한다.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

_CONFIG_PY = Path(__file__).resolve().parent.parent / "tray_client" / "src" / "config.py"


def _load_config_module(app_data_root: Path):
    """격리된 APPDATA 로 tray config 모듈을 새로 로드한다."""
    import os

    os.environ["APPDATA"] = str(app_data_root)
    spec = importlib.util.spec_from_file_location("tray_config_undertest", _CONFIG_PY)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    # dataclass(+from __future__ annotations)가 처리 중 sys.modules 를 조회하므로 등록 필요
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_defaults_alerts_on_scale_off(tmp_path):
    mod = _load_config_module(tmp_path)
    cfg = mod.Config.load()
    assert cfg.attendance_alerts_enabled is True
    assert cfg.viscosity_alerts_enabled is True
    assert cfg.scale_enabled is False


def test_toggles_persist_across_reload(tmp_path):
    mod = _load_config_module(tmp_path)
    cfg = mod.Config.load()
    cfg.attendance_alerts_enabled = False
    cfg.viscosity_alerts_enabled = True
    cfg.scale_enabled = True
    cfg.save()

    # 새 인스턴스로 다시 읽어도(=재부팅 상당) 값 유지
    reloaded = mod.Config.load()
    assert reloaded.attendance_alerts_enabled is False
    assert reloaded.viscosity_alerts_enabled is True
    assert reloaded.scale_enabled is True

    # 실제로 파일에 저장됐는지 확인
    saved = json.loads(mod.config_path().read_text(encoding="utf-8"))
    assert saved["attendance_alerts_enabled"] is False
    assert saved["scale_enabled"] is True


def test_legacy_config_without_toggles_gets_defaults(tmp_path):
    mod = _load_config_module(tmp_path)
    # 구 버전 config(토글 키 없음) 재현
    path = mod.config_path()
    path.write_text(json.dumps({"server_url": "http://10.0.0.5:9000"}), encoding="utf-8")

    cfg = mod.Config.load()
    assert cfg.server_url == "http://10.0.0.5:9000"  # 기존 값 보존
    assert cfg.attendance_alerts_enabled is True   # 누락 토글은 기본값
    assert cfg.viscosity_alerts_enabled is True
    assert cfg.scale_enabled is False


def test_legacy_alerts_enabled_migrates_to_granular(tmp_path):
    mod = _load_config_module(tmp_path)
    # 옛 단일 토글 alerts_enabled=False → 근태·점도 개별 토글 모두 False 로 이관
    mod.config_path().write_text(
        json.dumps({"server_url": "http://x:9000", "alerts_enabled": False}), encoding="utf-8"
    )
    cfg = mod.Config.load()
    assert cfg.attendance_alerts_enabled is False
    assert cfg.viscosity_alerts_enabled is False
    assert not hasattr(cfg, "alerts_enabled")


def test_unknown_keys_dropped(tmp_path):
    mod = _load_config_module(tmp_path)
    path = mod.config_path()
    path.write_text(
        json.dumps({"server_url": "http://x:9000", "legacy_tts_rate": 9, "scale_enabled": True}),
        encoding="utf-8",
    )
    cfg = mod.Config.load()
    assert cfg.scale_enabled is True
    assert not hasattr(cfg, "legacy_tts_rate")
