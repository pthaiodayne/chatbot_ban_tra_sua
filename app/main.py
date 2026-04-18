from __future__ import annotations

from pathlib import Path
import logging
import re

from fastapi import FastAPI, Request

from app.config import BASE_URL
from app.chat_service import get_recent_chat_messages, render_chat_history, save_chat_message
from app.customer_service import get_or_create_customer_profile, update_customer_profile
from app.database import Base, SessionLocal, engine
from app.logging_config import setup_logging
from app.llm_service import LLMService
from app.menu_service import MenuService
from app.models import Order
from app.order_service import (
    add_item_to_order,
    get_or_create_active_order,
    remove_item_from_order,
    render_cart,
    render_order_summary,
    update_order_item,
    update_customer_info,
)
from app.telegram_service import TelegramConfigError, send_message, set_webhook

BASE_DIR = Path(__file__).resolve().parent.parent
menu_service = MenuService(str(BASE_DIR / "data" / "Menu.csv"))
llm_service = LLMService()
setup_logging()

app = FastAPI(title="Milk Tea Bot")
Base.metadata.create_all(bind=engine)
logger = logging.getLogger("llm_service")


ACTIONABLE_KEYWORDS = (
    "cho mình",
    "cho em",
    "thêm",
    "bỏ",
    "xóa",
    "xoa",
    "đổi",
    "doi",
    "cập nhật",
    "cap nhat",
    "món số",
    "mon so",
    "giỏ",
    "gio",
    "size",
    "ly",
    "trân châu",
    "tran chau",
    "kem tươi",
    "kem tuoi",
    "ít đá",
    "it da",
    "ít đường",
    "it duong",
    "checkout",
    "thanh toán",
    "thanh toan",
    "+",
    "-"
)

PAYMENT_CONFIRM_KEYWORDS = (
    "xác nhận thanh toán",
    "xac nhan thanh toan",
    "xác nhận trả tiền",
    "xac nhan tra tien",
    "đồng ý thanh toán",
    "dong y thanh toan",
    "ok thanh toán",
    "ok thanh toan",
)

PAYMENT_REQUEST_KEYWORDS = (
    "thanh toán",
    "thanh toan",
    "trả tiền",
    "tra tien",
    "chuyển khoản",
    "chuyen khoan",
    "/pay",
)

CHECKOUT_REQUEST_KEYWORDS = (
    "chốt đơn",
    "chot don",
    "checkout",
    "đặt đơn",
    "dat don",
)


def looks_like_actionable_message(text: str) -> bool:
    normalized = text.strip().lower()
    return any(keyword in normalized for keyword in ACTIONABLE_KEYWORDS)


def is_payment_confirmation_message(text: str) -> bool:
    normalized = text.strip().lower()
    return normalized == "/confirm_pay" or any(keyword in normalized for keyword in PAYMENT_CONFIRM_KEYWORDS)


def looks_like_payment_request(text: str) -> bool:
    normalized = text.strip().lower()
    return any(keyword in normalized for keyword in PAYMENT_REQUEST_KEYWORDS)


def looks_like_checkout_request(text: str) -> bool:
    normalized = text.strip().lower()
    return any(keyword in normalized for keyword in CHECKOUT_REQUEST_KEYWORDS)


def extract_explicit_item_index(text: str) -> int | None:
    normalized = text.strip().lower()
    patterns = (
        r"(?:m[oó]n|mon|ly)\s*s[oố]\s*(\d+)",
        r"(?:m[oó]n|mon|ly)\s*(\d+)",
        r"s[oố]\s*(\d+)",
    )
    for pattern in patterns:
        match = re.search(pattern, normalized)
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                return None
    return None


def compose_reply(
    primary_reply: str | None,
    fallback_reply: str,
    *sections: str | None,
) -> str:
    parts: list[str] = []
    opening = (primary_reply or "").strip() or fallback_reply.strip()
    if opening:
        parts.append(opening)

    for section in sections:
        value = (section or "").strip()
        if value and value not in parts:
            parts.append(value)

    return "\n\n".join(parts)


def build_natural_action_reply(
    *,
    user_message: str,
    suggested_reply: str | None,
    system_result: str,
    menu_text: str,
    toppings_text: str,
    cart_text: str,
    conversation_history: str,
    fallback_reply: str,
) -> str:
    try:
        return llm_service.generate_action_reply(
            user_message=user_message,
            suggested_reply=suggested_reply or "",
            system_result=system_result,
            menu_text=menu_text,
            toppings_text=toppings_text,
            cart_text=cart_text,
            conversation_history=conversation_history,
        )
    except Exception:
        return (suggested_reply or "").strip() or fallback_reply


def mark_order_paid(db, order: Order) -> None:
    order.payment_status = "paid"
    order.order_status = "paid"
    db.commit()
    db.refresh(order)


def has_complete_customer_info(order: Order) -> bool:
    return bool(order.customer_name and order.phone and order.address)


def render_payment_confirmation(order: Order) -> str:
    return (
        "Mình lấy thông tin đặt hàng hiện tại từ hồ sơ của bạn trong hệ thống như sau:\n\n"
        f"{render_order_summary(order)}\n\n"
        "Nếu đúng, nhắn /confirm_pay hoặc 'xác nhận thanh toán'.\n"
        "Nếu cần sửa, bạn chỉ cần nhắn lại tên, số điện thoại của bạn hoặc địa chỉ mới."
    )


def maybe_advance_order_after_customer_info(
    db,
    order: Order,
    *,
    user_message: str,
    conversation_history: str,
) -> str | None:
    if not order.items or order.payment_status == "paid" or not has_complete_customer_info(order):
        return None

    normalized_context = f"{conversation_history}\n{user_message}".lower()
    wants_payment = looks_like_payment_request(normalized_context)
    wants_checkout = looks_like_checkout_request(normalized_context) or wants_payment

    if not wants_checkout:
        return None

    if order.order_status == "draft":
        order.order_status = "confirmed"

    if wants_payment and order.order_status == "confirmed":
        order.order_status = "awaiting_payment_confirmation"

    db.commit()
    db.refresh(order)

    if order.order_status == "awaiting_payment_confirmation":
        return render_payment_confirmation(order)

    return render_order_summary(order)


