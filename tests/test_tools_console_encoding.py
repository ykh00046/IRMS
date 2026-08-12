"""tools/ 의 콘솔 출력이 운영 PC(CP949) 에서 죽지 않는지 고정.

배경: 운영 PC 의 콘솔 코드페이지는 949 다. 파이썬이 stdout 에 CP949 로 인코딩할 수
없는 문자를 쓰면 UnicodeEncodeError 로 **도구가 그 자리에서 죽는다**. 한글은 CP949 에
있어서 멀쩡한데, 무심코 쓰는 em dash(—)·경고 기호(⚠)·체크(✓) 는 없다.

실제로 2026-08-12 에 unify_material_names.py 가 이 이유로 죽었고, 같은 문자가
import_legacy_records.py·blend_query.py 에도 들어가 있었다. 눈으로는 안 보이는
함정이라 테스트로 막는다.

chcp 65001 로 콘솔을 UTF-8 로 바꾸는 우회는 쓰지 않는다 — 배치 파서를 깨뜨려
별도 사고를 냈던 전례가 있다(feedback: .bat 인코딩). 출력 문자를 고르는 쪽이 맞다.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

TOOLS = sorted(Path("tools").glob("*.py"))


def _console_strings(path: Path) -> list[tuple[int, str]]:
    """print() 인자와 argparse help= 에 들어가는 문자열 리터럴을 모은다.

    이 둘이 콘솔로 나가는 경로다. 주석·docstring 은 파일 인코딩(UTF-8)으로만 읽히고
    출력되지 않으므로 대상이 아니다.
    """
    found: list[tuple[int, str]] = []
    tree = ast.parse(path.read_text(encoding="utf-8"))

    def literals(node):
        """노드 안의 문자열 리터럴 — f-string 의 고정 부분도 포함."""
        for sub in ast.walk(node):
            if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                yield sub.value

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            is_print = isinstance(func, ast.Name) and func.id == "print"
            if is_print:
                for arg in node.args:
                    for text in literals(arg):
                        found.append((node.lineno, text))
            for kw in node.keywords:
                if kw.arg in ("help", "description", "epilog"):
                    for text in literals(kw.value):
                        found.append((node.lineno, text))
    return found


@pytest.mark.parametrize("tool", TOOLS, ids=lambda p: p.name)
def test_console_output_encodes_on_cp949(tool: Path):
    offenders: list[str] = []
    for lineno, text in _console_strings(tool):
        for ch in text:
            try:
                ch.encode("cp949")
            except UnicodeEncodeError:
                offenders.append(f"{tool}:{lineno} U+{ord(ch):04X} {ch!r}")
    assert not offenders, (
        "운영 PC(CP949) 콘솔에서 UnicodeEncodeError 로 죽는 문자가 출력에 있습니다.\n"
        + "\n".join(sorted(set(offenders)))
        + "\n권장 대체: '—' -> '-' · '⚠' -> '[주의]' · '✓' -> '완료'"
    )


def test_the_guard_actually_catches_something():
    """가드가 살아 있는지 — 위험 문자를 넣으면 실제로 걸리는지 확인한다."""
    for ch in ("—", "⚠", "✓"):
        with pytest.raises(UnicodeEncodeError):
            ch.encode("cp949")
    # 반대로 한글·중점·화살표는 CP949 에 있어 안전하다(불필요한 치환을 막기 위해 고정).
    for ch in ("가", "·", "→", "─"):
        ch.encode("cp949")
