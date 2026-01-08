import argparse
import builtins
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from scripts import view_conversation


class TestViewConversation(unittest.TestCase):
    def setUp(self) -> None:
        self.base_args = {
            "conversation_id": "conv-1",
            "title": None,
            "lines_range": None,
            "lines_min": None,
            "lines_max": None,
            "since_last_update": False,
            "since_time": None,
            "token": None,
            "model": None,
            "output": None,
            "remote": False,
            "store": False,
            "nostore": False,
        }
        self.messages = [
            {"author": "assistant", "message_index": 0, "content": "hello"},
            {"author": "user", "message_index": 1, "content": "ok"},
        ]

    def _make_args(self, **overrides: object) -> SimpleNamespace:
        values = dict(self.base_args)
        values.update(overrides)
        return SimpleNamespace(**values)

    @patch("builtins.print")
    @patch("scripts.view_conversation.extract_ordered_messages")
    @patch("scripts.view_conversation.get_session_token", return_value="token")
    @patch("scripts.view_conversation.ConversationStorage")
    @patch("scripts.view_conversation.SyncChatGPT")
    def test_title_partial_match(
        self,
        mock_sync_chatgpt,
        mock_storage_cls,
        mock_token,
        mock_extract,
        mock_print,
    ) -> None:
        mock_extract.return_value = self.messages
        mock_chatgpt = MagicMock()
        mock_sync_chatgpt.return_value.__enter__.return_value = mock_chatgpt
        mock_chatgpt.list_all_conversations.return_value = [
            {"id": "match-1", "title": "Fish Spine Symbolism"},
            {"id": "match-2", "title": "Other Conversation"},
        ]
        mock_chatgpt.fetch_conversation.return_value = {"mapping": {}, "title": "Fish"}

        mock_storage = MagicMock()
        mock_storage.count_messages.return_value = 0
        mock_storage_cls.return_value.__enter__.return_value = mock_storage

        args = self._make_args(conversation_id=None, title="fish spine", remote=True)
        with patch.object(argparse.ArgumentParser, "parse_args", return_value=args):
            view_conversation.main()

        mock_chatgpt.fetch_conversation.assert_called_once_with("match-1")

    @patch("builtins.print")
    @patch("scripts.view_conversation.extract_ordered_messages")
    @patch("scripts.view_conversation.get_session_token", return_value="token")
    @patch("scripts.view_conversation.ConversationStorage")
    @patch("scripts.view_conversation.SyncChatGPT")
    def test_remote_flag_uses_fetch_conversation(
        self,
        mock_sync_chatgpt,
        mock_storage_cls,
        mock_token,
        mock_extract,
        mock_print,
    ) -> None:
        mock_extract.return_value = self.messages
        mock_chatgpt = MagicMock()
        mock_sync_chatgpt.return_value.__enter__.return_value = mock_chatgpt
        mock_chatgpt.fetch_conversation.return_value = {"mapping": {}, "title": "Fish"}
        mock_conv = MagicMock()
        mock_conv.fetch_chat.return_value = {"mapping": {}, "title": "Fish"}
        mock_chatgpt.get_conversation.return_value = mock_conv

        mock_storage = MagicMock()
        mock_storage.count_messages.return_value = 0
        mock_storage_cls.return_value.__enter__.return_value = mock_storage

        args = self._make_args(remote=True)
        with patch.object(argparse.ArgumentParser, "parse_args", return_value=args):
            view_conversation.main()

        mock_chatgpt.fetch_conversation.assert_called_once_with("conv-1")
        mock_chatgpt.get_conversation.assert_not_called()
        mock_storage.persist_chat.assert_not_called()

    @patch("builtins.print")
    @patch("scripts.view_conversation.extract_ordered_messages")
    @patch("scripts.view_conversation.get_session_token", return_value="token")
    @patch("scripts.view_conversation.ConversationStorage")
    @patch("scripts.view_conversation.SyncChatGPT")
    def test_store_flag_persists_remote_fetch(
        self,
        mock_sync_chatgpt,
        mock_storage_cls,
        mock_token,
        mock_extract,
        mock_print,
    ) -> None:
        mock_extract.return_value = self.messages
        mock_chatgpt = MagicMock()
        mock_sync_chatgpt.return_value.__enter__.return_value = mock_chatgpt
        mock_chatgpt.fetch_conversation.return_value = {"mapping": {}, "title": "Fish"}

        mock_storage = MagicMock()
        mock_storage.count_messages.return_value = 0
        mock_storage_cls.return_value.__enter__.return_value = mock_storage

        args = self._make_args(remote=True, store=True)
        with patch.object(argparse.ArgumentParser, "parse_args", return_value=args):
            view_conversation.main()

        mock_storage.persist_chat.assert_called_once()
        persisted_conversation_id, persisted_chat, persisted_messages = mock_storage.persist_chat.call_args[0]
        self.assertEqual(persisted_conversation_id, "conv-1")
        self.assertEqual(persisted_chat, {"mapping": {}, "title": "Fish"})
        self.assertEqual(persisted_messages, self.messages)
