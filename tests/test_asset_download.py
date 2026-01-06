import html
import json
import re
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from re_gpt.sync_chatgpt import SyncChatGPT


def _make_response(status_code: int, payload: dict | None = None, text: str | None = None):
    response = MagicMock()
    response.status_code = status_code
    if payload is None:
        response.json.side_effect = ValueError("no json")
        response.text = text or ""
    else:
        response.json.return_value = payload
        response.text = text or json.dumps(payload)
    return response


class TestAssetDownload(unittest.TestCase):
    def setUp(self) -> None:
        self.chatgpt = SyncChatGPT(session_token="dummy", auth_token="token")
        self.chatgpt.session = MagicMock()

    def test_resolve_asset_pointer_handles_sediment_fallback(self):
        first = _make_response(404, text="not found")
        second = _make_response(200, payload={"download_url": "https://example.com/image.png"})
        self.chatgpt.session.post.side_effect = [first, second]

        download_url = self.chatgpt.resolve_asset_pointer("sediment://file_123456789")

        self.assertEqual(download_url, "https://example.com/image.png")
        self.assertEqual(self.chatgpt.session.post.call_count, 2)
        first_call, second_call = self.chatgpt.session.post.call_args_list
        self.assertEqual(first_call.kwargs["json"], {"asset_pointer": "sediment://file_123456789"})
        self.assertEqual(second_call.kwargs["json"], {"asset_pointer": "file-service://file_123456789"})

    def test_resolve_asset_pointer_tries_dash_variant(self):
        responses = [
            _make_response(404, text="missing"),
            _make_response(404, text="still missing"),
            _make_response(200, payload={"download_url": "https://example.com/alt.png"}),
        ]
        self.chatgpt.session.post.side_effect = responses

        download_url = self.chatgpt.resolve_asset_pointer("sediment://file_abcdef")

        self.assertEqual(download_url, "https://example.com/alt.png")
        calls = self.chatgpt.session.post.call_args_list
        self.assertEqual(calls[0].kwargs["json"], {"asset_pointer": "sediment://file_abcdef"})
        self.assertEqual(calls[1].kwargs["json"], {"asset_pointer": "file-service://file_abcdef"})
        self.assertEqual(calls[2].kwargs["json"], {"asset_pointer": "file-service://file-abcdef"})

    def test_resolve_asset_pointer_falls_back_to_files_endpoint(self):
        self.chatgpt.session.post.return_value = _make_response(404, text="missing")
        self.chatgpt.session.get.return_value = _make_response(
            200,
            payload={"download_url": "https://example.com/direct.png"},
        )

        download_url = self.chatgpt.resolve_asset_pointer("file-service://file-direct")

        self.assertEqual(download_url, "https://example.com/direct.png")
        self.chatgpt.session.get.assert_called_once()
        called_url = self.chatgpt.session.get.call_args.args[0]
        self.assertTrue(called_url.endswith("/files/file-direct/download"))

    def test_resolve_asset_pointer_uses_conversation_page(self):
        # Fail both asset/get and files endpoint to trigger HTML fallback.
        self.chatgpt.session.post.return_value = _make_response(404, text="missing")
        self.chatgpt.session.get.return_value = _make_response(404, text="missing")
        html_payload = """
            <html>
              <body>
                <img src="https://chatgpt.com/backend-api/estuary/content?id=file-xyz&sig=abc">
              </body>
            </html>
        """
        self.chatgpt.fetch_conversation_page = MagicMock(return_value=html_payload)

        download_url = self.chatgpt.resolve_asset_pointer(
            "sediment://file_xyz",
            conversation_id="conv-123",
        )

        self.assertEqual(
            download_url,
            "https://chatgpt.com/backend-api/estuary/content?id=file-xyz&sig=abc",
        )
        self.chatgpt.fetch_conversation_page.assert_called_once_with("conv-123")

    def test_resolve_asset_pointer_handles_estuary_links_from_raw_html(self):
        fixture_path = Path("Reddit Test Message.html")
        if fixture_path.is_file():
            raw_html = fixture_path.read_text(encoding="utf-8")
        else:
            raw_html = """
                <html>
                  <body>
                    <img src="https://chatgpt.com/backend-api/estuary/content?id=file_abc123&sig=demo">
                    <img src="https://chatgpt.com/backend-api/estuary/content?id=file-xyz789&sig=demo">
                  </body>
                </html>
            """
        decoded_html = html.unescape(raw_html)

        def fail_post(*_, **__):
            return _make_response(404, text="missing")

        def fail_get(*_, **__):
            return _make_response(404, text="missing")

        self.chatgpt.session.post.side_effect = fail_post
        self.chatgpt.session.get.side_effect = fail_get

        id_pattern = re.compile(r"id=(file[-_][^&\"'>\\s]+)")
        file_ids = {match.group(1) for match in id_pattern.finditer(decoded_html)}
        if not file_ids:
            raw_html = """
                <html>
                  <body>
                    <img src="https://chatgpt.com/backend-api/estuary/content?id=file_abc123&sig=demo">
                    <img src="https://chatgpt.com/backend-api/estuary/content?id=file-xyz789&sig=demo">
                  </body>
                </html>
            """
            decoded_html = html.unescape(raw_html)
            file_ids = {match.group(1) for match in id_pattern.finditer(decoded_html)}
        self.assertTrue(file_ids)
        self.chatgpt.fetch_conversation_page = MagicMock(return_value=raw_html)

        expected_urls = {}
        for file_id in file_ids:
            pattern = re.compile(
                r"https://chatgpt\.com/backend-api/[^\s\"'>]*"
                + re.escape(file_id)
                + r"[^\s\"'>]*"
            )
            match = pattern.search(decoded_html)
            self.assertIsNotNone(match, f"Expected estuary URL for {file_id}")
            expected_urls[file_id] = match.group(0)

        pointer_variants = set()
        for file_id in file_ids:
            pointer_variants.add(f"file-service://{file_id}")
            pointer_variants.add(file_id)
            if file_id.startswith("file_"):
                swapped = file_id.replace("file_", "file-", 1)
            elif file_id.startswith("file-"):
                swapped = file_id.replace("file-", "file_", 1)
            else:
                swapped = ""
            if file_id.startswith("file_"):
                pointer_variants.add(f"sediment://{file_id}")
            if swapped and swapped != file_id:
                pointer_variants.add(swapped)
                pointer_variants.add(f"file-service://{swapped}")

        for pointer in pointer_variants:
            remainder = pointer.split("://", 1)[-1] if "://" in pointer else pointer
            candidates = {remainder}
            if remainder.startswith("file_"):
                candidates.add(remainder.replace("file_", "file-", 1))
            if remainder.startswith("file-"):
                candidates.add(remainder.replace("file-", "file_", 1))
            expected_candidates = {
                expected_urls[identifier]
                for identifier in candidates
                if identifier in expected_urls
            }
            self.assertTrue(
                expected_candidates,
                f"No expected URL found for pointer {pointer}",
            )
            download_url = self.chatgpt.resolve_asset_pointer(
                pointer, conversation_id="conv-raw"
            )
            self.assertIn(download_url, expected_candidates)


if __name__ == "__main__":
    unittest.main()
