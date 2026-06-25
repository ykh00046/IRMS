"""서명 합성 파라미터 설정(관리자 튜닝) — signature_qa_tool 이식.

ImageProcessor 합성 파라미터 + 스캔 효과를 data/signature_config.json 에 저장/로드.
미설정 시 기본값 사용. dhr_pdf 가 이 설정을 읽어 합성·스캔에 반영한다.
"""

import json
from typing import Any

from .. import config

DEFAULTS: dict[str, float] = {
    # 서명 합성(ImageProcessor)
    "gaussian_blur_sigma": 0.7,
    "pressure_noise_strength": 0.08,
    "ink_alpha_factor": 1.3,
    "signature_brightness_factor": 1.15,
    "final_contrast_factor": 1.2,
    "rotation_angle": 6.0,
    "scale_min": 0.85,
    "scale_max": 0.95,
    # 스캔 효과 (원본 운영값 — 복사/스캔 느낌)
    "scan_noise_range": 12.0,
    "scan_blur_radius": 1.1,
    "scan_contrast": 1.4,
    "scan_brightness": 1.0,
    "scan_paper_tone": 0.07,  # 종이톤 강도(흰 여백을 스캔 종이처럼) 0=없음
}

# 입력 검증 범위 (signature_qa_tool 슬라이더 범위 참고)
RANGES: dict[str, tuple[float, float]] = {
    "gaussian_blur_sigma": (0.0, 5.0),
    "pressure_noise_strength": (0.0, 0.5),
    "ink_alpha_factor": (1.0, 3.0),
    "signature_brightness_factor": (0.5, 2.0),
    "final_contrast_factor": (0.5, 2.0),
    "rotation_angle": (0.0, 30.0),
    "scale_min": (0.5, 1.0),
    "scale_max": (0.5, 1.0),
    "scan_noise_range": (0.0, 60.0),
    "scan_blur_radius": (0.0, 3.0),
    "scan_contrast": (0.5, 2.0),
    "scan_brightness": (0.5, 2.0),
    "scan_paper_tone": (0.0, 0.3),
}


def _path():
    return config.DATA_DIR / "signature_config.json"


def load() -> dict[str, float]:
    cfg = dict(DEFAULTS)
    p = _path()
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            for k, v in data.items():
                if k in DEFAULTS:
                    cfg[k] = float(v)
        except (ValueError, OSError):
            pass
    return cfg


def save(updates: dict[str, Any]) -> dict[str, float]:
    cfg = load()
    for k, v in updates.items():
        if k not in DEFAULTS or v is None:
            continue
        try:
            val = float(v)
        except (TypeError, ValueError):
            continue
        lo, hi = RANGES.get(k, (None, None))
        if lo is not None:
            val = max(lo, min(hi, val))
        cfg[k] = val
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    _path().write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return cfg
