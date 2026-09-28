"""Command-line entry point: `lce <command>`."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from lce import __version__


def _cmd_validate(args: argparse.Namespace) -> int:
    from lce.validate import validate_dir

    root = Path(args.path)
    if not root.is_dir():
        print(f"✗ {root} is not a directory")
        return 2
    checked, errors = validate_dir(root)
    if errors:
        print(f"✗ {len(errors)} validation error(s) in {checked} file(s)")
        for err in errors:
            print(err.render())
        return 1
    print(f"✓ {checked} file(s) valid")
    return 0


def _cmd_privacy_scan(args: argparse.Namespace) -> int:
    from lce.privacy.scan import main as scan_main

    return scan_main(["--root", args.root])


def _cmd_check_data_dir(args: argparse.Namespace) -> int:
    from lce.config.paths import DataDirError, resolve_data_dir

    try:
        path = resolve_data_dir(args.path)
    except DataDirError as exc:
        print(f"✗ {exc}")
        return 1
    print(f"✓ data directory OK: {path}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="lce", description="LinkedIn content engine")
    ap.add_argument("--version", action="version", version=f"lce {__version__}")
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("validate", help="validate a data directory against the schemas")
    p.add_argument("path")
    p.set_defaults(func=_cmd_validate)

    p = sub.add_parser("privacy-scan", help="scan this repository for private data")
    p.add_argument("--root", default=".")
    p.set_defaults(func=_cmd_privacy_scan)

    p = sub.add_parser("check-data-dir", help="verify the private data directory location")
    p.add_argument("path", nargs="?", default=None, help="defaults to $LCE_DATA_DIR")
    p.set_defaults(func=_cmd_check_data_dir)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
