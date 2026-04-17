from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.customer_service import apply_profile_to_order, get_or_create_customer_profile
from app.menu_service import normalize_text
from app.models import Order, OrderItem


def _sorted_order_items(order: Order) -> list[OrderItem]:
    return sorted(order.items, key=lambda item: (item.id or 0, item.item_name))


def _item_name_matches(order_item_name: str, target_item_name: str) -> bool:
    normalized_order_name = normalize_text(order_item_name)
    normalized_target_name = normalize_text(target_item_name)
    return (
        normalized_order_name == normalized_target_name
        or normalized_target_name in normalized_order_name
        or normalized_order_name in normalized_target_name
    )


def _item_matches(order_item: OrderItem, *, item_name: str | None = None, size: str | None = None) -> bool:
    if item_name and not _item_name_matches(order_item.item_name, item_name):
        return False

    if size and normalize_text(order_item.size) != normalize_text(size):
        return False

    return True


def get_or_create_active_order(db: Session, telegram_user_id: str) -> Order:
    order = (
        db.query(Order)
        .filter(
            Order.telegram_user_id == telegram_user_id,
            Order.payment_status != "paid",
            Order.order_status.in_(("draft", "confirmed", "awaiting_payment_confirmation")),
        )
        .order_by(Order.id.desc())
        .first()
    )
    if order:
        profile = get_or_create_customer_profile(db, telegram_user_id)
        if apply_profile_to_order(order, profile):
            db.commit()
            db.refresh(order)
        return order

    return get_or_create_draft_order(db, telegram_user_id)


def _find_target_item(
    order: Order,
    *,
    item_index: int | None = None,
    item_name: str | None = None,
    size: str | None = None,
) -> OrderItem | None:
    sorted_items = _sorted_order_items(order)
    indexed_candidate: OrderItem | None = None

    if item_index is not None:
        if 0 < item_index <= len(sorted_items):
            indexed_candidate = sorted_items[item_index - 1]
        elif not item_name:
            return None

    if item_name:
        if indexed_candidate and _item_matches(indexed_candidate, item_name=item_name, size=size):
            return indexed_candidate

        matched_items = [item for item in sorted_items if _item_matches(item, item_name=item_name, size=size)]
        if len(matched_items) == 1:
            return matched_items[0]
        if indexed_candidate:
            return indexed_candidate

    if indexed_candidate:
        return indexed_candidate

    return None


def get_or_create_draft_order(db: Session, telegram_user_id: str) -> Order:
    order = (
        db.query(Order)
        .filter(Order.telegram_user_id == telegram_user_id, Order.order_status == "draft")
        .order_by(Order.id.desc())
        .first()
    )
    if order:
        profile = get_or_create_customer_profile(db, telegram_user_id)
        if apply_profile_to_order(order, profile):
            db.commit()
            db.refresh(order)
        return order

    profile = get_or_create_customer_profile(db, telegram_user_id)
    order = Order(telegram_user_id=telegram_user_id, order_status="draft", payment_status="pending")
    apply_profile_to_order(order, profile)
    db.add(order)
    db.commit()
    db.refresh(order)
    return order


def recalculate_order(db: Session, order: Order) -> None:
    subtotal = (
        db.query(func.coalesce(func.sum(OrderItem.line_total), 0))
        .filter(OrderItem.order_id == order.id)
        .scalar()
    )
    order.subtotal = subtotal
    order.total = subtotal + (order.shipping_fee or 0)


def add_item_to_order(
    db: Session,
    order: Order,
    item_name: str,
    size: str,
    quantity: int,
    toppings: list[str],
    unit_price: int,
    topping_price_map: dict[str, int] | None = None,
    sugar_level: str = "",
    ice_level: str = "",
) -> OrderItem:
    topping_price_map = topping_price_map or {}
    topping_total = sum(int(topping_price_map.get(t, 0)) for t in toppings)
    final_unit_price = unit_price + topping_total
    line_total = final_unit_price * quantity

    item = OrderItem(
        order=order,
        item_name=item_name,
        size=size,
        quantity=quantity,
        toppings="|".join(toppings),
        sugar_level=sugar_level,
        ice_level=ice_level,
        unit_price=final_unit_price,
        line_total=line_total,
    )
    db.add(item)
    db.flush()

    recalculate_order(db, order)
    db.commit()
    db.refresh(order)
    return item


