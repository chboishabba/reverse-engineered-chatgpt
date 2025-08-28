from __future__ import annotations

from configparser import ConfigParser
from pathlib import Path
from typing import Iterable


def get_session_token(config_file: str | Path | None = None) -> str:
    """Return the ChatGPT session token.

    The token is read from one of the following locations (in order):

    1. ``config_file`` if provided.
    2. ``config.ini`` in the current working directory.
    3. ``~/.chatgpt_session`` in the user's home directory.

    ``config.ini`` files must contain a ``[session]`` section with a ``token``
    field. ``~/.chatgpt_session`` should contain only the raw token value.
    """

    candidates: Iterable[Path | None] = (
        Path(config_file).expanduser() if config_file else None,
        Path("config.ini"),
        Path.home() / ".chatgpt_session",
    )

    for path in candidates:
        if not path or not path.is_file():
            continue
        if path.suffix == ".ini":
            parser = ConfigParser()
            parser.read(path)
            if parser.has_option("session", "token"):
                return parser.get("session", "token").strip()
        else:
            return path.read_text().strip()

    raise FileNotFoundError(
        "Session token not found. Create config.ini or ~/.chatgpt_session with your token."
    )
