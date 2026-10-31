from __future__ import annotations

import inspect
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from re_gpt.mcp_server import chatgpt_revive_multi


class PublicMultiParentMCPContractTests(unittest.TestCase):
    def test_request_id_is_required_and_forwarded_by_public_mcp_entrypoint(self):
        parameter = inspect.signature(chatgpt_revive_multi).parameters["request_id"]
        self.assertIs(parameter.default, inspect.Parameter.empty)

        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "handoff.json"
            with patch("re_gpt.multi_parent_revival.revive_from_handoff") as revive:
                revive.return_value = {"state": "complete", "request_id": "public-request"}
                result = chatgpt_revive_multi(
                    parent_thread_ids=["canonical-a", "canonical-b"],
                    handoff_path=str(artifact),
                    prompt="continue",
                    request_id="public-request",
                    db_path=str(Path(directory) / "receipts.sqlite"),
                    follow=False,
                    follow_timeout=12.5,
                )

        self.assertEqual(result["request_id"], "public-request")
        revive.assert_called_once_with(
            ["canonical-a", "canonical-b"],
            artifact,
            "continue",
            request_id="public-request",
            db_path=Path(directory) / "receipts.sqlite",
            follow=False,
            follow_timeout=12.5,
        )


if __name__ == "__main__":
    unittest.main()
