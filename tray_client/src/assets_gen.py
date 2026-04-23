"""Generate runtime assets (tray icon, chime sound) without shipping binaries.

Run manually during the build step:

    python -m tray_client.src.assets_gen

This writes ``tray_client/assets/icon.ico`` and ``tray_client/assets/ding.wav``.
Keeping the generator in source means the repo contains no committed binaries
and every build produces byte-identical artifacts.
"""

from __future__ import annotations

import math
import struct
import sys
import wave
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ASSETS_DIR = Path(__file__).resolve().parent.parent / "assets"

BRAND_BG = (30, 64, 175)      # IRMS navy
BRAND_FG = (255, 255, 255)    # White text
ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)


def _load_bold_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Try a few system fonts to get a bold-looking glyph set."""
    candidates = [
        r"C:\Windows\Fonts\malgunbd.ttf",
        r"C:\Windows\Fonts\arialbd.ttf",
        r"C:\Windows\Fonts\segoeuib.ttf",
        r"C:\Windows\Fonts\arial.ttf",
    ]
    for path in candidates:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def _render_icon_frame(size: int) -> Image.Image:
    image = Image.new("RGBA", (size, size), BRAND_BG + (255,))
    draw = ImageDraw.Draw(image)

    # Soft rounded rectangle edge so the icon doesn't look like a flat square.
    radius = max(2, size // 6)
    draw.rounded_rectangle(
        (0, 0, size - 1, size - 1),
        radius=radius,
        fill=BRAND_BG + (255,),
    )

    # Text size is chosen to fill the frame without bleeding.
    text = "IRMS"
    # Scale font to roughly 46% of height for legibility.
    font = _load_bold_font(max(8, int(size * 0.46)))
    try:
        bbox = draw.textbbox((0, 0), text, font=font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
        x = (size - text_w) // 2 - bbox[0]
        y = (size - text_h) // 2 - bbox[1]
    except AttributeError:
        text_w, text_h = draw.textsize(text, font=font)  # type: ignore[attr-defined]
        x = (size - text_w) // 2
        y = (size - text_h) // 2
    draw.text((x, y), text, fill=BRAND_FG + (255,), font=font)
    return image


def build_icon(target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    base = _render_icon_frame(256)
    base.save(
        target,
        format="ICO",
        sizes=[(s, s) for s in ICON_SIZES],
    )


def _tone(frequency: float, duration_s: float, sample_rate: int, fade_ms: float = 20.0) -> bytes:
    total = int(duration_s * sample_rate)
    fade = int((fade_ms / 1000.0) * sample_rate)
    buf = bytearray()
    amp = 0.55 * 32767
    for n in range(total):
        sample = amp * math.sin(2 * math.pi * frequency * (n / sample_rate))
        if n < fade:
            sample *= n / fade
        elif n > total - fade:
            sample *= max(0.0, (total - n) / fade)
        buf += struct.pack("<h", int(sample))
    return bytes(buf)


def build_chime(target: Path, sample_rate: int = 44100) -> None:
    """Render a short two-tone 'ding-dong' notification."""
    target.parent.mkdir(parents=True, exist_ok=True)
    ding = _tone(880.0, 0.22, sample_rate)   # high
    gap = b"\x00\x00" * int(0.04 * sample_rate)
    dong = _tone(660.0, 0.32, sample_rate)   # low
    payload = ding + gap + dong
    with wave.open(str(target), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(payload)


def main() -> None:
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    icon_path = ASSETS_DIR / "icon.ico"
    chime_path = ASSETS_DIR / "ding.wav"
    build_icon(icon_path)
    build_chime(chime_path)
    print(f"wrote {icon_path} ({icon_path.stat().st_size} bytes)")
    print(f"wrote {chime_path} ({chime_path.stat().st_size} bytes)")


if __name__ == "__main__":
    sys.exit(main())
