"""check_repo_hygiene — 저장소 루트 1단계 위생 검사. 위반 시 exit 1.

로컬: python tools/check_repo_hygiene.py   (청소 후 상시 실행 가능)
CI  : .github/workflows/test.yml 스텝 — 커밋된 오염물 유입 차단.

역할 구분:
- CI(체크아웃 트리)는 추적 파일만 포함하므로 커밋된 오염물 유입을 차단한다.
- 로컬 재발은 본 스크립트의 수동 실행 + 산출물 경로 통일(`.tmp-tests/`)로 막는다.
본 스크립트는 검사만 수행하고 파일을 삭제/이동하지 않는다(청소는 별도 수동 작업).
"""

from __future__ import annotations

import sys
from pathlib import Path

# Windows 기본 콘솔(cp949)에서 한글·em-dash 출력 시 UnicodeEncodeError 로 죽는다.
# 검사 도구가 결과를 못 찍고 크래시하면 게이트 의미가 없으므로 출력 인코딩을 고정.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent

ALLOWED_DIRS = {
    ".antigravitycli", ".bkit", ".claude", ".codegraph", ".git", ".github",
    ".gstack", ".playwright-mcp", ".pytest_cache", ".ruff_cache", ".tmp-tests",
    ".venv", "artifacts", "backups", "cloudflared", "data", "docs", "excel",
    "scale_agent", "scripts", "src", "static", "templates", "tests", "tools",
    "tray_client",
}
BANNED_FILE_GLOBS = ("*.png", "*.jpg", "*.jpeg", "*.db")  # 루트 1단계 한정


def check(root: Path) -> list[str]:
    violations: list[str] = []
    for entry in root.iterdir():
        if entry.is_dir():
            if entry.name.startswith("tmp"):
                violations.append(f"root tmp dir: {entry.name}/")
            elif entry.name == "__pycache__":
                violations.append("root __pycache__/")
            elif entry.name.startswith(".venv-"):
                violations.append(f"stale venv backup: {entry.name}/")
            elif entry.name not in ALLOWED_DIRS:
                violations.append(f"unexpected root dir: {entry.name}/")
        else:
            for pattern in BANNED_FILE_GLOBS:
                if entry.match(pattern):
                    violations.append(f"root artifact file: {entry.name}")
                    break
    return violations


def main() -> int:
    violations = check(ROOT)
    for v in violations:
        print(f"HYGIENE FAIL: {v}")
    if violations:
        print(f"\n{len(violations)}건 — 산출물은 .tmp-tests/ 하위로, 자료는 docs/assets 또는 excel/legacy 로.")
        return 1
    print("repo hygiene OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
