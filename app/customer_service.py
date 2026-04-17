from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import CustomerProfile, Order


def get_or_create_customer_profile(db: Session, telegram_user_id: str) -> CustomerProfile:
    profile = (
        db.query(CustomerProfile)
        .filter(CustomerProfile.telegram_user_id == telegram_user_id)
        .first()
    )
    if profile:
        return profile

    profile = CustomerProfile(telegram_user_id=telegram_user_id)
    db.add(profile)
    db.commit()
    db.refresh(profile)
    return profile


def update_customer_profile(
    profile: CustomerProfile,
    *,
    name: str | None = None,
    phone: str | None = None,
    address: str | None = None,
) -> None:
    if name is not None:
        profile.customer_name = name
    if phone is not None:
        profile.phone = phone
    if address is not None:
        profile.address = address


def apply_profile_to_order(order: Order, profile: CustomerProfile) -> bool:
    changed = False
    if not order.customer_name and profile.customer_name:
        order.customer_name = profile.customer_name
        changed = True
    if not order.phone and profile.phone:
        order.phone = profile.phone
        changed = True
    if not order.address and profile.address:
        order.address = profile.address
        changed = True
    return changed
