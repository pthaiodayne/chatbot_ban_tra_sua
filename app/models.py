from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from app.database import Base


class Order(Base):
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, index=True)
    telegram_user_id = Column(String, index=True, nullable=False)
    customer_name = Column(String, nullable=True)
    phone = Column(String, nullable=True)
    address = Column(String, nullable=True)
    note = Column(Text, nullable=True)

    subtotal = Column(Integer, default=0)
    shipping_fee = Column(Integer, default=0)
    total = Column(Integer, default=0)

    payment_status = Column(String, default="pending")
    order_status = Column(String, default="draft")

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    items = relationship("OrderItem", back_populates="order", cascade="all, delete-orphan")


class CustomerProfile(Base):
    __tablename__ = "customer_profiles"

    id = Column(Integer, primary_key=True, index=True)
    telegram_user_id = Column(String, index=True, nullable=False, unique=True)
    customer_name = Column(String, nullable=True)
    phone = Column(String, nullable=True)
    address = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class OrderItem(Base):
    __tablename__ = "order_items"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False)

    item_name = Column(String, nullable=False)
    size = Column(String, nullable=False)
    quantity = Column(Integer, default=1)
    toppings = Column(Text, default="")
    sugar_level = Column(String, default="")
    ice_level = Column(String, default="")
    unit_price = Column(Integer, default=0)
    line_total = Column(Integer, default=0)

    order = relationship("Order", back_populates="items")


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id = Column(Integer, primary_key=True, index=True)
    telegram_user_id = Column(String, index=True, nullable=False)
    role = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
