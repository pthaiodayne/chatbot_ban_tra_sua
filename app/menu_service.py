from __future__ import annotations

import unicodedata

import pandas as pd

REQUIRED_COLUMNS = {
    "category",
    "item_id",
    "name",
    "description",
    "price_m",
    "price_l",
    "available",
}

SIZE_ALIASES = {
    "m": "M",
    "size m": "M",
    "vua": "M",
    "medium": "M",
    "l": "L",
    "size l": "L",
    "lon": "L",
    "large": "L",
}


def strip_accents(text: str) -> str:
    text = str(text).replace("đ", "d").replace("Đ", "D")
    text = unicodedata.normalize("NFD", text)
    return "".join(ch for ch in text if unicodedata.category(ch) != "Mn")


def normalize_text(text: str) -> str:
    text = strip_accents(str(text)).lower().strip()
    return " ".join(text.split())


class MenuService:
    def __init__(self, csv_path: str):
        self.csv_path = csv_path
        self.raw_df, self.drink_df, self.topping_df = self._load_csv(csv_path)

    def _load_csv(self, csv_path: str):
        df = pd.read_csv(csv_path)
        missing = REQUIRED_COLUMNS - set(df.columns)
        if missing:
            raise ValueError(f"Menu.csv thiếu cột: {', '.join(sorted(missing))}")

        df = df.copy()
        df["category"] = df["category"].astype(str).str.strip()
        df["item_id"] = df["item_id"].astype(str).str.strip()
        df["name"] = df["name"].astype(str).str.strip()
        df["description"] = df["description"].fillna("").astype(str).str.strip()
        df["available"] = df["available"].astype(str).str.strip().str.lower().isin(["true", "1", "yes"])
        df["name_norm"] = df["name"].apply(normalize_text)

        for col in ["price_m", "price_l"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        topping_df = df[(df["category"].str.lower() == "topping") & (df["available"])].copy()
        topping_df["price"] = topping_df["price_m"].fillna(0).astype(int)

        drink_rows = []
        drinks_source = df[(df["category"].str.lower() != "topping") & (df["available"])].copy()
        for _, row in drinks_source.iterrows():
            for size_key, size_label in [("price_m", "M"), ("price_l", "L")]:
                price = row[size_key]
                if pd.isna(price):
                    continue
                price = int(price)
                if price <= 0:
                    continue
                drink_rows.append(
                    {
                        "item_id": row["item_id"],
                        "category": row["category"],
                        "name": row["name"],
                        "description": row["description"],
                        "name_norm": row["name_norm"],
                        "size": size_label,
                        "size_norm": normalize_text(size_label),
                        "price": price,
                        "available": True,
                    }
                )
        drink_df = pd.DataFrame(drink_rows)
        return df, drink_df, topping_df

    def normalize_size(self, size: str) -> str | None:
        key = normalize_text(size)
        return SIZE_ALIASES.get(key)

    def get_menu_text(self) -> str:
        if self.drink_df.empty:
            return "Menu hiện chưa có món nào khả dụng."

        lines: list[str] = []
        for category, group in self.drink_df.groupby("category", sort=False):
            lines.append(f"[{category}]")
            for name, item_group in group.groupby("name", sort=False):
                prices = []
                for _, row in item_group.sort_values(by="size", key=lambda s: s.map({"M": 0, "L": 1})).iterrows():
                    prices.append(f"{row['size']}: {int(row['price']):,}đ")
                lines.append(f"- {name}: " + ", ".join(prices))
            lines.append("")
        return "\n".join(lines).strip()

    def get_toppings_text(self) -> str:
        if self.topping_df.empty:
            return "Hiện chưa có topping khả dụng."
        lines = ["[Topping]"]
        for _, row in self.topping_df.iterrows():
            lines.append(f"- {row['name']}: {int(row['price']):,}đ")
        return "\n".join(lines)

    def _find_by_exact_or_contains(self, df: pd.DataFrame, query: str) -> pd.DataFrame:
        q = normalize_text(query)
        exact = df[df["name_norm"] == q]
        if not exact.empty:
            return exact
        contains = df[df["name_norm"].str.contains(q, regex=False)]
        if len(contains) == 1:
            return contains
        reverse_contains = df[df["name_norm"].apply(lambda x: q in x or x in q)]
        if len(reverse_contains) == 1:
            return reverse_contains
        return pd.DataFrame(columns=df.columns)

    def find_item(self, item_name: str, size: str) -> dict | None:
        normalized_size = self.normalize_size(size)
        if not normalized_size:
            return None
        matched = self._find_by_exact_or_contains(self.drink_df, item_name)
        if matched.empty:
            return None
        rows = matched[matched["size"] == normalized_size]
        if rows.empty:
            return None
        return rows.iloc[0].to_dict()

    def find_topping(self, topping_name: str) -> dict | None:
        matched = self._find_by_exact_or_contains(self.topping_df, topping_name)
        if matched.empty:
            return None
        return matched.iloc[0].to_dict()

    def are_valid_toppings(self, toppings: list[str]) -> tuple[bool, list[str], list[dict]]:
        invalid = []
        matched_rows = []
        seen = set()
        for topping in toppings:
            found = self.find_topping(topping)
            if not found:
                invalid.append(topping)
                continue
            key = found["item_id"]
            if key in seen:
                continue
            seen.add(key)
            matched_rows.append(found)
        return len(invalid) == 0, invalid, matched_rows

    def get_topping_price_map(self, toppings: list[dict]) -> dict[str, int]:
        return {t["name"]: int(t["price"]) for t in toppings}

    def get_all_topping_price_map(self) -> dict[str, int]:
        if self.topping_df.empty:
            return {}
        return {str(row["name"]): int(row["price"]) for _, row in self.topping_df.iterrows()}
