import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from re_gpt.storage import AssetDownload, ConversationStorage


def _make_chat(title: str, user_text: str, assistant_text: str, update_time: float = 123.0) -> dict:
    return {
        "title": title,
        "update_time": update_time,
        "mapping": {
            "1": {
                "message": {
                    "author": {"role": "user"},
                    "content": {"parts": [user_text]},
                    "create_time": 1,
                }
            },
            "2": {
                "message": {
                    "author": {"role": "assistant"},
                    "content": {"parts": [assistant_text]},
                    "create_time": 2,
                }
            },
        },
    }


class TestConversationStorage(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = TemporaryDirectory()
        base_path = Path(self.tempdir.name)
        self.db_path = base_path / "history.sqlite3"
        self.export_dir = base_path / "exports"
        self.storage = ConversationStorage(db_path=self.db_path, export_dir=self.export_dir)

    def tearDown(self) -> None:
        self.storage.close()
        self.tempdir.cleanup()

    def test_record_conversations_tracks_add_and_update(self) -> None:
        catalog = [
            {"id": "conv-1", "title": "First chat", "update_time": 111.0},
            {"id": "conv-2", "title": "Second chat", "update_time": 222.0},
        ]

        stats_first = self.storage.record_conversations(catalog)
        self.assertEqual(stats_first.added, 2)
        self.assertEqual(stats_first.updated, 0)

        stats_second = self.storage.record_conversations(catalog)
        self.assertEqual(stats_second.added, 0)
        self.assertEqual(stats_second.updated, 2)

        key = self.storage.get_conversation_key("conv-1")
        self.assertIsNotNone(key)

    def test_persist_chat_detects_new_messages(self) -> None:
        conversation_id = "conv-123"
        chat = _make_chat("Sample Chat", "Hello", "Hi there")

        result_first = self.storage.persist_chat(conversation_id, chat)
        self.assertEqual(result_first.new_messages, 2)
        self.assertEqual(result_first.total_messages, 2)
        self.assertTrue(result_first.json_path.exists())
        self.assertTrue(result_first.json_path.name.startswith("sample-chat__"))
        self.assertTrue(result_first.json_path.name.endswith(".json"))

        with result_first.json_path.open("r", encoding="utf-8") as handle:
            stored_chat = json.load(handle)
        self.assertEqual(stored_chat.get("title"), "Sample Chat")

        result_second = self.storage.persist_chat(conversation_id, chat)
        self.assertEqual(result_second.new_messages, 0)
        self.assertEqual(result_second.total_messages, 2)

        rows = self.storage._connection.execute(
            "SELECT message_key FROM messages WHERE conversation_id = ? ORDER BY message_index",
            (conversation_id,),
        ).fetchall()
        for index, (message_key,) in enumerate(rows, start=0):
            self.assertIsNotNone(message_key)
            self.assertIn(f".{index:04d}", message_key)

    def test_persist_chat_downloads_assets(self) -> None:
        conversation_id = "conv-asset"
        pointer = "file-service://file-EXAMPLEASSET"
        sediment_pointer = "sediment://file_ABC123"

        chat = {
            "title": "Plot Run",
            "mapping": {
                "1": {
                    "message": {
                        "author": {"role": "assistant"},
                        "content": {
                            "content_type": "multimodal_text",
                            "parts": [
                                {
                                    "content_type": "image_asset_pointer",
                                    "asset_pointer": pointer,
                                    "size_bytes": 10,
                                },
                                {
                                    "content_type": "image_asset_pointer",
                                    "asset_pointer": sediment_pointer,
                                    "size_bytes": 20,
                                },
                                "Here is the plot.",
                            ],
                        },
                        "create_time": 1,
                        "metadata": {
                            "attachments": [
                                {
                                    "id": "file-EXAMPLEASSET",
                                    "mime_type": "image/png",
                                    "size": 10,
                                }
                            ],
                            "aggregate_result": {
                                "messages": [
                                    {"image_url": pointer},
                                    {"image_url": sediment_pointer, "size_bytes": 20},
                                ],
                                "jupyter_messages": [
                                    {
                                        "content": {
                                            "data": {
                                                "image/vnd.openai.fileservice2.png": json.dumps(
                                                    {
                                                        "url": pointer,
                                                        "bytes": 10,
                                                    }
                                                )
                                            }
                                        }
                                    }
                                ],
                            }
                        },
                    }
                }
            },
        }

        calls: list[str] = []

        def fake_fetcher(p: str) -> AssetDownload:
            calls.append(p)
            return AssetDownload(content=b"PNGDATA", content_type="image/png")

        result = self.storage.persist_chat(
            conversation_id,
            chat,
            asset_fetcher=fake_fetcher,
        )

        self.assertEqual(calls, [pointer, sediment_pointer])
        self.assertEqual(len(result.asset_paths), 2)

        asset_names = {path.name for path in result.asset_paths}
        self.assertTrue(
            any(name.endswith(".png") and "file-EXAMPLEASSET" in name for name in asset_names)
        )
        self.assertTrue(
            any(name.endswith(".png") and "file_ABC123" in name for name in asset_names)
        )


if __name__ == "__main__":
    unittest.main()
