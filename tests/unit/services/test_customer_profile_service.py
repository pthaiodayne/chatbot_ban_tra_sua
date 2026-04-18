from __future__ import annotations

from app.customer_service import get_or_create_customer_profile, update_customer_profile
from app.order_service import get_or_create_active_order
from tests.unit.base import DatabaseTestCase


class CustomerProfileTestCase(DatabaseTestCase):
    def test_new_order_prefills_customer_info_from_profile(self) -> None:
        profile = get_or_create_customer_profile(self.db, "tele-1")
        update_customer_profile(
            profile,
            name="Nguyen Van A",
            phone="0909000000",
            address="123 Nguyen Hue",
        )
        self.db.commit()

        order = get_or_create_active_order(self.db, "tele-1")

        self.assertEqual(order.customer_name, "Nguyen Van A")
        self.assertEqual(order.phone, "0909000000")
        self.assertEqual(order.address, "123 Nguyen Hue")

    def test_existing_active_order_backfills_missing_fields_from_profile(self) -> None:
        order = get_or_create_active_order(self.db, "tele-2")
        self.assertIsNone(order.customer_name)

        profile = get_or_create_customer_profile(self.db, "tele-2")
        update_customer_profile(
            profile,
            name="Tran Thi B",
            phone="0911222333",
            address="45 Le Loi",
        )
        self.db.commit()

        order = get_or_create_active_order(self.db, "tele-2")

        self.assertEqual(order.customer_name, "Tran Thi B")
        self.assertEqual(order.phone, "0911222333")
        self.assertEqual(order.address, "45 Le Loi")


if __name__ == "__main__":
    import unittest

    unittest.main()
