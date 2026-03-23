from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from re_gpt import utils


def test_get_session_token_reads_chunked_new_file():
    with TemporaryDirectory() as tmpdir:
        home = Path(tmpdir)
        (home / ".chatgpt_session_new").write_text("chunk-one\nchunk-two\n", encoding="utf-8")

        with patch.object(utils.Path, "home", return_value=home):
            token = utils.get_session_token(config_path=str(home / "config.ini"))

    assert token == "chunk-onechunk-two"


def test_get_session_token_prefers_config_ini_over_chunked_file():
    with TemporaryDirectory() as tmpdir:
        home = Path(tmpdir)
        (home / ".chatgpt_session_new").write_text("chunk-one\nchunk-two\n", encoding="utf-8")
        (home / "config.ini").write_text("[session]\ntoken=config-token\n", encoding="utf-8")

        with patch.object(utils.Path, "home", return_value=home):
            token = utils.get_session_token(config_path=str(home / "config.ini"))

    assert token == "config-token"