def remove_item_from_order(
    db: Session,
    order: Order,
    *,
    item_index: int | None = None,
    item_name: str | None = None,
    size: str | None = None,
    quantity: int | None = None,
) -> dict | None:
    target_item = _find_target_item(order, item_index=item_index, item_name=item_name, size=size)
    if target_item is None:
        return None

    if quantity is not None and quantity <= 0:
        raise ValueError("Số lượng cần xóa phải là số nguyên dương.")

    current_quantity = int(target_item.quantity)
    removed_quantity = current_quantity
    removed_entire_line = True

    if quantity is not None and quantity < current_quantity:
        target_item.quantity = current_quantity - quantity
        target_item.line_total = int(target_item.unit_price) * int(target_item.quantity)
        removed_quantity = quantity
        removed_entire_line = False
    elif quantity is not None and quantity > current_quantity:
        raise ValueError("Số lượng muốn xóa lớn hơn số lượng hiện có trong giỏ.")
    else:
        db.delete(target_item)

    db.flush()
    recalculate_order(db, order)
    db.commit()
    db.refresh(order)

    return {
        "item_name": target_item.item_name,
        "size": target_item.size,
        "removed_quantity": removed_quantity,
        "removed_entire_line": removed_entire_line,
    }


def update_toppings_for_order_item(
    db: Session,
    order: Order,
    *,
    item_index: int | None = None,
    item_name: str | None = None,
    size: str | None = None,
    toppings_to_add: list[str],
    toppings_to_remove: list[str],
    topping_price_map: dict[str, int],
) -> dict | None:
    return update_order_item(
        db,
        order,
        item_index=item_index,
        item_name=item_name,
        size=size,
        toppings_to_add=toppings_to_add,
        toppings_to_remove=toppings_to_remove,
        topping_price_map=topping_price_map,
    )


def update_order_item(
    db: Session,
    order: Order,
    *,
    item_index: int | None = None,
    item_name: str | None = None,
    size: str | None = None,
    quantity: int | None = None,
    sugar_level: str | None = None,
    ice_level: str | None = None,
    toppings_to_add: list[str] | None = None,
    toppings_to_remove: list[str] | None = None,
    topping_price_map: dict[str, int] | None = None,
    updated_item_size: str | None = None,
    updated_base_unit_price: int | None = None,
) -> dict | None:
    toppings_to_add = toppings_to_add or []
    toppings_to_remove = toppings_to_remove or []
    topping_price_map = topping_price_map or {}

    if (
        quantity is None
        and sugar_level is None
        and ice_level is None
        and updated_item_size is None
        and not toppings_to_add
        and not toppings_to_remove
    ):
        raise ValueError("Cần ít nhất một thay đổi để cập nhật món.")

    if not toppings_to_add and not toppings_to_remove:
        pass

    target_item = _find_target_item(order, item_index=item_index, item_name=item_name, size=size)
    if target_item is None:
        return None

    if quantity is not None and quantity <= 0:
        raise ValueError("Số lượng mới phải là số nguyên dương.")

    existing_toppings = [x.strip() for x in (target_item.toppings or "").split("|") if x.strip()]
    existing_keys = {normalize_text(x) for x in existing_toppings}
    added_toppings: list[str] = []
    removed_toppings: list[str] = []

    for topping in toppings_to_add:
        if normalize_text(topping) in existing_keys:
            continue
        existing_toppings.append(topping)
        existing_keys.add(normalize_text(topping))
        added_toppings.append(topping)

    normalized_remove_keys = {normalize_text(t) for t in toppings_to_remove}
    if normalized_remove_keys:
        kept_toppings: list[str] = []
        for topping in existing_toppings:
            normalized_topping = normalize_text(topping)
            if normalized_topping in normalized_remove_keys:
                removed_toppings.append(topping)
                normalized_remove_keys.remove(normalized_topping)
                continue
            kept_toppings.append(topping)
        existing_toppings = kept_toppings

    if quantity is not None:
        target_item.quantity = quantity
    if updated_item_size is not None:
        target_item.size = updated_item_size
    if sugar_level is not None:
        target_item.sugar_level = sugar_level
    if ice_level is not None:
        target_item.ice_level = ice_level

    current_base_unit_price = int(target_item.unit_price) - sum(int(topping_price_map.get(t, 0)) for t in [x.strip() for x in (target_item.toppings or "").split("|") if x.strip()])
    base_unit_price = updated_base_unit_price if updated_base_unit_price is not None else current_base_unit_price
    target_item.toppings = "|".join(existing_toppings)
    topping_total = sum(int(topping_price_map.get(t, 0)) for t in existing_toppings)
    target_item.unit_price = int(base_unit_price) + topping_total
    target_item.line_total = int(target_item.unit_price) * int(target_item.quantity)

    db.flush()
    recalculate_order(db, order)
    db.commit()
    db.refresh(order)

    return {
        "item_name": target_item.item_name,
        "size": target_item.size,
        "quantity": target_item.quantity,
        "sugar_level": target_item.sugar_level,
        "ice_level": target_item.ice_level,
        "added_toppings": added_toppings,
        "removed_toppings": removed_toppings,
    }