def sync_customer_info(
    db,
    telegram_user_id: str,
    order: Order,
    *,
    name: str | None = None,
    phone: str | None = None,
    address: str | None = None,
    note: str | None = None,
) -> None:
    update_customer_info(
        order,
        name=name,
        phone=phone,
        address=address,
        note=note,
    )
    profile = get_or_create_customer_profile(db, telegram_user_id)
    update_customer_profile(
        profile,
        name=name,
        phone=phone,
        address=address,
    )
    db.commit()
    db.refresh(order)


def send_bot_message(db, chat_id: int, telegram_user_id: str, text: str) -> None:
    send_message(chat_id, text)
    save_chat_message(db, telegram_user_id, "assistant", text)


@app.on_event("startup")
def startup_event() -> None:
    if not BASE_URL:
        print("BASE_URL chưa cấu hình, bỏ qua bước set webhook.")
        return
    try:
        webhook_url = f"{BASE_URL.rstrip('/')}/telegram/webhook"
        result = set_webhook(webhook_url)
        print("Webhook result:", result)
    except TelegramConfigError as exc:
        print(str(exc))
    except Exception as exc:  # noqa: BLE001
        print("Không set được webhook:", exc)


@app.get("/")
def root() -> dict:
    return {"message": "Milk tea bot backend is running"}


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/orders")
def list_orders() -> list[dict]:
    db = SessionLocal()
    try:
        orders = db.query(Order).order_by(Order.id.desc()).all()
        return [
            {
                "id": o.id,
                "telegram_user_id": o.telegram_user_id,
                "customer_name": o.customer_name,
                "phone": o.phone,
                "address": o.address,
                "subtotal": o.subtotal,
                "total": o.total,
                "payment_status": o.payment_status,
                "order_status": o.order_status,
            }
            for o in orders
        ]
    finally:
        db.close()


