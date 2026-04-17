from __future__ import annotations

import unittest

from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker

from app.models import Base
from app.order_service import (
    add_item_to_order,
    get_or_create_draft_order,
    remove_item_from_order,
    update_order_item,
    update_toppings_for_order_item,
)


class OrderPricingTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)
        Base.metadata.create_all(bind=self.engine)
        self.db = self.SessionLocal()

    def tearDown(self) -> None:
        self.db.close()
        Base.metadata.drop_all(bind=self.engine)
        self.engine.dispose()

    def test_first_item_updates_subtotal_and_total(self) -> None:
        order = get_or_create_draft_order(self.db, "test-user-1")

        add_item_to_order(
            db=self.db,
            order=order,
            item_name="Tra Xoai",
            size="M",
            quantity=1,
            toppings=[],
            unit_price=32000,
            topping_price_map={},
        )

        self.db.refresh(order)
        self.assertEqual(order.subtotal, 32000)
        self.assertEqual(order.total, 32000)
        self.assertEqual(len(order.items), 1)
        self.assertEqual(order.items[0].line_total, 32000)

    def test_multiple_items_and_toppings_are_accumulated_correctly(self) -> None:
        order = get_or_create_draft_order(self.db, "test-user-2")

        add_item_to_order(
            db=self.db,
            order=order,
            item_name="Tra Sua Truyen Thong",
            size="L",
            quantity=2,
            toppings=["Kem Tuoi"],
            unit_price=40000,
            topping_price_map={"Kem Tuoi": 8000},
        )
        add_item_to_order(
            db=self.db,
            order=order,
            item_name="Tra Xoai",
            size="M",
            quantity=1,
            toppings=["Tran Chau Den"],
            unit_price=32000,
            topping_price_map={"Tran Chau Den": 5000},
        )

        self.db.refresh(order)
        self.assertEqual(len(order.items), 2)
        self.assertEqual(order.items[0].unit_price, 48000)
        self.assertEqual(order.items[0].line_total, 96000)
        self.assertEqual(order.items[1].unit_price, 37000)
        self.assertEqual(order.items[1].line_total, 37000)
        self.assertEqual(order.subtotal, 133000)
        self.assertEqual(order.total, 133000)

    def test_remove_item_by_index_updates_total(self) -> None:
        order = get_or_create_draft_order(self.db, "test-user-3")
        add_item_to_order(
            db=self.db,
            order=order,
            item_name="Tra Sua Truyen Thong",
            size="L",
            quantity=2,
            toppings=[],
            unit_price=40000,
            topping_price_map={},
        )
        add_item_to_order(
            db=self.db,
            order=order,
            item_name="Tra Xoai",
            size="M",
            quantity=1,
            toppings=[],
            unit_price=32000,
            topping_price_map={},
        )

        removed = remove_item_from_order(self.db, order, item_index=1)

        self.db.refresh(order)
        self.assertIsNotNone(removed)
        self.assertEqual(removed["item_name"], "Tra Sua Truyen Thong")
        self.assertEqual(len(order.items), 1)
        self.assertEqual(order.subtotal, 32000)
        self.assertEqual(order.total, 32000)

    def test_remove_partial_quantity_updates_line_total(self) -> None:
        order = get_or_create_draft_order(self.db, "test-user-4")
        add_item_to_order(
            db=self.db,
            order=order,
            item_name="Tra Sua Truyen Thong",
            size="L",
            quantity=3,
            toppings=["Kem Tuoi"],
            unit_price=40000,
            topping_price_map={"Kem Tuoi": 8000},
        )

        removed = remove_item_from_order(
            self.db,
            order,
            item_name="Tra Sua Truyen Thong",
            size="L",
            quantity=1,
        )

        self.db.refresh(order)
        self.assertIsNotNone(removed)
        self.assertFalse(removed["removed_entire_line"])
        self.assertEqual(removed["removed_quantity"], 1)
        self.assertEqual(len(order.items), 1)
        self.assertEqual(order.items[0].quantity, 2)
        self.assertEqual(order.items[0].line_total, 96000)
        self.assertEqual(order.subtotal, 96000)

    def test_add_topping_to_existing_item_by_index_updates_same_line(self) -> None:
        order = get_or_create_draft_order(self.db, "test-user-5")
        add_item_to_order(
            db=self.db,
            order=order,
            item_name="Tra Sua Truyen Thong",
            size="L",
            quantity=1,
            toppings=[],
            unit_price=40000,
            topping_price_map={},
        )
        add_item_to_order(
            db=self.db,
            order=order,
            item_name="Tra Xoai",
            size="M",
            quantity=1,
            toppings=[],
            unit_price=32000,
            topping_price_map={},
        )

        updated = update_toppings_for_order_item(
            self.db,
            order,
            item_index=2,
            toppings_to_add=["Tran Chau Den"],
            toppings_to_remove=[],
            topping_price_map={"Tran Chau Den": 5000},
        )

        self.db.refresh(order)
        self.assertIsNotNone(updated)
        self.assertEqual(updated["item_name"], "Tra Xoai")
        self.assertEqual(len(order.items), 2)
        self.assertEqual(order.items[1].toppings, "Tran Chau Den")
        self.assertEqual(order.items[1].unit_price, 37000)
        self.assertEqual(order.items[1].line_total, 37000)
        self.assertEqual(order.subtotal, 77000)

    def test_add_existing_topping_does_not_duplicate_line(self) -> None:
        order = get_or_create_draft_order(self.db, "test-user-6")
        add_item_to_order(
            db=self.db,
            order=order,
            item_name="Tra Xoai",
            size="M",
            quantity=1,
            toppings=["Tran Chau Den"],
            unit_price=32000,
            topping_price_map={"Tran Chau Den": 5000},
        )

        updated = update_toppings_for_order_item(
            self.db,
            order,
            item_index=1,
            toppings_to_add=["Tran Chau Den"],
            toppings_to_remove=[],
            topping_price_map={"Tran Chau Den": 5000},
        )

        self.db.refresh(order)
        self.assertIsNotNone(updated)
        self.assertEqual(updated["added_toppings"], [])
        self.assertEqual(len(order.items), 1)
        self.assertEqual(order.items[0].toppings, "Tran Chau Den")
        self.assertEqual(order.subtotal, 37000)

    def test_remove_topping_from_existing_item_updates_same_line(self) -> None:
        order = get_or_create_draft_order(self.db, "test-user-7")
        add_item_to_order(
            db=self.db,
            order=order,
            item_name="Tra Xoai",
            size="M",
            quantity=1,
            toppings=["Tran Chau Den", "Kem Tuoi"],
            unit_price=32000,
            topping_price_map={"Tran Chau Den": 5000, "Kem Tuoi": 8000},
        )

        updated = update_toppings_for_order_item(
            self.db,
            order,
            item_index=1,
            toppings_to_add=[],
            toppings_to_remove=["Tran Chau Den"],
            topping_price_map={"Tran Chau Den": 5000, "Kem Tuoi": 8000},
        )

        self.db.refresh(order)
        self.assertIsNotNone(updated)
        self.assertEqual(updated["added_toppings"], [])
        self.assertEqual(updated["removed_toppings"], ["Tran Chau Den"])
        self.assertEqual(order.items[0].toppings, "Kem Tuoi")
        self.assertEqual(order.items[0].unit_price, 40000)
        self.assertEqual(order.items[0].line_total, 40000)
        self.assertEqual(order.subtotal, 40000)

    def test_update_item_quantity_updates_total(self) -> None:
        order = get_or_create_draft_order(self.db, "test-user-8")
        add_item_to_order(
            db=self.db,
            order=order,
            item_name="Tra Xoai",
            size="M",
            quantity=1,
            toppings=["Tran Chau Den"],
            unit_price=32000,
            topping_price_map={"Tran Chau Den": 5000},
        )

        updated = update_order_item(
            self.db,
            order,
            item_index=1,
            quantity=3,
            topping_price_map={"Tran Chau Den": 5000},
        )

        self.db.refresh(order)
        self.assertIsNotNone(updated)
        self.assertEqual(order.items[0].quantity, 3)
        self.assertEqual(order.items[0].line_total, 111000)
        self.assertEqual(order.subtotal, 111000)

    def test_update_item_size_recalculates_base_price(self) -> None:
        order = get_or_create_draft_order(self.db, "test-user-9")
        add_item_to_order(
            db=self.db,
            order=order,
            item_name="Tra Xoai",
            size="M",
            quantity=1,
            toppings=["Tran Chau Den"],
            unit_price=32000,
            topping_price_map={"Tran Chau Den": 5000},
        )

        updated = update_order_item(
            self.db,
            order,
            item_index=1,
            topping_price_map={"Tran Chau Den": 5000},
            updated_item_size="L",
            updated_base_unit_price=42000,
        )

        self.db.refresh(order)
        self.assertIsNotNone(updated)
        self.assertEqual(order.items[0].size, "L")
        self.assertEqual(order.items[0].unit_price, 47000)
        self.assertEqual(order.items[0].line_total, 47000)
        self.assertEqual(order.subtotal, 47000)

    def test_update_item_prefers_unique_name_match_when_ai_index_is_wrong(self) -> None:
        order = get_or_create_draft_order(self.db, "test-user-11")
        add_item_to_order(
            db=self.db,
            order=order,
            item_name="Tra Sua Tran Chau Den",
            size="M",
            quantity=1,
            toppings=[],
            unit_price=35000,
            topping_price_map={},
        )
        add_item_to_order(
            db=self.db,
            order=order,
            item_name="Tra Vai Thieu",
            size="L",
            quantity=1,
            toppings=[],
            unit_price=43000,
            topping_price_map={},
        )
        add_item_to_order(
            db=self.db,
            order=order,
            item_name="Ca Phe Den",
            size="M",
            quantity=1,
            toppings=[],
            unit_price=25000,
            topping_price_map={},
        )

        updated = update_order_item(
            self.db,
            order,
            item_index=2,
            item_name="Ca Phe Den",
            size="M",
            updated_item_size="L",
            updated_base_unit_price=30000,
            topping_price_map={},
        )

        self.db.refresh(order)
        self.assertIsNotNone(updated)
        self.assertEqual(updated["item_name"], "Ca Phe Den")
        self.assertEqual(updated["size"], "L")
        self.assertEqual(order.items[1].size, "L")
        self.assertEqual(order.items[2].size, "L")
        self.assertEqual(order.items[2].unit_price, 30000)

    def test_update_item_sugar_and_ice(self) -> None:
        order = get_or_create_draft_order(self.db, "test-user-10")
        add_item_to_order(
            db=self.db,
            order=order,
            item_name="Tra Xoai",
            size="M",
            quantity=1,
            toppings=[],
            unit_price=32000,
            topping_price_map={},
        )

        updated = update_order_item(
            self.db,
            order,
            item_index=1,
            sugar_level="50%",
            ice_level="Ít đá",
            topping_price_map={},
        )

        self.db.refresh(order)
        self.assertIsNotNone(updated)
        self.assertEqual(order.items[0].sugar_level, "50%")
        self.assertEqual(order.items[0].ice_level, "Ít đá")
        self.assertEqual(order.subtotal, 32000)


if __name__ == "__main__":
    unittest.main()