def update_customer_info(
    order: Order,
    name: str | None = None,
    phone: str | None = None,
    address: str | None = None,
    note: str | None = None,
) -> None:
    if name is not None:
        order.customer_name = name
    if phone is not None:
        order.phone = phone
    if address is not None:
        order.address = address
    if note is not None:
        order.note = note


def render_cart(order: Order) -> str:
    if not order.id:
        return "Giỏ hàng đang trống."

    lines = [f"Đơn #{order.id}"]
    for idx, item in enumerate(_sorted_order_items(order), start=1):
        details = []
        if item.toppings:
            details.append(f"topping: {item.toppings.replace('|', ', ')}")
        if item.sugar_level:
            details.append(f"đường: {item.sugar_level}")
        if item.ice_level:
            details.append(f"đá: {item.ice_level}")
        suffix = f" | {' | '.join(details)}" if details else ""
        lines.append(
            f"{idx}. {item.item_name} - size {item.size} - SL {item.quantity} - {item.line_total:,}đ{suffix}"
        )

    lines.append(f"Tạm tính: {order.subtotal:,}đ")
    lines.append(f"Ship: {order.shipping_fee:,}đ")
    lines.append(f"Tổng: {order.total:,}đ")
    return "\n".join(lines)


def render_order_summary(order: Order) -> str:
    customer_name = order.customer_name or "Chưa có"
    phone = order.phone or "Chưa có"
    address = order.address or "Chưa có"
    note = order.note or "Không có"

    lines = [
        f"ĐƠN #{order.id}",
        f"Khách: {customer_name}",
        f"SĐT: {phone}",
        f"Địa chỉ: {address}",
        "Món:",
    ]
    for idx, item in enumerate(_sorted_order_items(order), start=1):
        topping_text = f" | topping: {item.toppings.replace('|', ', ')}" if item.toppings else ""
        lines.append(
            f"{idx}. {item.item_name} - size {item.size} - SL {item.quantity} - {item.line_total:,}đ{topping_text}"
        )
    lines.extend(
        [
            f"Ghi chú: {note}",
            f"Tổng: {order.total:,}đ",
            f"Thanh toán: {order.payment_status}",
            f"Trạng thái đơn: {order.order_status}",
        ]
    )
    return "\n".join(lines)
