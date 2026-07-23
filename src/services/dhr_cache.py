"""배합일지 PDF 캐시 — 승인/생성된 DHR PDF 를 디스크에 보관해 재생성(Excel COM) 회피.

마커(레코드 내용 + 서명 설정의 해시) 기반으로 자동 무효화한다. 레코드나 서명 설정이
바뀌면 마커가 달라져 자동으로 재생성된다(별도 무효화 훅 불필요).
"""

import hashlib
import json
from typing import Any

from .. import config
from . import signature_config

_CACHE_DIR = config.DATA_DIR / "dhr_cache"


def _marker(record: dict[str, Any]) -> str:
    # v2: 기본 출력이 '서명 없음'으로 바뀜 → 기존(서명본) 캐시 무효화
    payload = {"v": 2, "record": record, "sig": signature_config.load()}
    blob = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _paths(record_id: Any):
    return (_CACHE_DIR / f"blend_{record_id}.pdf", _CACHE_DIR / f"blend_{record_id}.marker")


def get(record: dict[str, Any]) -> bytes | None:
    """캐시가 유효하면 PDF 바이트, 아니면 None."""
    rid = record.get("id")
    if rid is None:
        return None
    pdf_path, marker_path = _paths(rid)
    try:
        if pdf_path.exists() and marker_path.exists():
            if marker_path.read_text(encoding="utf-8") == _marker(record):
                return pdf_path.read_bytes()
    except OSError:
        pass
    return None


def put(record: dict[str, Any], data: bytes) -> None:
    """PDF 와 마커를 저장."""
    rid = record.get("id")
    if rid is None:
        return
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        pdf_path, marker_path = _paths(rid)
        pdf_path.write_bytes(data)
        marker_path.write_text(_marker(record), encoding="utf-8")
    except OSError:
        pass


def purge(record_id: Any) -> None:
    """레코드의 캐시(PDF+마커)를 삭제 — hard 삭제 후 잔류 파일 제거(POLISH-7a).

    id 재사용은 없지만 디스크 litter 를 남기지 않도록 삭제 시 함께 정리한다.
    파일이 없어도(캐시 미생성) 안전한 no-op.
    """
    if record_id is None:
        return
    pdf_path, marker_path = _paths(record_id)
    for path in (pdf_path, marker_path):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
