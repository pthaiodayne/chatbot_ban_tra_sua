from __future__ import annotations

import argparse
import sys
import unittest
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from scripts.test_gemini import run_gemini_check


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run project checks.")
    parser.add_argument(
        "--suite",
        choices=["unit", "gemini", "all"],
        default="unit",
        help="Chọn nhóm check muốn chạy.",
    )
    parser.add_argument(
        "--message",
        help="Tin nhắn để test AI parse/reply. Bắt buộc khi chạy --suite gemini hoặc all.",
    )
    parser.add_argument(
        "--gemini-mode",
        choices=["parse", "reply", "both"],
        default="both",
        help="Chế độ test AI parse/reply.",
    )
    parser.add_argument(
        "--cart",
        default="Giỏ hàng đang trống.",
        help="Giỏ hàng giả lập khi test AI.",
    )
    return parser


def run_unit_tests() -> bool:
    suite = unittest.defaultTestLoader.discover(str(BASE_DIR / "tests" / "unit"), pattern="test_*.py")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return result.wasSuccessful()


def main() -> None:
    args = build_parser().parse_args()

    success = True

    if args.suite in {"unit", "all"}:
        print("=== RUNNING UNIT TESTS ===")
        success = run_unit_tests() and success

    if args.suite in {"gemini", "all"}:
        if not args.message:
            raise SystemExit("Cần truyền --message khi chạy suite Gemini.")
        print("\n=== RUNNING AI CHECK ===")
        run_gemini_check(message=args.message, mode=args.gemini_mode, cart=args.cart)

    if not success:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
