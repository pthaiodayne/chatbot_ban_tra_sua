from __future__ import annotations

from pathlib import Path
import unittest

from app.main import infer_add_items_from_text
from app.menu_service import MenuService

BASE_DIR = Path(__file__).resolve().parents[3]


class MenuServiceTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.menu_service = MenuService(str(BASE_DIR / "data" / "Menu.csv"))

    def test_find_item_supports_common_alias_cf(self) -> None:
        item = self.menu_service.find_item("cf đen", "M")

        self.assertIsNotNone(item)
        self.assertEqual(item["name"], "Cà Phê Đen")
        self.assertEqual(item["size"], "M")

    def test_infer_add_items_for_same_drink_split_by_size(self) -> None:
        parsed_items = infer_add_items_from_text("2 macchiato,1 ly size M, 1 ly size L")

        self.assertEqual(
            parsed_items,
            [
                {
                    "item_name": "Cà Phê Macchiato",
                    "size": "M",
                    "quantity": 1,
                    "toppings": [],
                },
                {
                    "item_name": "Cà Phê Macchiato",
                    "size": "L",
                    "quantity": 1,
                    "toppings": [],
                },
            ],
        )

    def test_infer_add_items_for_multiple_drinks(self) -> None:
        parsed_items = infer_add_items_from_text("1 cf đen, 2 macchiato")

        self.assertEqual(
            parsed_items,
            [
                {
                    "item_name": "Cà Phê Đen",
                    "size": "M",
                    "quantity": 1,
                    "toppings": [],
                },
                {
                    "item_name": "Cà Phê Macchiato",
                    "size": "M",
                    "quantity": 2,
                    "toppings": [],
                },
            ],
        )

    def test_infer_add_items_supports_plus_separator(self) -> None:
        parsed_items = infer_add_items_from_text("1 cf đen + 2 macchiato size l")

        self.assertEqual(
            parsed_items,
            [
                {
                    "item_name": "Cà Phê Đen",
                    "size": "M",
                    "quantity": 1,
                    "toppings": [],
                },
                {
                    "item_name": "Cà Phê Macchiato",
                    "size": "L",
                    "quantity": 2,
                    "toppings": [],
                },
            ],
        )


if __name__ == "__main__":
    unittest.main()
