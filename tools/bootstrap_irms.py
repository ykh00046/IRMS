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
    requirements_file = ROOT / "requirements.txt"

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
