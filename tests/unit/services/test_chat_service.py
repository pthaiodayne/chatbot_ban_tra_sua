from __future__ import annotations

from app.chat_service import get_recent_chat_messages, render_chat_history, save_chat_message
from tests.unit.base import DatabaseTestCase


class ChatServiceTestCase(DatabaseTestCase):
    def test_keeps_only_last_four_messages_per_user(self) -> None:
        for idx in range(6):
            role = "user" if idx % 2 == 0 else "assistant"
            save_chat_message(self.db, "tele-1", role, f"message-{idx}")

        messages = get_recent_chat_messages(self.db, "tele-1")

        self.assertEqual(len(messages), 4)
        self.assertEqual([message.content for message in messages], ["message-2", "message-3", "message-4", "message-5"])

    def test_render_chat_history_labels_roles(self) -> None:
        save_chat_message(self.db, "tele-2", "user", "Xin chào")
        save_chat_message(self.db, "tele-2", "assistant", "Chào bạn")

        history = render_chat_history(get_recent_chat_messages(self.db, "tele-2"))

        self.assertIn("Khách: Xin chào", history)
        self.assertIn("Bot: Chào bạn", history)


if __name__ == "__main__":
    import unittest

    unittest.main()
