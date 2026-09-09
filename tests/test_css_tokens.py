"""CSS 변수 참조 무결성.

배합 기록 상세창이 투명하게 뜬 사고(2026-09-09): 토큰 회수 커밋이 `--bg` 를 지웠는데
blend.css 의 `.ss-modal { background: var(--bg) }` 가 남아 배경이 사라졌다. 브라우저는
정의되지 않은 변수를 조용히 무시하므로 테스트만이 잡는다.

규칙: static/css 와 템플릿 인라인 <style> 안의 `var(--x)` 는 common.css 또는 같은 파일에서
정의돼 있어야 한다. 폴백이 있어도 허용하지 않는다(폴백은 토큰 표준 밖의 hex 를 숨긴다).
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEF_RE = re.compile(r"--([a-zA-Z0-9-]+)\s*:")
USE_RE = re.compile(r"var\(--([a-zA-Z0-9-]+)")


def _defined_in(text: str) -> set[str]:
    return set(DEF_RE.findall(text))


def test_every_css_var_is_defined() -> None:
    common = (ROOT / "static/css/common.css").read_text(encoding="utf-8")
    global_defs = _defined_in(common)
    problems: list[str] = []
    files = sorted((ROOT / "static/css").glob("*.css")) + sorted((ROOT / "templates").glob("*.html"))
    for path in files:
        text = path.read_text(encoding="utf-8")
        local_defs = _defined_in(text)
        for match in USE_RE.finditer(text):
            name = match.group(1)
            if name not in global_defs and name not in local_defs:
                line = text.count("\n", 0, match.start()) + 1
                problems.append(f"{path.relative_to(ROOT)}:{line} var(--{name})")
    assert not problems, "정의되지 않은 CSS 변수:\n" + "\n".join(problems)