@app.post("/telegram/webhook")
async def telegram_webhook(request: Request) -> dict:
    update = await request.json()
    message = update.get("message") or {}
    chat = message.get("chat") or {}
    from_user = message.get("from") or {}

    chat_id = chat.get("id")
    telegram_user_id = str(from_user.get("id", ""))
    text = (message.get("text") or "").strip()

    if not chat_id or not text:
        return {"ok": True}

    db = SessionLocal()
    try:
        conversation_history = render_chat_history(get_recent_chat_messages(db, telegram_user_id))
        save_chat_message(db, telegram_user_id, "user", text)

        if text == "/start":
            send_bot_message(
                db,
                chat_id,
                telegram_user_id,
                "Chào bạn \n"
                "Các lệnh hiện có:\n"
                "/menu - xem menu đồ uống\n"
                "/toppings - xem topping\n"
                "/add <tên món> - <size> - <số lượng> - <topping1,topping2>\n"
                "Ví dụ: /add Trà Sữa Truyền Thống - L - 2 - Kem Tươi,Trân Châu Đen\n"
                "/cart - xem giỏ hàng\n"
                "/name <tên khách>\n"
                "/phone <số điện thoại của bạn>\n"
                "/address <địa chỉ>\n"
                "/note <ghi chú>\n"
                "/checkout - chốt đơn\n"
                "/pay - xem lại thông tin trước khi thanh toán\n"
                "/confirm_pay - xác nhận thanh toán\n"
                "/summary - xem tóm tắt đơn\n\n"
                "Bạn cũng có thể nhắn tự nhiên, ví dụ:\n"
                '"Cho mình 2 trà sữa truyền thống size L thêm kem tươi"\n'
                '"Thêm trân châu vào món số 2"\n'
                '"Bỏ trân châu khỏi món số 2"\n'
                '"Đổi món số 2 sang size L, ít đá"\n'
                '"Bỏ món số 1 khỏi giỏ"\n'
                '"Tên mình là An, số điện thoại của mình là 09..., địa chỉ ..."',
            )

        elif text == "/menu":
            send_bot_message(
                db,
                chat_id,
                telegram_user_id,
                f"Menu hiện tại:\n{menu_service.get_menu_text()}\n\n{menu_service.get_toppings_text()}",
            )

        elif text == "/toppings":
            send_bot_message(db, chat_id, telegram_user_id, menu_service.get_toppings_text())

        elif text == "/cart":
            order = get_or_create_active_order(db, telegram_user_id)
            send_bot_message(db, chat_id, telegram_user_id, render_cart(order))

        elif text.startswith("/name "):
            order = get_or_create_active_order(db, telegram_user_id)
            sync_customer_info(db, telegram_user_id, order, name=text.replace("/name ", "", 1).strip())
            reply_text = "Đã lưu tên khách."
            if order.order_status == "awaiting_payment_confirmation":
                reply_text += "\n\n" + render_payment_confirmation(order)
            send_bot_message(db, chat_id, telegram_user_id, reply_text)

        elif text.startswith("/phone "):
            order = get_or_create_active_order(db, telegram_user_id)
            sync_customer_info(db, telegram_user_id, order, phone=text.replace("/phone ", "", 1).strip())
            reply_text = "Đã lưu số điện thoại của bạn."
            if order.order_status == "awaiting_payment_confirmation":
                reply_text += "\n\n" + render_payment_confirmation(order)
            send_bot_message(db, chat_id, telegram_user_id, reply_text)

        elif text.startswith("/address "):
            order = get_or_create_active_order(db, telegram_user_id)
            sync_customer_info(db, telegram_user_id, order, address=text.replace("/address ", "", 1).strip())
            reply_text = "Đã lưu địa chỉ."
            if order.order_status == "awaiting_payment_confirmation":
                reply_text += "\n\n" + render_payment_confirmation(order)
            send_bot_message(db, chat_id, telegram_user_id, reply_text)

        elif text.startswith("/note "):
            order = get_or_create_active_order(db, telegram_user_id)
            update_customer_info(order, note=text.replace("/note ", "", 1).strip())
            db.commit()
            send_bot_message(db, chat_id, telegram_user_id, "Đã lưu ghi chú.")

        elif text.startswith("/add"):
            payload = text.replace("/add", "", 1).strip()
            parts = [p.strip() for p in payload.split(" - ")]
            if len(parts) < 3:
                send_bot_message(
                    db,
                    chat_id,
                    telegram_user_id,
                    "Cú pháp đúng:\n/add <tên món> - <size> - <số lượng> - <topping1,topping2>",
                )
                return {"ok": True}

            item_name = parts[0]
            size = parts[1]
            try:
                quantity = int(parts[2])
                if quantity <= 0:
                    raise ValueError
            except ValueError:
                send_bot_message(db, chat_id, telegram_user_id, "Số lượng phải là số nguyên dương.")
                return {"ok": True}

            toppings = []
            if len(parts) >= 4 and parts[3]:
                toppings = [x.strip() for x in parts[3].split(",") if x.strip()]

            item = menu_service.find_item(item_name, size)
            if not item:
                send_bot_message(
                    db,
                    chat_id,
                    telegram_user_id,
                    "Không tìm thấy món hoặc size hợp lệ trong menu. Size hợp lệ hiện tại là M hoặc L.",
                )
                return {"ok": True}

            ok, invalid_toppings, matched_toppings = menu_service.are_valid_toppings(toppings)
            if not ok:
                send_bot_message(db, chat_id, telegram_user_id, f"Topping không hợp lệ: {', '.join(invalid_toppings)}")
                return {"ok": True}

            order = get_or_create_active_order(db, telegram_user_id)
            topping_price_map = menu_service.get_topping_price_map(matched_toppings)
            canonical_topping_names = [t["name"] for t in matched_toppings]

            add_item_to_order(
                db=db,
                order=order,
                item_name=item["name"],
                size=item["size"],
                quantity=quantity,
                toppings=canonical_topping_names,
                unit_price=int(item["price"]),
                topping_price_map=topping_price_map,
            )
            db.refresh(order)
            send_bot_message(db, chat_id, telegram_user_id, "Đã thêm món vào giỏ.\n" + render_cart(order))

        elif text == "/checkout":
            order = get_or_create_active_order(db, telegram_user_id)
            if not order.id or order.payment_status == "paid":
                send_bot_message(db, chat_id, telegram_user_id, "Giỏ hàng đang trống hoặc đơn hàng đã được thanh toán.")
                return {"ok": True}

            missing = []
            if not order.customer_name:
                missing.append("tên khách")
            if not order.phone:
                missing.append("số điện thoại")
            if not order.address:
                missing.append("địa chỉ")

            if missing:
                send_bot_message(
                    db,
                    chat_id,
                    telegram_user_id,
                    "Mình chưa đủ thông tin giao hàng để chốt đơn. Bạn còn thiếu: "
                    + ", ".join(missing)
                    + ".\nBạn có thể nhắn tự nhiên hoặc dùng /name, /phone, /address.",
                )
                return {"ok": True}

            order.order_status = "confirmed"
            db.commit()
            db.refresh(order)
            send_bot_message(
                db,
                chat_id,
                telegram_user_id,
                "Mình đã chốt đơn tạm thời cho bạn.\n\n"
                + render_order_summary(order)
                + "\n\nDùng /pay để xem lại thông tin trong hệ thống trước khi xác nhận thanh toán.",
            )

        elif text == "/pay":
            order = get_or_create_active_order(db, telegram_user_id)
            if not order.id or order.payment_status == "paid":
                send_bot_message(db, chat_id, telegram_user_id, "Giỏ hàng đang trống hoặc đơn hàng đã được thanh toán.")
                return {"ok": True}

            if order.order_status != "confirmed":
                send_bot_message(db, chat_id, telegram_user_id, "Bạn cần /checkout trước khi thanh toán.")
                return {"ok": True}

            if not has_complete_customer_info(order):
                send_bot_message(
                    db,
                    chat_id,
                    telegram_user_id,
                    "Mình chưa đủ thông tin để xác nhận thanh toán. Bạn vui lòng cập nhật tên, số điện thoại của bạn và địa chỉ giúp mình.",
                )
                return {"ok": True}

            order.order_status = "awaiting_payment_confirmation"
            db.commit()
            db.refresh(order)
            confirmation_text = render_payment_confirmation(order)
            reply_text = build_natural_action_reply(
                user_message=text,
                suggested_reply="",
                system_result="Hệ thống đã chuyển đơn sang bước chờ khách xác nhận thanh toán.",
                menu_text=menu_service.get_menu_text(),
                toppings_text=menu_service.get_toppings_text(),
                cart_text=confirmation_text,
                conversation_history=conversation_history,
                fallback_reply="Mình lấy thông tin đặt hàng hiện tại từ hồ sơ của bạn trong hệ thống để bạn xác nhận lại trước khi thanh toán.",
            )
            send_bot_message(
                db,
                chat_id,
                telegram_user_id,
                compose_reply(reply_text, reply_text, confirmation_text),
            )

        elif text == "/confirm_pay":
            order = get_or_create_active_order(db, telegram_user_id)
            if not order.id or order.payment_status == "paid":
                reply_text = build_natural_action_reply(
                    user_message=text,
                    suggested_reply="",
                    system_result="Hệ thống không có đơn nào đang chờ thanh toán để xác nhận.",
                    menu_text=menu_service.get_menu_text(),
                    toppings_text=menu_service.get_toppings_text(),
                    cart_text=render_cart(order),
                    conversation_history=conversation_history,
                    fallback_reply="Không có đơn nào đang chờ thanh toán.",
                )
                send_bot_message(db, chat_id, telegram_user_id, reply_text)
                return {"ok": True}

            if order.order_status != "awaiting_payment_confirmation":
                reply_text = build_natural_action_reply(
                    user_message=text,
                    suggested_reply="",
                    system_result="Hệ thống chưa ở bước xác nhận thanh toán vì khách chưa vào bước /pay.",
                    menu_text=menu_service.get_menu_text(),
                    toppings_text=menu_service.get_toppings_text(),
                    cart_text=render_cart(order),
                    conversation_history=conversation_history,
                    fallback_reply="Bạn cần dùng /pay trước để xem lại thông tin và xác nhận thanh toán.",
                )
                send_bot_message(db, chat_id, telegram_user_id, reply_text)
                return {"ok": True}

            mark_order_paid(db, order)
            summary_text = render_order_summary(order)
            reply_text = build_natural_action_reply(
                user_message=text,
                suggested_reply="",
                system_result="Hệ thống đã xác nhận thanh toán thành công cho đơn hàng.",
                menu_text=menu_service.get_menu_text(),
                toppings_text=menu_service.get_toppings_text(),
                cart_text=summary_text,
                conversation_history=conversation_history,
                fallback_reply="Thanh toán thành công.",
            )
            send_bot_message(
                db,
                chat_id,
                telegram_user_id,
                compose_reply(reply_text, "Thanh toán thành công.", summary_text, "Tiệm Trà Sữa Của Mẹ cảm ơn bạn đã đặt hàng!\n\n Nhấn /start để bắt đầu đơn hàng mới."),
            )

        elif text == "/summary":
            order = get_or_create_active_order(db, telegram_user_id)
            send_bot_message(db, chat_id, telegram_user_id, render_order_summary(order))

        else:
            order = get_or_create_active_order(db, telegram_user_id)
            menu_text = menu_service.get_menu_text()
            toppings_text = menu_service.get_toppings_text()
            cart_text = render_cart(order)

            if is_payment_confirmation_message(text):
                if not order.id or order.payment_status == "paid":
                    reply_text = build_natural_action_reply(
                        user_message=text,
                        suggested_reply="",
                        system_result="Hệ thống không có đơn nào đang chờ thanh toán để xác nhận.",
                        menu_text=menu_text,
                        toppings_text=toppings_text,
                        cart_text=cart_text,
                        conversation_history=conversation_history,
                        fallback_reply="Không có đơn nào đang chờ thanh toán.",
                    )
                    send_bot_message(db, chat_id, telegram_user_id, reply_text)
                    return {"ok": True}

                if order.order_status != "awaiting_payment_confirmation":
                    reply_text = build_natural_action_reply(
                        user_message=text,
                        suggested_reply="",
                        system_result="Hệ thống chưa ở bước xác nhận thanh toán vì khách chưa vào bước /pay.",
                        menu_text=menu_text,
                        toppings_text=toppings_text,
                        cart_text=cart_text,
                        conversation_history=conversation_history,
                        fallback_reply="Mình chưa ở bước xác nhận thanh toán. Bạn dùng /pay trước để mình hiển thị lại thông tin từ hệ thống nhé.",
                    )
                    send_bot_message(db, chat_id, telegram_user_id, reply_text)
                    return {"ok": True}

                mark_order_paid(db, order)
                summary_text = render_order_summary(order)
                reply_text = build_natural_action_reply(
                    user_message=text,
                    suggested_reply="",
                    system_result="Hệ thống đã xác nhận thanh toán thành công cho đơn hàng.",
                    menu_text=menu_text,
                    toppings_text=toppings_text,
                    cart_text=summary_text,
                    conversation_history=conversation_history,
                    fallback_reply="Thanh toán thành công.",
                )
                send_bot_message(
                    db,
                    chat_id,
                    telegram_user_id,
                    compose_reply(reply_text, "Thanh toán thành công.", summary_text, "Tiệm Trà Sữa Của Mẹ cảm ơn bạn đã đặt hàng!\n\n Nhấn /start để bắt đầu đơn hàng mới."),
                )
                return {"ok": True}

            if not llm_service.is_enabled():
                send_bot_message(
                    db,
                    chat_id,
                    telegram_user_id,
                    "Mình chưa hiểu lệnh này.\n"
                    "Hiện tại LLM chưa được cấu hình hoặc có lỗi, mình chưa xử lý được tin nhắn tự nhiên đâu. Bạn thử lại sau nhé.\n"
                    "Bạn dùng /menu, /toppings, /add, /cart, /name, /phone, /address, /note, /checkout giúp mình nhé!\n"
                    "Tiệm Trà Sữa Của Mẹ cảm ơn sự thông cảm và ủng hộ của bạn!",
                )
                return {"ok": True}

            try:
                ai_result = llm_service.parse_customer_message(
                    user_message=text,
                    menu_text=menu_text,
                    toppings_text=toppings_text,
                    cart_text=cart_text,
                    conversation_history=conversation_history,
                )
            except Exception:
                logger.exception("Failed to parse customer message with LLM.")
                if looks_like_actionable_message(text):
                    send_bot_message(
                        db,
                        chat_id,
                        telegram_user_id,
                        "Mình chưa hiểu chính xác thao tác bạn muốn làm với đơn hàng.\n"
                        "Bạn nói rõ hơn giúp mình tên món, size, số lượng hoặc số thứ tự món trong giỏ nhé.\n"
                        'Ví dụ: "Cho mình 1 trà xoài size M", "Thêm trân châu vào món số 2", "Bỏ món số 1".',
                    )
                    return {"ok": True}

                ai_reply = llm_service.generate_customer_reply(
                    user_message=text,
                    menu_text=menu_text,
                    toppings_text=toppings_text,
                    cart_text=cart_text,
                    conversation_history=conversation_history,
                )
                send_bot_message(
                    db,
                    chat_id,
                    telegram_user_id,
                    ai_reply or "Mình chưa xử lý được yêu cầu này, bạn thử lại giúp mình.",
                )
                return {"ok": True}

            intent = ai_result.get("intent")
            if looks_like_payment_request(text) and not is_payment_confirmation_message(text):
                intent = "pay"

            if intent == "add_item":
                add_item = ai_result.get("add_item") or {}
                parsed_items = add_item.get("items") or []
                shared_toppings = [str(x).strip() for x in (add_item.get("shared_toppings") or []) if str(x).strip()]

                if not parsed_items:
                    reply_text = build_natural_action_reply(
                        user_message=text,
                        suggested_reply=ai_result.get("reply"),
                        system_result="Hệ thống chưa tách được món nào hợp lệ từ tin nhắn của khách.",
                        menu_text=menu_text,
                        toppings_text=toppings_text,
                        cart_text=cart_text,
                        conversation_history=conversation_history,
                        fallback_reply="Mình chưa tách được món cần thêm vào giỏ. Bạn nhắn rõ từng món và size giúp mình nhé.",
                    )
                    send_bot_message(
                        db,
                        chat_id,
                        telegram_user_id,
                        reply_text,
                    )
                    return {"ok": True}

                prepared_items: list[dict] = []
                for parsed_item in parsed_items:
                    item_name = (parsed_item.get("item_name") or "").strip()
                    size = (parsed_item.get("size") or "").strip()
                    quantity = int(parsed_item.get("quantity") or 1)
                    item_toppings = [
                        str(x).strip()
                        for x in (parsed_item.get("toppings") or [])
                        if str(x).strip()
                    ]
                    combined_toppings = item_toppings + shared_toppings

                    item = menu_service.find_item(item_name, size) if item_name and size else None
                    if not item:
                        reply_text = build_natural_action_reply(
                            user_message=text,
                            suggested_reply=ai_result.get("reply"),
                            system_result=f"Hệ thống không tìm thấy món hoặc size hợp lệ cho mục '{item_name}' với size '{size}'.",
                            menu_text=menu_text,
                            toppings_text=toppings_text,
                            cart_text=cart_text,
                            conversation_history=conversation_history,
                            fallback_reply="Mình chưa xác định được một trong các món hoặc size hợp lệ trong câu vừa rồi.",
                        )
                        send_bot_message(
                            db,
                            chat_id,
                            telegram_user_id,
                            reply_text,
                        )
                        return {"ok": True}

                    ok, invalid_toppings, matched_toppings = menu_service.are_valid_toppings(combined_toppings)
                    if not ok:
                        reply_text = build_natural_action_reply(
                            user_message=text,
                            suggested_reply=ai_result.get("reply"),
                            system_result=f"Hệ thống phát hiện topping không hợp lệ: {', '.join(invalid_toppings)}.",
                            menu_text=menu_text,
                            toppings_text=toppings_text,
                            cart_text=cart_text,
                            conversation_history=conversation_history,
                            fallback_reply=f"Topping không hợp lệ: {', '.join(invalid_toppings)}",
                        )
                        send_bot_message(db, chat_id, telegram_user_id, reply_text)
                        return {"ok": True}

                    prepared_items.append(
                        {
                            "item": item,
                            "quantity": quantity,
                            "toppings": [t["name"] for t in matched_toppings],
                            "topping_price_map": menu_service.get_topping_price_map(matched_toppings),
                        }
                    )

                for prepared_item in prepared_items:
                    add_item_to_order(
                        db=db,
                        order=order,
                        item_name=prepared_item["item"]["name"],
                        size=prepared_item["item"]["size"],
                        quantity=prepared_item["quantity"],
                        toppings=prepared_item["toppings"],
                        unit_price=int(prepared_item["item"]["price"]),
                        topping_price_map=prepared_item["topping_price_map"],
                    )

                db.refresh(order)
                updated_cart_text = render_cart(order)
                reply_text = build_natural_action_reply(
                    user_message=text,
                    suggested_reply=ai_result.get("reply"),
                    system_result="Hệ thống đã thêm món vào giỏ hàng thành công.",
                    menu_text=menu_text,
                    toppings_text=toppings_text,
                    cart_text=updated_cart_text,
                    conversation_history=conversation_history,
                    fallback_reply="Mình đã thêm món vào giỏ.",
                )
                send_bot_message(
                    db,
                    chat_id,
                    telegram_user_id,
                    compose_reply(
                        reply_text,
                        "Mình đã thêm món vào giỏ.",
                        updated_cart_text,
                    ),
                )

            elif intent == "update_item":
                if not order.items:
                    reply_text = build_natural_action_reply(
                        user_message=text,
                        suggested_reply=ai_result.get("reply"),
                        system_result="Hệ thống không thể cập nhật món vì giỏ hàng đang trống.",
                        menu_text=menu_text,
                        toppings_text=toppings_text,
                        cart_text=cart_text,
                        conversation_history=conversation_history,
                        fallback_reply="Giỏ hàng đang trống.",
                    )
                    send_bot_message(db, chat_id, telegram_user_id, reply_text)
                    return {"ok": True}

                update_item = ai_result.get("update_item") or {}
                item_index = update_item.get("cart_item_index")
                explicit_item_index = extract_explicit_item_index(text)
                if explicit_item_index is not None:
                    item_index = explicit_item_index
                item_name = (update_item.get("item_name") or "").strip() or None
                current_size = (update_item.get("size") or "").strip() or None
                new_size = (update_item.get("new_size") or "").strip() or None
                quantity = update_item.get("quantity")
                sugar_level = (update_item.get("sugar_level") or "").strip() or None
                ice_level = (update_item.get("ice_level") or "").strip() or None
                add_toppings = [str(x).strip() for x in (update_item.get("add_toppings") or []) if str(x).strip()]
                remove_toppings = [str(x).strip() for x in (update_item.get("remove_toppings") or []) if str(x).strip()]

                all_toppings = add_toppings + remove_toppings
                ok, invalid_toppings, matched_toppings = menu_service.are_valid_toppings(all_toppings)
                if not ok:
                    reply_text = build_natural_action_reply(
                        user_message=text,
                        suggested_reply=ai_result.get("reply"),
                        system_result=f"Hệ thống phát hiện topping không hợp lệ: {', '.join(invalid_toppings)}.",
                        menu_text=menu_text,
                        toppings_text=toppings_text,
                        cart_text=cart_text,
                        conversation_history=conversation_history,
                        fallback_reply=f"Topping không hợp lệ: {', '.join(invalid_toppings)}",
                    )
                    send_bot_message(db, chat_id, telegram_user_id, reply_text)
                    return {"ok": True}

                topping_price_map = menu_service.get_all_topping_price_map()
                canonical_add_toppings = []
                canonical_remove_toppings = []
                for topping in add_toppings:
                    found = menu_service.find_topping(topping)
                    if found:
                        canonical_add_toppings.append(found["name"])
                for topping in remove_toppings:
                    found = menu_service.find_topping(topping)
                    if found:
                        canonical_remove_toppings.append(found["name"])

                updated_base_unit_price = None
                normalized_new_size = None
                if new_size:
                    target_lookup_name = item_name
                    if not target_lookup_name and item_index is not None and 0 < item_index <= len(order.items):
                        target_lookup_name = order.items[item_index - 1].item_name
                    if not target_lookup_name:
                        reply_text = build_natural_action_reply(
                            user_message=text,
                            suggested_reply=ai_result.get("reply"),
                            system_result="Hệ thống chưa xác định được món cần đổi size.",
                            menu_text=menu_text,
                            toppings_text=toppings_text,
                            cart_text=cart_text,
                            conversation_history=conversation_history,
                            fallback_reply="Mình chưa xác định được món cần đổi size.",
                        )
                        send_bot_message(db, chat_id, telegram_user_id, reply_text)
                        return {"ok": True}
                    updated_menu_item = menu_service.find_item(target_lookup_name, new_size)
                    if not updated_menu_item:
                        reply_text = build_natural_action_reply(
                            user_message=text,
                            suggested_reply=ai_result.get("reply"),
                            system_result=f"Hệ thống không tìm thấy size mới hợp lệ '{new_size}' cho món '{target_lookup_name}'.",
                            menu_text=menu_text,
                            toppings_text=toppings_text,
                            cart_text=cart_text,
                            conversation_history=conversation_history,
                            fallback_reply="Không tìm thấy size mới hợp lệ cho món này. Size hợp lệ hiện tại là M hoặc L.",
                        )
                        send_bot_message(db, chat_id, telegram_user_id, reply_text)
                        return {"ok": True}
                    normalized_new_size = updated_menu_item["size"]
                    updated_base_unit_price = int(updated_menu_item["price"])

                try:
                    updated = update_order_item(
                        db,
                        order,
                        item_index=item_index,
                        item_name=item_name,
                        size=current_size,
                        quantity=quantity,
                        sugar_level=sugar_level,
                        ice_level=ice_level,
                        toppings_to_add=canonical_add_toppings,
                        toppings_to_remove=canonical_remove_toppings,
                        topping_price_map=topping_price_map,
                        updated_item_size=normalized_new_size,
                        updated_base_unit_price=updated_base_unit_price,
                    )
                except ValueError as exc:
                    reply_text = build_natural_action_reply(
                        user_message=text,
                        suggested_reply=ai_result.get("reply"),
                        system_result=f"Hệ thống không thể cập nhật món: {exc}",
                        menu_text=menu_text,
                        toppings_text=toppings_text,
                        cart_text=cart_text,
                        conversation_history=conversation_history,
                        fallback_reply=str(exc),
                    )
                    send_bot_message(db, chat_id, telegram_user_id, reply_text)
                    return {"ok": True}

                if not updated:
                    reply_text = build_natural_action_reply(
                        user_message=text,
                        suggested_reply=ai_result.get("reply"),
                        system_result="Hệ thống chưa xác định được món cần cập nhật trong giỏ.",
                        menu_text=menu_text,
                        toppings_text=toppings_text,
                        cart_text=cart_text,
                        conversation_history=conversation_history,
                        fallback_reply="Mình chưa xác định được món cần cập nhật trong giỏ. Bạn nói rõ số thứ tự món hoặc tên món giúp mình nhé.",
                    )
                    send_bot_message(
                        db,
                        chat_id,
                        telegram_user_id,
                        reply_text,
                    )
                    return {"ok": True}

                updated_cart_text = render_cart(order)
                if (
                    not updated["added_toppings"]
                    and not updated["removed_toppings"]
                    and quantity is None
                    and sugar_level is None
                    and ice_level is None
                    and normalized_new_size is None
                ):
                    send_bot_message(
                        db,
                        chat_id,
                        telegram_user_id,
                        compose_reply(
                            build_natural_action_reply(
                                user_message=text,
                                suggested_reply=ai_result.get("reply"),
                                system_result=f"Hệ thống không ghi nhận thay đổi nào cho {updated['item_name']} size {updated['size']}.",
                                menu_text=menu_text,
                                toppings_text=toppings_text,
                                cart_text=updated_cart_text,
                                conversation_history=conversation_history,
                                fallback_reply=f"Mình chưa thấy thay đổi nào cho {updated['item_name']} size {updated['size']}.",
                            ),
                            f"Mình chưa thấy thay đổi nào cho {updated['item_name']} size {updated['size']}.",
                            updated_cart_text,
                        ),
                    )
                    return {"ok": True}

                actions = []
                if normalized_new_size is not None:
                    actions.append(f"đổi size thành {updated['size']}")
                if quantity is not None:
                    actions.append(f"đổi số lượng thành {updated['quantity']}")
                if sugar_level is not None:
                    actions.append(f"đổi đường thành {updated['sugar_level']}")
                if ice_level is not None:
                    actions.append(f"đổi đá thành {updated['ice_level']}")
                if updated["added_toppings"]:
                    actions.append(f"thêm {', '.join(updated['added_toppings'])}")
                if updated["removed_toppings"]:
                    actions.append(f"bỏ {', '.join(updated['removed_toppings'])}")
                send_bot_message(
                    db,
                    chat_id,
                    telegram_user_id,
                    compose_reply(
                        build_natural_action_reply(
                            user_message=text,
                            suggested_reply=ai_result.get("reply"),
                            system_result=f"Hệ thống đã cập nhật món với các thay đổi: {', '.join(actions)}.",
                            menu_text=menu_text,
                            toppings_text=toppings_text,
                            cart_text=updated_cart_text,
                            conversation_history=conversation_history,
                            fallback_reply=f"Mình đã {' và '.join(actions)} cho {updated['item_name']} size {updated['size']}.",
                        ),
                        f"Mình đã {' và '.join(actions)} cho {updated['item_name']} size {updated['size']}.",
                        updated_cart_text,
                    ),
                )

            elif intent == "remove_item":
                if not order.items:
                    reply_text = build_natural_action_reply(
                        user_message=text,
                        suggested_reply=ai_result.get("reply"),
                        system_result="Hệ thống không thể xóa món vì giỏ hàng đang trống.",
                        menu_text=menu_text,
                        toppings_text=toppings_text,
                        cart_text=cart_text,
                        conversation_history=conversation_history,
                        fallback_reply="Giỏ hàng đang trống.",
                    )
                    send_bot_message(db, chat_id, telegram_user_id, reply_text)
                    return {"ok": True}

                remove_item = ai_result.get("remove_item") or {}
                item_index = remove_item.get("cart_item_index")
                item_name = (remove_item.get("item_name") or "").strip() or None
                size = (remove_item.get("size") or "").strip() or None
                quantity = remove_item.get("quantity")

                try:
                    removed = remove_item_from_order(
                        db,
                        order,
                        item_index=item_index,
                        item_name=item_name,
                        size=size,
                        quantity=quantity,
                    )
                except ValueError as exc:
                    reply_text = build_natural_action_reply(
                        user_message=text,
                        suggested_reply=ai_result.get("reply"),
                        system_result=f"Hệ thống không thể xóa món: {exc}",
                        menu_text=menu_text,
                        toppings_text=toppings_text,
                        cart_text=cart_text,
                        conversation_history=conversation_history,
                        fallback_reply=str(exc),
                    )
                    send_bot_message(db, chat_id, telegram_user_id, reply_text)
                    return {"ok": True}

                if not removed:
                    reply_text = build_natural_action_reply(
                        user_message=text,
                        suggested_reply=ai_result.get("reply"),
                        system_result="Hệ thống chưa xác định được món cần xóa trong giỏ.",
                        menu_text=menu_text,
                        toppings_text=toppings_text,
                        cart_text=cart_text,
                        conversation_history=conversation_history,
                        fallback_reply="Mình chưa xác định được món cần xóa trong giỏ. Bạn nói rõ tên món hoặc số thứ tự món giúp mình nhé.",
                    )
                    send_bot_message(
                        db,
                        chat_id,
                        telegram_user_id,
                        reply_text,
                    )
                    return {"ok": True}

                removed_text = (
                    f"đã xóa {removed['removed_quantity']} {removed['item_name']} size {removed['size']}"
                    if not removed["removed_entire_line"]
                    else f"đã xóa {removed['item_name']} size {removed['size']} khỏi giỏ"
                )
                updated_cart_text = render_cart(order)
                send_bot_message(
                    db,
                    chat_id,
                    telegram_user_id,
                    compose_reply(
                        build_natural_action_reply(
                            user_message=text,
                            suggested_reply=ai_result.get("reply"),
                            system_result=f"Hệ thống {removed_text}.",
                            menu_text=menu_text,
                            toppings_text=toppings_text,
                            cart_text=updated_cart_text,
                            conversation_history=conversation_history,
                            fallback_reply=f"Mình {removed_text}.",
                        ),
                        f"Mình {removed_text}.",
                        updated_cart_text,
                    ),
                )

            elif intent == "customer_info":
                customer_info = ai_result.get("customer_info") or {}
                sync_customer_info(
                    db,
                    telegram_user_id,
                    order,
                    name=customer_info.get("name"),
                    phone=customer_info.get("phone"),
                    address=customer_info.get("address"),
                    note=customer_info.get("note"),
                )
                follow_up_text = maybe_advance_order_after_customer_info(
                    db,
                    order,
                    user_message=text,
                    conversation_history=conversation_history,
                )
                reply_text = ai_result.get("reply") or "Mình đã lưu thông tin của bạn."
                reply_text = build_natural_action_reply(
                    user_message=text,
                    suggested_reply=reply_text,
                    system_result="Hệ thống đã lưu và đồng bộ thông tin khách hàng vào đơn hiện tại và hồ sơ của khách.",
                    menu_text=menu_text,
                    toppings_text=toppings_text,
                    cart_text=render_cart(order),
                    conversation_history=conversation_history,
                    fallback_reply="Mình đã lưu thông tin của bạn.",
                )
                if follow_up_text:
                    reply_text += "\n\n" + follow_up_text
                elif order.order_status == "awaiting_payment_confirmation":
                    reply_text += "\n\n" + render_payment_confirmation(order)
                send_bot_message(db, chat_id, telegram_user_id, reply_text)

            elif intent == "show_menu":
                send_bot_message(
                    db,
                    chat_id,
                    telegram_user_id,
                    compose_reply(
                        ai_result.get("reply"),
                        "Đây là menu hiện tại của quán.",
                        f"Menu hiện tại:\n{menu_text}",
                        toppings_text,
                    ),
                )

            elif intent == "show_toppings":
                send_bot_message(
                    db,
                    chat_id,
                    telegram_user_id,
                    compose_reply(
                        ai_result.get("reply"),
                        "Đây là danh sách topping hiện có.",
                        toppings_text,
                    ),
                )

            elif intent == "show_cart":
                latest_cart_text = render_cart(order)
                send_bot_message(
                    db,
                    chat_id,
                    telegram_user_id,
                    compose_reply(
                        build_natural_action_reply(
                            user_message=text,
                            suggested_reply=ai_result.get("reply"),
                            system_result="Hệ thống đã lấy giỏ hàng hiện tại của khách.",
                            menu_text=menu_text,
                            toppings_text=toppings_text,
                            cart_text=latest_cart_text,
                            conversation_history=conversation_history,
                            fallback_reply="Đây là giỏ hàng hiện tại của bạn.",
                        ),
                        "Đây là giỏ hàng hiện tại của bạn.",
                        latest_cart_text,
                    ),
                )

            elif intent == "checkout":
                if not order.items:
                    reply_text = build_natural_action_reply(
                        user_message=text,
                        suggested_reply=ai_result.get("reply"),
                        system_result="Hệ thống không thể chốt đơn vì giỏ hàng đang trống.",
                        menu_text=menu_text,
                        toppings_text=toppings_text,
                        cart_text=cart_text,
                        conversation_history=conversation_history,
                        fallback_reply="Giỏ hàng đang trống.",
                    )
                    send_bot_message(db, chat_id, telegram_user_id, reply_text)
                    return {"ok": True}

                missing = []
                if not order.customer_name:
                    missing.append("tên khách")
                if not order.phone:
                    missing.append("số điện thoại")
                if not order.address:
                    missing.append("địa chỉ")

                if missing:
                    reply_text = build_natural_action_reply(
                        user_message=text,
                        suggested_reply=ai_result.get("reply"),
                        system_result=f"Hệ thống chưa thể chốt đơn vì thiếu thông tin giao hàng: {', '.join(missing)}.",
                        menu_text=menu_text,
                        toppings_text=toppings_text,
                        cart_text=cart_text,
                        conversation_history=conversation_history,
                        fallback_reply="Mình chưa đủ thông tin giao hàng để chốt đơn.",
                    )
                    send_bot_message(db, chat_id, telegram_user_id, reply_text)
                    return {"ok": True}

                order.order_status = "confirmed"
                db.commit()
                db.refresh(order)
                summary_text = render_order_summary(order)
                send_bot_message(
                    db,
                    chat_id,
                    telegram_user_id,
                    compose_reply(
                        build_natural_action_reply(
                            user_message=text,
                            suggested_reply=ai_result.get("reply"),
                            system_result="Hệ thống đã chốt đơn tạm thời thành công.",
                            menu_text=menu_text,
                            toppings_text=toppings_text,
                            cart_text=summary_text,
                            conversation_history=conversation_history,
                            fallback_reply="Mình đã chốt đơn tạm thời cho bạn.",
                        ),
                        "Mình đã chốt đơn tạm thời cho bạn.",
                        summary_text,
                        "Dùng /pay để xem lại thông tin trong hệ thống trước khi xác nhận thanh toán.",
                    ),
                )

            elif intent == "pay":
                if not order.items:
                    reply_text = build_natural_action_reply(
                        user_message=text,
                        suggested_reply=ai_result.get("reply"),
                        system_result="Hệ thống không thể chuyển sang bước thanh toán vì giỏ hàng đang trống.",
                        menu_text=menu_text,
                        toppings_text=toppings_text,
                        cart_text=cart_text,
                        conversation_history=conversation_history,
                        fallback_reply="Giỏ hàng đang trống.",
                    )
                    send_bot_message(db, chat_id, telegram_user_id, reply_text)
                    return {"ok": True}

                if order.order_status == "draft" and has_complete_customer_info(order):
                    order.order_status = "confirmed"
                    db.commit()
                    db.refresh(order)

                if order.order_status != "confirmed":
                    reply_text = build_natural_action_reply(
                        user_message=text,
                        suggested_reply=ai_result.get("reply"),
                        system_result="Hệ thống chưa thể thanh toán vì đơn chưa được checkout/chốt đơn.",
                        menu_text=menu_text,
                        toppings_text=toppings_text,
                        cart_text=cart_text,
                        conversation_history=conversation_history,
                        fallback_reply="Bạn cần chốt đơn trước khi thanh toán.",
                    )
                    send_bot_message(db, chat_id, telegram_user_id, reply_text)
                    return {"ok": True}

                if not has_complete_customer_info(order):
                    reply_text = build_natural_action_reply(
                        user_message=text,
                        suggested_reply=ai_result.get("reply"),
                        system_result="Hệ thống chưa thể xác nhận thanh toán vì thiếu tên, số điện thoại hoặc địa chỉ.",
                        menu_text=menu_text,
                        toppings_text=toppings_text,
                        cart_text=cart_text,
                        conversation_history=conversation_history,
                        fallback_reply="Mình chưa đủ thông tin để xác nhận thanh toán. Bạn vui lòng cập nhật tên, số điện thoại của bạn và địa chỉ giúp mình.",
                    )
                    send_bot_message(db, chat_id, telegram_user_id, reply_text)
                    return {"ok": True}

                order.order_status = "awaiting_payment_confirmation"
                db.commit()
                db.refresh(order)
                confirmation_text = render_payment_confirmation(order)
                send_bot_message(
                    db,
                    chat_id,
                    telegram_user_id,
                    compose_reply(
                        build_natural_action_reply(
                            user_message=text,
                            suggested_reply=ai_result.get("reply"),
                            system_result="Hệ thống đã chuyển đơn sang bước chờ khách xác nhận thanh toán.",
                            menu_text=menu_text,
                            toppings_text=toppings_text,
                            cart_text=confirmation_text,
                            conversation_history=conversation_history,
                            fallback_reply="Mình đã lấy thông tin hiện có từ hệ thống để bạn xác nhận lại trước khi thanh toán.",
                        ),
                        "Mình đã lấy thông tin hiện có từ hệ thống để bạn xác nhận lại trước khi thanh toán.",
                        confirmation_text,
                    ),
                )

            elif intent == "summary":
                summary_text = render_order_summary(order)
                send_bot_message(
                    db,
                    chat_id,
                    telegram_user_id,
                    compose_reply(
                        build_natural_action_reply(
                            user_message=text,
                            suggested_reply=ai_result.get("reply"),
                            system_result="Hệ thống đã lấy tóm tắt đơn hiện tại của khách.",
                            menu_text=menu_text,
                            toppings_text=toppings_text,
                            cart_text=summary_text,
                            conversation_history=conversation_history,
                            fallback_reply="Đây là tóm tắt đơn hiện tại của bạn.",
                        ),
                        "Đây là tóm tắt đơn hiện tại của bạn.",
                        summary_text,
                    ),
                )

            else:
                if looks_like_payment_request(text) and order.items and order.payment_status != "paid":
                    if order.order_status == "draft" and has_complete_customer_info(order):
                        order.order_status = "confirmed"
                        db.commit()
                        db.refresh(order)
                    if order.order_status == "confirmed":
                        order.order_status = "awaiting_payment_confirmation"
                        db.commit()
                        db.refresh(order)
                        confirmation_text = render_payment_confirmation(order)
                        send_bot_message(
                            db,
                            chat_id,
                            telegram_user_id,
                            compose_reply(
                                build_natural_action_reply(
                                    user_message=text,
                                    suggested_reply=ai_result.get("reply"),
                                    system_result="Hệ thống đã chuyển đơn sang bước chờ khách xác nhận thanh toán.",
                                    menu_text=menu_text,
                                    toppings_text=toppings_text,
                                    cart_text=confirmation_text,
                                    conversation_history=conversation_history,
                                    fallback_reply="Mình đã lấy thông tin hiện có từ hệ thống để bạn xác nhận lại trước khi thanh toán.",
                                ),
                                "Mình đã lấy thông tin hiện có từ hệ thống để bạn xác nhận lại trước khi thanh toán.",
                                confirmation_text,
                            ),
                        )
                        return {"ok": True}

                reply = ai_result.get("reply") or llm_service.generate_customer_reply(
                    user_message=text,
                    menu_text=menu_service.get_menu_text(),
                    toppings_text=menu_service.get_toppings_text(),
                    cart_text=cart_text,
                    conversation_history=conversation_history,
                )
                send_bot_message(db, chat_id, telegram_user_id, reply)
    finally:
        db.close()

    return {"ok": True}
