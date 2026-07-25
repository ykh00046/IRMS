#!/usr/bin/env python3
"""Reproducible local bootstrap helper for IRMS."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import venv
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_VENV = ROOT / ".venv"


def venv_python(venv_dir: Path) -> Path:
    if os.name == "nt":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True, cwd=ROOT)


def create_venv(venv_dir: Path) -> Path:
    python_path = venv_python(venv_dir)
    if not python_path.exists():
        builder = venv.EnvBuilder(with_pip=True)
        builder.create(venv_dir)
    return python_path


def install_requirements(python_path: Path, requirements_file: Path) -> None:
    run([str(python_path), "-m", "pip", "install", "--upgrade", "pip"])
    run([str(python_path), "-m", "pip", "install", "-r", str(requirements_file)])


def run_smoke(python_path: Path) -> None:
    run(
        [
            str(python_path),
            "tools/smoke_irms.py",
            "--mode",
            "development",
            "--seed-demo-data",
            "--check-health",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Bootstrap IRMS into a local virtualenv")
    parser.add_argument("--venv-dir", default=str(DEFAULT_VENV))
    parser.add_argument("--skip-install", action="store_true")
    parser.add_argument("--run-smoke", action="store_true")
    args = parser.parse_args()

    venv_dir = Path(args.venv_dir).resolve()
    # 검증된 고정 버전(requirements-lock.txt)이 있으면 그것을 먼저 쓴다 — serve.py 의
    # _requirements_file 과 같은 규칙. 예전에는 부트스트랩만 requirements.txt(범위 버전)를
    # 써서, venv 를 새로 만드는 순간(운영 PC 교체·venv 손상 복구) 그날의 PyPI 최신이
    # 들어왔다. numpy 2.5.0 으로 운영이 멈춘 사고가 정확히 이 조건이었다 —
    # 잠금 정책이 가장 필요한 '백지 상태 설치'에서만 적용되지 않았다.
    lock_file = ROOT / "requirements-lock.txt"
    requirements_file = lock_file if lock_file.exists() else ROOT / "requirements.txt"

    python_path = create_venv(venv_dir)

    if not args.skip_install:
        install_requirements(python_path, requirements_file)

    if args.run_smoke:
        run_smoke(python_path)

    print("IRMS bootstrap complete")
    print(f"venv={venv_dir}")
    print(f"python={python_path}")
    print("start=run_irms.bat or run_irms_intranet.bat on Windows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
