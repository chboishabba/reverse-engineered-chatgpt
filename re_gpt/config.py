"""Utility helpers for loading the ChatGPT session token."""

from __future__ import annotations

import configparser
from pathlib import Path
from typing import Optional


DEFAULT_CONFIG_PATHS = [Path("config.ini"), Path.home() / ".chatgpt_session"]


def get_session_token(path: Optional[str] = None) -> str:
    """Return the ChatGPT session token.

    The token can be stored either as plain text in a file or inside an INI
    file under ``[session]`` with the key ``token``. If ``path`` is ``None``,
    the function searches the default locations ``config.ini`` in the current
    working directory and ``~/.chatgpt_session``.

    Args:
        path: Optional path to the configuration file.

    Returns:
        The session token as a string.

    Raises:
        FileNotFoundError: If no suitable configuration file is found.
        KeyError: If the configuration file does not contain the token.
    """

    paths = [Path(path).expanduser()] if path else DEFAULT_CONFIG_PATHS

    for cfg_path in paths:
        if not cfg_path.exists():
            continue

        if cfg_path.suffix in {".ini", ".cfg"}:
            parser = configparser.ConfigParser()
            parser.read(cfg_path)
            try:
                return parser["session"]["token"].strip()
            except KeyError as exc:
                raise KeyError("Session token not found in config file") from exc
        else:
            return cfg_path.read_text().strip()

    raise FileNotFoundError(
        "No session token file found. Provide a path or create config.ini or "
        "~/.chatgpt_session",
    )
