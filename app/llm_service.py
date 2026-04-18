from __future__ import annotations

import json
import logging
import re

from typing import Any
from openai import OpenAI

from app.config import OPENAI_API_KEY, OPENAI_MODEL

logger = logging.getLogger("llm_service")

class OpenAIConfigError(RuntimeError):
    pass


class LLMService:
    def __init__(self, api_key: str = OPENAI_API_KEY, model: str = OPENAI_MODEL):
        self.api_key = api_key
        self.model = model
        self._client: OpenAI | None = None

    def is_enabled(self) -> bool:
        return bool(self.api_key)

    def _require_api_key(self) -> None:
        if not self.api_key:
            raise OpenAIConfigError("OPENAI_API_KEY chưa được cấu hình.")

    def _client_instance(self) -> OpenAI:
        self._require_api_key()
        if self._client is None:
            self._client = OpenAI(api_key=self.api_key)
        return self._client

    def _extract_json_text(self, text: str) -> str:
        stripped = text.strip()
        if stripped.startswith("{") and stripped.endswith("}"):
            return stripped

        fenced_match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", stripped, flags=re.DOTALL)
        if fenced_match:
            return fenced_match.group(1).strip()

        first_brace = stripped.find("{")
        last_brace = stripped.rfind("}")
        if first_brace != -1 and last_brace != -1 and first_brace < last_brace:
            return stripped[first_brace:last_brace + 1].strip()

        return stripped

    def _generate_json(self, prompt: str, schema: dict[str, Any]) -> dict[str, Any]:
        try:
            response = self._client_instance().chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "user", "content": prompt},
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "customer_message_parse",
                        "schema": schema,
                        "strict": True,
                    },
                },
            )

            text = (response.choices[0].message.content or "").strip()
            if not text:
                raise ValueError("OpenAI không trả về nội dung JSON.")

            logger.info("OpenAI response: %s", text)
            return json.loads(text)
        except Exception:
            logger.exception("Structured JSON parse failed, trying text fallback.")
            fallback_prompt = (
                prompt
                + "\n\nQuan trọng: chỉ trả về đúng một JSON object hợp lệ, không giải thích thêm, không markdown."
            )
            fallback_response = self._client_instance().chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "user", "content": fallback_prompt},
                ],
            )
            fallback_text = (fallback_response.choices[0].message.content or "").strip()
            if not fallback_text:
                raise ValueError("OpenAI không trả về nội dung JSON ở bước fallback.")

            logger.info("OpenAI fallback response: %s", fallback_text)
            return json.loads(self._extract_json_text(fallback_text))

    def _generate_text(self, prompt: str) -> str:
        response = self._client_instance().chat.completions.create(
            model=self.model,
            messages=[
                {"role": "user", "content": prompt},
            ],
        )
        text = (response.choices[0].message.content or "").strip()
        if not text:
            raise ValueError("OpenAI không trả về nội dung.")
        
        logger.info("OpenAI response: %s", text)

        return text

    def parse_customer_message(
        self,
        *,
        user_message: str,
        menu_text: str,
        toppings_text: str,
        cart_text: str,
        conversation_history: str = "",
    ) -> dict[str, Any]:
        schema = {
            "type": "object",
            "properties": {
                "intent": {
                    "type": "string",
                    "enum": [
                        "add_item",
                        "update_item",
                        "remove_item",
                        "customer_info",
                        "show_menu",
                        "show_toppings",
                        "show_cart",
                        "checkout",
                        "pay",
                        "summary",
                        "general_reply",
                        "unknown",
                    ],
                },
                "reply": {"type": "string"},
                "add_item": {
                    "type": ["object", "null"],
                    "properties": {
                        "items": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "item_name": {"type": ["string", "null"]},
                                    "size": {"type": ["string", "null"]},
                                    "quantity": {"type": "integer", "minimum": 1},
                                    "toppings": {
                                        "type": "array",
                                        "items": {"type": "string"},
                                    },
                                },
                                "required": ["item_name", "size", "quantity", "toppings"],
                                "additionalProperties": False,
                            },
                        },
                        "shared_toppings": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "required": ["items", "shared_toppings"],
                    "additionalProperties": False,
                },
                "remove_item": {
                    "type": ["object", "null"],
                    "properties": {
                        "cart_item_index": {"type": ["integer", "null"], "minimum": 1},
                        "item_name": {"type": ["string", "null"]},
                        "size": {"type": ["string", "null"]},
                        "quantity": {"type": ["integer", "null"], "minimum": 1},
                    },
                    "required": ["cart_item_index", "item_name", "size", "quantity"],
                    "additionalProperties": False,
                },
                "update_item": {
                    "type": ["object", "null"],
                    "properties": {
                        "cart_item_index": {"type": ["integer", "null"], "minimum": 1},
                        "item_name": {"type": ["string", "null"]},
                        "size": {"type": ["string", "null"]},
                        "new_size": {"type": ["string", "null"]},
                        "quantity": {"type": ["integer", "null"], "minimum": 1},
                        "sugar_level": {"type": ["string", "null"]},
                        "ice_level": {"type": ["string", "null"]},
                        "add_toppings": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "remove_toppings": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "required": [
                        "cart_item_index",
                        "item_name",
                        "size",
                        "new_size",
                        "quantity",
                        "sugar_level",
                        "ice_level",
                        "add_toppings",
                        "remove_toppings",
                    ],
                    "additionalProperties": False,
                },
                "customer_info": {
                    "type": ["object", "null"],
                    "properties": {
                        "name": {"type": ["string", "null"]},
                        "phone": {"type": ["string", "null"]},
                        "address": {"type": ["string", "null"]},
                        "note": {"type": ["string", "null"]},
                    },
                    "required": ["name", "phone", "address", "note"],
                    "additionalProperties": False,
                },
            },
            "required": ["intent", "reply", "add_item", "remove_item", "update_item", "customer_info"],
            "additionalProperties": False,
        }
        prompt = f"""
Bạn là trợ lý đặt đồ uống cho quán trà sữa. Hãy đọc tin nhắn khách và trả về JSON đúng schema.

Quy tắc:
- Chỉ dùng các món và topping có trong menu bên dưới.
- Nếu khách đang gọi món tự nhiên như "cho mình 2 trà sữa truyền thống size l thêm kem tươi", chọn intent = "add_item".
- Với `add_item`, luôn tách từng món thành từng phần tử riêng trong `items`.
- Nếu khách gọi nhiều món trong một câu như "1 cà phê đen và 1 cà phê macchiato", phải trả về 2 phần tử trong `items`, tuyệt đối không gộp nhiều tên món vào một `item_name`.
- Nếu khách viết kiểu phân bổ cùng một món theo nhiều size như "2 macchiato, 1 ly size M, 1 ly size L", vẫn phải trả về 2 phần tử trong `items` cho cùng món "Cà Phê Macchiato", một phần tử size M quantity 1 và một phần tử size L quantity 1.
- Hiểu các cách gọi ngắn thông dụng nếu map duy nhất được vào menu, ví dụ "cf đen" = "Cà Phê Đen", "macchiato" = "Cà Phê Macchiato".
- Với `add_item`, ưu tiên suy luận và điền đầy đủ `item_name`, `quantity`, `size` ngay trong JSON để hệ thống dùng trực tiếp; chỉ để `size = null` khi thật sự không suy ra được.
- Nếu khách liệt kê nhiều món ngăn bởi dấu phẩy, dấu cộng, hoặc chữ "và", hãy tách đúng từng món trong `items`.
- Nếu khách muốn chỉnh món đã có trong giỏ, như "thêm trân châu vào món số 2", "thêm kem tươi vào trà xoài size M đang có", "bỏ trân châu khỏi món số 2", "đổi món số 2 sang size L", "món số 2 ít đường", "món số 2 còn 1 ly", chọn intent = "update_item".
- Nếu khách muốn xóa món khỏi giỏ, như "xóa trà xoài", "bỏ món số 1", "bớt 1 trà sữa size L", chọn intent = "remove_item".
- Nếu khách cung cấp tên, số điện thoại, địa chỉ, ghi chú thì chọn intent = "customer_info".
- Trường `phone` chỉ dùng cho số điện thoại của chính người đang nhắn tin bằng Telegram này.
- Nếu khách nói "số điện thoại của mình", "sđt của mình", "đổi số", "số mới của mình là ...", "liên hệ mình qua số ..." thì điền vào `customer_info.phone`.
- Không tạo khái niệm người nhận hay số điện thoại của người khác. Nếu khách cố nói số điện thoại của người khác, vẫn chỉ coi đó là số của chính khách khi họ muốn cập nhật thông tin liên hệ của mình.
- Nếu khách hỏi xem menu, topping, giỏ hàng, chốt đơn, thanh toán, tóm tắt đơn thì map sang intent tương ứng.
- Nếu khách hỏi tư vấn, hỏi món nào ngon, hỏi giá, hoặc chat tự nhiên thì chọn "general_reply".
- Nếu không chắc chắn, chọn "unknown".
- `reply` phải là câu trả lời ngắn gọn bằng tiếng Việt, thân thiện, tối đa 3 câu.
- Với `add_item`, chuẩn hóa `size` của từng phần tử trong `items` về M hoặc L nếu suy ra được. Nếu không đủ dữ liệu, để null.
- Với topping, nếu topping chỉ áp dụng cho một món thì đặt trong `items[n].toppings`.
- Chỉ dùng `shared_toppings` khi người dùng nói rõ topping áp dụng cho tất cả món trong câu.
- Trả về danh sách tên topping canonical trong menu khi có thể.
- Với `update_item`, chỉ dùng khi khách muốn sửa món đã có trong giỏ. Ưu tiên `cart_item_index` nếu khách nói "món số 2".
- `cart_item_index` là số thứ tự 1-based đúng như đang hiển thị trong giỏ hàng. Không dùng zero-based và không được lệch số thứ tự.
- `size` là size hiện tại để nhận diện món nếu có, `new_size` là size mới khách muốn đổi sang.
- `quantity` là số lượng mới của dòng món sau khi cập nhật.
- `sugar_level` và `ice_level` là mức mới khách muốn đổi cho dòng món.
- Điền `add_toppings` với topping cần thêm và `remove_toppings` với topping cần bỏ khỏi dòng món hiện có.
- Với `remove_item`, ưu tiên `cart_item_index` nếu khách nói "món 1", "ly số 2"... Nếu khách nói theo tên món thì điền `item_name`; `quantity` là số lượng cần bớt nếu suy ra được, nếu khách muốn bỏ hẳn món thì để null.
- Không bịa món ngoài menu.

Menu đồ uống:
{menu_text}

Danh sách topping:
{toppings_text}

Giỏ hàng hiện tại:
{cart_text}

Lịch sử hội thoại gần đây:
{conversation_history or "Chưa có lịch sử hội thoại gần đây."}

Tin nhắn khách:
{user_message}
"""
        return self._generate_json(prompt, schema)

    def generate_customer_reply(
        self,
        *,
        user_message: str,
        menu_text: str,
        toppings_text: str,
        cart_text: str,
        conversation_history: str = "",
    ) -> str:
        prompt = f"""
Bạn là nhân viên CSKH của quán trà sữa trên Telegram.

Yêu cầu:
- Trả lời bằng tiếng Việt.
- Ngắn gọn, tự nhiên, tối đa 4 câu.
- Chỉ tư vấn dựa trên menu và giỏ hàng bên dưới.
- Nếu khách muốn gọi món, hãy hướng dẫn họ nói theo dạng tự nhiên hoặc dùng /add.
- Không bịa món, giá, topping ngoài dữ liệu được cung cấp.
- Không được đưa ra hành động như "đã thêm", "đã xóa", "đã cập nhật" vì nhân viên chưa thực hiện hành động đó. Hãy hướng dẫn khách nói theo dạng tự nhiên hoặc dùng lệnh để thực hiện.

Menu đồ uống:
{menu_text}

Danh sách topping:
{toppings_text}

Giỏ hàng hiện tại:
{cart_text}

Lịch sử hội thoại gần đây:
{conversation_history or "Chưa có lịch sử hội thoại gần đây."}

Tin nhắn khách:
{user_message}
"""
        return self._generate_text(prompt)

    def generate_action_reply(
        self,
        *,
        user_message: str,
        suggested_reply: str,
        system_result: str,
        menu_text: str,
        toppings_text: str,
        cart_text: str,
        conversation_history: str = "",
    ) -> str:
        prompt = f"""
Bạn là nhân viên CSKH của quán trà sữa trên Telegram.

Nhiệm vụ:
- Viết câu trả lời ngắn gọn, tự nhiên, tối đa 4 câu.
- Dùng `system_result` làm nguồn sự thật duy nhất về việc hệ thống vừa làm được hay không làm được gì.
- Có thể tham khảo `suggested_reply` để giữ giọng văn tự nhiên, nhưng không được lặp lại nội dung sai với `system_result`.
- Không bịa món, topping, giá, trạng thái đơn hay thao tác chưa xảy ra.
- Không cần tự chèn lại toàn bộ giỏ hàng hay tóm tắt đơn, vì hệ thống có thể thêm phần đó sau.

Gợi ý câu trả lời:
{suggested_reply or "Không có"}

Kết quả thực tế từ hệ thống:
{system_result}

Menu đồ uống:
{menu_text}

Danh sách topping:
{toppings_text}

Giỏ hàng hiện tại:
{cart_text}

Lịch sử hội thoại gần đây:
{conversation_history or "Chưa có lịch sử hội thoại gần đây."}

        Tin nhắn khách:
{user_message}
"""
        return self._generate_text(prompt)
