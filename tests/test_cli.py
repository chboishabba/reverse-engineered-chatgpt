import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import tests.mock_helper

from re_gpt import cli
from re_gpt.errors import InvalidSessionToken
from re_gpt.storage import CatalogUpdateStats, PersistResult


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


if __name__ == '__main__':
    unittest.main()
