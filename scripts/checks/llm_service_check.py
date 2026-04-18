from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from app.llm_service import LLMService
from app.logging_config import setup_logging
from app.menu_service import MenuService

setup_logging()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a local LLM service check for the milk tea bot.")
    parser.add_argument(
        "--message",
        help="Tin nhắn khách hàng muốn test. Nếu bỏ trống, script sẽ hỏi nhập tương tác.",
    )
    parser.add_argument(
        "--mode",
        choices=["parse", "reply", "both"],
        default="both",
        help="Chọn chỉ test parse, chỉ test reply, hoặc cả hai.",
    )
    parser.add_argument(
        "--cart",
        default="Giỏ hàng đang trống.",
        help="Nội dung giỏ hàng giả lập để đưa vào prompt.",
    )
    return parser


def run_llm_check(*, message: str, mode: str = "both", cart: str = "Giỏ hàng đang trống.") -> None:
    menu_service = MenuService(str(BASE_DIR / "data" / "Menu.csv"))
    llm_service = LLMService()

    if not llm_service.is_enabled():
        raise SystemExit("Thiếu OPENAI_API_KEY trong file .env.")

    if not message:
        raise SystemExit("Tin nhắn test không được để trống.")

    menu_text = menu_service.get_menu_text()
    toppings_text = menu_service.get_toppings_text()
    cart_text = cart

    if mode in {"parse", "both"}:
        parsed = llm_service.parse_customer_message(
            user_message=message,
            menu_text=menu_text,
            toppings_text=toppings_text,
            cart_text=cart_text,
        )
        print("=== PARSE RESULT ===")
        print(json.dumps(parsed, ensure_ascii=False, indent=2))

    if mode in {"reply", "both"}:
        reply = llm_service.generate_customer_reply(
            user_message=message,
            menu_text=menu_text,
            toppings_text=toppings_text,
            cart_text=cart_text,
        )
        print("\n=== REPLY RESULT ===")
        print(reply)


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    message = args.message or input("Nhập tin nhắn khách hàng: ").strip()
    run_llm_check(message=message, mode=args.mode, cart=args.cart)


if __name__ == "__main__":
    main()
