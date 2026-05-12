"""Gemini 2.5 Flash OCR service for INK request sheet image parsing.

Parses INK request sheet screenshots into structured data using Google Gemini
multimodal API with Pydantic schema enforcement.
"""

from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from ..config import GEMINI_API_KEY

logger = logging.getLogger(__name__)


# ── Response schemas ──────────────────────────────────────

class InkRequestRow(BaseModel):
    machine_no: int = Field(description="호기 번호 (10, 11, ...)")
    brand: str = Field(description="구분 (IRIS, PIA, PIA_M, PIA/액상, Bella 등)")
    product_name: str = Field(description="제품명 (Suzy Brown, D_Pink Bomb_55%_14.5 UV 등)")


class InkRequestSheet(BaseModel):
    request_date: str = Field(description="요청 날짜 YYYY-MM-DD 형식")
    shift: str = Field(description="시프트: 주간, 야간, 명일주간")
    line: str = Field(description="라인: 3F 또는 1F")
    rows: list[InkRequestRow] = Field(default_factory=list)


class ChemicalRow(BaseModel):
    chemical_name: str = Field(description="약품명 (38CD-HCN, 55GD-HCU 등)")
    concentration: str = Field(default="", description="농도 (38%, 55% 등)")
    qty_3f: float | None = Field(default=None, description="3F 대수")
    qty_1f: float | None = Field(default=None, description="1F 대수")


class InkRequestDocument(BaseModel):
    ink_requests: list[InkRequestSheet] = Field(default_factory=list)
    chemical_requests: list[ChemicalRow] = Field(default_factory=list)


# ── Prompt ────────────────────────────────────────────────

_SYSTEM_PROMPT = """당신은 콘택트렌즈 잉크 생산 문서 전문 OCR 파서입니다.

이미지는 'INK 요청서'와 '약품요청서' 두 부분으로 구성됩니다.

## INK 요청서 파싱 규칙:
1. 상단 테이블에서 호기(10~59), 구분(브랜드), 제품명을 추출하세요.
2. 날짜, 시프트(주간/야간/명일주간), 라인(3F/1F)별로 분리하세요.
3. "TEST"라고 적힌 행은 제외하세요.
4. 제품명은 원본 그대로 추출하세요 (특수문자, 숫자, % 포함).
5. 빈 셀이 있는 행은 건너뛰세요.

## 약품요청서 파싱 규칙:
1. 하단 테이블에서 약품명, 농도(구분), 3F/1F 대수를 추출하세요.
2. 대수가 비어있으면 null로 설정하세요.

## 날짜 형식:
- "2026년 05월 12일 (화)" → "2026-05-12"
"""


# ── Service functions ─────────────────────────────────────

def _ensure_api_key() -> None:
    if not GEMINI_API_KEY:
        raise RuntimeError(
            "GEMINI_API_KEY 환경변수가 설정되지 않았습니다. "
            "https://aistudio.google.com/apikey 에서 발급하세요."
        )


def parse_ink_request_image(image_path: str | Path) -> InkRequestDocument:
    """Parse an INK request sheet image using Gemini 2.5 Flash."""
    _ensure_api_key()

    try:
        from google import genai
        from google.genai import types
    except ImportError:
        raise RuntimeError("google-genai 패키지를 설치하세요: pip install google-genai")

    image_path = Path(image_path)
    if not image_path.exists():
        raise FileNotFoundError(f"이미지 파일을 찾을 수 없습니다: {image_path}")

    image_bytes = image_path.read_bytes()
    suffix = image_path.suffix.lower()
    mime_map = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
    mime_type = mime_map.get(suffix, "image/png")

    client = genai.Client(api_key=GEMINI_API_KEY)

    image_part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=[
            _SYSTEM_PROMPT,
            image_part,
            "이 INK 요청서 이미지를 파싱하여 구조화된 JSON으로 반환하세요.",
        ],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=InkRequestDocument,
            temperature=0.1,
        ),
    )

    result = response.parsed
    if result is None:
        logger.error("Gemini returned no parsed result. Raw: %s", response.text[:500] if response.text else "empty")
        raise RuntimeError("Gemini OCR 파싱 실패: 응답을 파싱할 수 없습니다.")

    logger.info(
        "OCR parsed: %d ink sheets, %d chemical rows",
        len(result.ink_requests),
        len(result.chemical_requests),
    )
    return result


def parse_ink_request_bytes(image_bytes: bytes, filename: str = "upload.png") -> InkRequestDocument:
    """Parse from raw bytes (for file upload endpoint)."""
    import tempfile
    suffix = Path(filename).suffix or ".png"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(image_bytes)
        tmp_path = tmp.name
    try:
        return parse_ink_request_image(tmp_path)
    finally:
        Path(tmp_path).unlink(missing_ok=True)
