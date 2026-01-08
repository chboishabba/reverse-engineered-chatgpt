import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import tests.mock_helper

from re_gpt import cli
from re_gpt.errors import InvalidSessionToken
from re_gpt.storage import CatalogUpdateStats, PersistResult
from re_gpt.view_helpers import parse_view_argument


class TestCli(unittest.TestCase):
    @patch('re_gpt.cli.subprocess.run')
    def test_select_and_view_conversation(self, mock_subprocess_run):
        # Mock the ChatGPT object and its methods
        mock_chatgpt = MagicMock()
        mock_chatgpt.get_conversation.return_value.fetch_chat.return_value = {}

        # Call the function directly
        cli.handle_view_command(
            '1',
            mock_chatgpt,
            [{'id': '123', 'title': 'Test Conversation'}],
            [{'id': '123', 'title': 'Test Conversation'}],
        )

        # Assert that the conversation was fetched and viewed
        mock_chatgpt.get_conversation.assert_called_with(
            '123', title='Test Conversation'
        )
        mock_subprocess_run.assert_called_once()

    @patch('re_gpt.cli.SyncChatGPT')
    def test_verify_session_token_success(self, mock_sync_chatgpt):
        # Mock the context manager
        mock_chatgpt_instance = MagicMock()
        mock_chatgpt_instance.__enter__.return_value = mock_chatgpt_instance
        mock_chatgpt_instance.__exit__.return_value = False
        mock_chatgpt_instance.auth_token = "access-token"
        mock_sync_chatgpt.return_value = mock_chatgpt_instance

        # This should not raise an exception
        cli.verify_session_token('test_token')

    @patch('re_gpt.cli.SyncChatGPT')
    def test_verify_session_token_failure(self, mock_sync_chatgpt):
        # Mock the context manager
        mock_chatgpt_instance = MagicMock()
        mock_chatgpt_instance.__enter__.return_value = mock_chatgpt_instance
        mock_chatgpt_instance.__exit__.return_value = False
        mock_chatgpt_instance.auth_token = None
        mock_sync_chatgpt.return_value = mock_chatgpt_instance

        # This should raise InvalidSessionToken
        with self.assertRaises(InvalidSessionToken):
            cli.verify_session_token('test_token')

    @patch('builtins.print')
    def test_download_conversation(self, mock_print):
        # Mock the ChatGPT object and its methods
        mock_chatgpt = MagicMock()
        mock_chatgpt.list_all_conversations.return_value = [
            {'id': '123', 'title': 'Test Conversation'}
        ]
        mock_chatgpt.get_conversation.return_value.fetch_chat.return_value = {}

        # Mock the storage object
        mock_storage = MagicMock()
        mock_storage.persist_chat.return_value = PersistResult(
            json_path=Path("conversation_123.json"),
            new_messages=0,
            total_messages=2,
        )
        mock_storage.record_conversations.return_value = CatalogUpdateStats()

        # Call the function directly
        cli.handle_download_command('download 123', mock_chatgpt, mock_storage)

        # Assert that the conversation was downloaded
        mock_storage.persist_chat.assert_called_once()
        _, kwargs = mock_storage.persist_chat.call_args
        self.assertIn("asset_fetcher", kwargs)
        self.assertTrue(kwargs["asset_fetcher"])
        mock_storage.record_conversations.assert_called()

    @patch('builtins.print')
    def test_download_conversation_by_index(self, mock_print):
        mock_chatgpt = MagicMock()
        mock_chatgpt.get_conversation.return_value.fetch_chat.return_value = {}
        mock_storage = MagicMock()
        mock_storage.persist_chat.return_value = PersistResult(
            json_path=Path("conversation_abc.json"),
            new_messages=1,
            total_messages=1,
        )
        mock_storage.record_conversations.return_value = CatalogUpdateStats()
        current_page = [{'id': 'abc', 'title': 'Indexed Conversation'}]

        cli.handle_download_command(
            'download 1',
            mock_chatgpt,
            mock_storage,
            current_page=current_page,
            cached_conversations=current_page,
        )

        mock_storage.persist_chat.assert_called_once()
        mock_chatgpt.list_all_conversations.assert_not_called()
        mock_storage.record_conversations.assert_not_called()

    @patch('builtins.print')
    def test_download_conversation_reports_assets(self, mock_print):
        mock_chatgpt = MagicMock()
        mock_chatgpt.list_all_conversations.return_value = [{'id': '123', 'title': 'Example'}]
        mock_chatgpt.get_conversation.return_value.fetch_chat.return_value = {}
        mock_storage = MagicMock()
        mock_storage.persist_chat.return_value = PersistResult(
            json_path=Path("sample.json"),
            new_messages=0,
            total_messages=5,
            asset_paths=(Path("assets/file.png"),),
        )
        mock_storage.record_conversations.return_value = CatalogUpdateStats()

        cli.handle_download_command('download 123', mock_chatgpt, mock_storage)

        printed_messages = [
            str(call.args[0]) for call in mock_print.call_args_list if call.args
        ]
        combined = " ".join(printed_messages)
        self.assertIn("saved 1 image(s)", combined)

    @patch('builtins.print')
    def test_download_list_catalogues_conversations(self, mock_print):
        mock_chatgpt = MagicMock()
        mock_chatgpt.list_all_conversations.return_value = [
            {'id': 'one', 'title': 'First'},
            {'id': 'two', 'title': 'Second'},
        ]
        mock_storage = MagicMock()
        mock_storage.record_conversations.return_value = CatalogUpdateStats(added=2, updated=0)

        cli.handle_download_command('download list', mock_chatgpt, mock_storage)

        mock_storage.record_conversations.assert_called_once()
        mock_print.assert_any_call('Catalogued 2 conversation(s) (added 2, refreshed 0).')

    @patch('builtins.input', side_effect=['search Test', 'q'])
    @patch('builtins.print')
    def test_search_conversation(self, mock_print, mock_input):
        # Mock the ChatGPT object and its methods
        mock_chatgpt = MagicMock()
        mock_chatgpt.list_conversations_page.return_value = {
            'items': [
                {'id': '123', 'title': 'Test Conversation'}
            ]
        }
        # Mock the storage object
        mock_storage = MagicMock()
        mock_storage.record_conversations.return_value = CatalogUpdateStats()
        mock_storage.search_conversations.return_value = []

        # Run the conversation selection loop
        with patch('re_gpt.cli.SyncChatGPT', return_value=mock_chatgpt):
            cli._pick_conversation_id(mock_chatgpt, mock_storage)

        # Assert that the search results are printed
        mock_print.assert_any_call("Found 1 conversation(s):")
        mock_storage.record_conversations.assert_called()

    @patch('builtins.input', side_effect=['search Missing', 'q'])
    @patch('builtins.print')
    def test_search_conversation_uses_storage_catalog(self, mock_print, mock_input):
        mock_chatgpt = MagicMock()
        mock_chatgpt.list_conversations_page.return_value = {
            'items': [
                {'id': '456', 'title': 'Another Chat'}
            ]
        }

        mock_storage = MagicMock()
        mock_storage.record_conversations.return_value = CatalogUpdateStats()
        mock_storage.search_conversations.return_value = [
            {'id': 'abc-123', 'title': 'Missing Pieces'}
        ]

        with patch('re_gpt.cli.SyncChatGPT', return_value=mock_chatgpt):
            cli._pick_conversation_id(mock_chatgpt, mock_storage)

        mock_storage.search_conversations.assert_called_once_with('Missing')
        mock_print.assert_any_call("Found 1 conversation(s):")
        mock_print.assert_any_call("- Missing Pieces [abc-123]")

    @patch('builtins.input', side_effect=['next', 'prev', 'q'])
    @patch('builtins.print')
    def test_navigate_conversations(self, mock_print, mock_input):
        # Mock the ChatGPT object and its methods
        mock_chatgpt = MagicMock()
        mock_chatgpt.list_conversations_page.side_effect = [
            {
                'items': [
                    {'id': '123', 'title': 'Test Conversation 1'}
                ] * 10
            },
            {
                'items': [
                    {'id': '456', 'title': 'Test Conversation 2'}
                ]
            },
            {
                'items': [
                    {'id': '123', 'title': 'Test Conversation 1'}
                ] * 10
            },
        ]
        # Mock the storage object
        mock_storage = MagicMock()

        # Run the conversation selection loop
        with patch('re_gpt.cli.SyncChatGPT', return_value=mock_chatgpt):
            cli._pick_conversation_id(mock_chatgpt, mock_storage)

        # Assert that the next and prev pages are loaded
        self.assertEqual(mock_chatgpt.list_conversations_page.call_count, 3)

    @patch('builtins.print')
    def test_view_invalid_conversation(self, mock_print):
        # Mock the ChatGPT object and its methods
        mock_chatgpt = MagicMock()

        # Call the function directly
        cli.handle_view_command(
            'invalid_command',
            mock_chatgpt,
            [],
            [],
        )

        # Assert that an error message is printed
        mock_print.assert_any_call("Conversation 'invalid_command' not found.")

    @patch('builtins.print')
    def test_list_conversations_flag(self, mock_print):
        mock_chatgpt = MagicMock()
        mock_chatgpt.list_all_conversations.return_value = [
            {'id': 'a', 'title': 'Alpha'},
            {'id': 'b', 'title': 'Beta'},
        ]

        mock_storage_ctx = MagicMock()
        mock_storage_instance = MagicMock()
        mock_storage_ctx.__enter__.return_value = mock_storage_instance
        mock_storage_ctx.__exit__.return_value = False

        args = MagicMock(list=True, nostore=False, key=None, model=None)
        with patch('argparse.ArgumentParser.parse_args', return_value=args):
            with patch('re_gpt.cli.obtain_session_token', return_value='token'):
                mock_sync_ctx = MagicMock()
                mock_sync_ctx.__enter__.return_value = mock_chatgpt
                with patch('re_gpt.cli.SyncChatGPT', return_value=mock_sync_ctx):
                    with patch('re_gpt.cli.ConversationStorage', return_value=mock_storage_ctx):
                        cli.main()

        mock_chatgpt.list_all_conversations.assert_called_once()
        mock_storage_instance.record_conversations.assert_called_once_with(
            mock_chatgpt.list_all_conversations.return_value
        )
        mock_print.assert_any_call("a\tAlpha")

    def test_parse_view_argument_lines_range(self):
        selector, lines_range, since = parse_view_argument(
            "Demo chat lines 3-5"
        )
        self.assertEqual(selector, "Demo chat")
        self.assertEqual(lines_range, (3, 5))
        self.assertFalse(since)

    def test_parse_view_argument_since_last_update(self):
        selector, lines_range, since = parse_view_argument(
            "Demo chat since last update"
        )
        self.assertEqual(selector, "Demo chat")
        self.assertIsNone(lines_range)
        self.assertTrue(since)

    @patch('os.remove')
    @patch('re_gpt.cli.tempfile.NamedTemporaryFile')
    @patch('re_gpt.cli.extract_ordered_messages')
    @patch('re_gpt.cli.subprocess.run')
    def test_handle_view_command_since_last_update_filters_old_messages(
        self,
        mock_run,
        mock_extract,
        mock_tmpfile,
        mock_remove,
    ):
        mock_extract.return_value = [
            {"author": "user", "content": "welcome", "message_index": 0},
            {"author": "assistant", "content": "cached", "message_index": 1},
            {"author": "assistant", "content": "fresh", "message_index": 2},
        ]
        mock_tmp = MagicMock()
        mock_tmp.__enter__.return_value = mock_tmp
        mock_tmp.__exit__.return_value = False
        mock_tmp.name = "/tmp/fake"
        mock_tmp.write = MagicMock()
        mock_tmpfile.return_value = mock_tmp

        mock_chatgpt = MagicMock()
        mock_conversation = MagicMock()
        mock_conversation.title = "Filtered"
        mock_conversation.fetch_chat.return_value = {}
        mock_chatgpt.get_conversation.return_value = mock_conversation

        mock_storage = MagicMock()
        mock_storage.count_messages.return_value = 2

        current_page = [{'id': '123', 'title': 'Filtered'}]

        cli.handle_view_command(
            '1 since last update',
            mock_chatgpt,
            current_page,
            current_page,
            storage=mock_storage,
        )

        mock_storage.count_messages.assert_called_once_with('123')
        written = "".join(call.args[0] for call in mock_tmp.write.call_args_list)
        self.assertIn("fresh", written)
        self.assertNotIn("cached", written)


if __name__ == '__main__':
    unittest.main()
