#!/usr/bin/env python3
"""Minimal reproducible smoke check for IRMS."""

from __future__ import annotations

import argparse
import asyncio
import inspect
import os
import py_compile
import shutil
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def compile_python_sources() -> None:
    for path in sorted((ROOT / "src").rglob("*.py")):
        py_compile.compile(str(path), doraise=True)


def configure_env(args: argparse.Namespace) -> None:
    os.environ["IRMS_ENV"] = args.mode
    os.environ["IRMS_DATA_DIR"] = str(Path(args.data_dir).resolve())
    os.environ["IRMS_SEED_DEMO_DATA"] = "1" if args.seed_demo_data else "0"

    if args.mode != "development":
        if not args.session_secret:
            raise SystemExit("--session-secret is required outside development mode")
        os.environ["IRMS_SESSION_SECRET"] = args.session_secret


def smoke_import(check_health: bool) -> None:
    sys.path.insert(0, str(ROOT))

    try:
        from src.main import create_app  # noqa: PLC0415
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "Missing dependency during smoke import: "
            f"{exc.name}. Install requirements.txt before running the smoke check."
        ) from exc

    app = create_app()
    route_map = {route.path: route for route in app.routes}
    route_paths = set(route_map)
    assert "/health" in route_paths, "missing /health route"

    if check_health:
        route = route_map["/health"]
        endpoint = getattr(route, "endpoint", None)
        if endpoint is None:
            raise SystemExit("/health route is missing an endpoint")

        if inspect.iscoroutinefunction(endpoint):
            payload = asyncio.run(endpoint())
        else:
            payload = endpoint()

        if not isinstance(payload, dict) or payload.get("status") != "ok":
            raise SystemExit(f"/health returned unexpected payload: {payload!r}")


def main() -> int:
    parser = argparse.ArgumentParser(description="IRMS smoke check")
    parser.add_argument("--mode", choices=("development", "production"), default="development")
    parser.add_argument("--data-dir", default=str(ROOT / "tmp_smoke_runtime"))
    parser.add_argument("--session-secret")
    parser.add_argument("--seed-demo-data", action="store_true")
    parser.add_argument("--check-health", action="store_true")
    parser.add_argument("--clean", action="store_true")
    args = parser.parse_args()

    data_dir = Path(args.data_dir).resolve()
    if args.clean and data_dir.exists():
        shutil.rmtree(data_dir)

    configure_env(args)
    compile_python_sources()
    smoke_import(check_health=args.check_health)

    print("IRMS smoke check passed")
    print(f"mode={args.mode}")
    print(f"data_dir={data_dir}")
    print(f"seed_demo_data={args.seed_demo_data}")
    print(f"check_health={args.check_health}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
