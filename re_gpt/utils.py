import configparser
import hashlib
import os
import platform
from pathlib import Path
from typing import Optional
import time

from .errors import TokenNotProvided

current_os = platform.system()
current_file_directory = "/".join(
    __file__.split("\\" if current_os == "Windows" else "/")[0:-1]
)

funcaptcha_bin_folder_path = f"{current_file_directory}/funcaptcha_bin"
latest_release_url = (
    "https://api.github.com/repos/Zai-Kun/reverse-engineered-chatgpt/releases"
)

binary_file_name = {"Windows": "windows_arkose.dll", "Linux": "linux_arkose.so"}.get(
    current_os
)

binary_path = {
    "Windows": f"{funcaptcha_bin_folder_path}/{binary_file_name}",
    "Linux": f"{funcaptcha_bin_folder_path}/{binary_file_name}",
}.get(current_os)


def calculate_file_md5(file_path):
    with open(file_path, "rb") as file:
        file_content = file.read()
        md5_hash = hashlib.md5(file_content).hexdigest()
        return md5_hash


def get_file_url(json_data):
    for release in json_data:
        if release["tag_name"].startswith("funcaptcha_bin"):
            file_url = next(
                asset["browser_download_url"]
                for asset in release["assets"]
                if asset["name"] == binary_file_name
            )
            return file_url


# async
async def async_download_binary(session, output_path, file_url):
    with open(output_path, "wb") as output_file:
        response = await session.get(
            url=file_url, content_callback=lambda chunk: output_file.write(chunk)
        )


async def async_get_binary_path(session):
    if binary_path is None:
        return None

    if not os.path.exists(funcaptcha_bin_folder_path) or not os.path.isdir(
        funcaptcha_bin_folder_path
    ):
        os.mkdir(funcaptcha_bin_folder_path)

    if os.path.isfile(binary_path):
        try:
            local_binary_hash = calculate_file_md5(binary_path)
            response = await session.get(latest_release_url)
            json_data = response.json()

            for line in json_data["body"].splitlines():
                if line.startswith(current_os):
                    latest_binary_hash = line.split("=")[-1]
                    break

            if local_binary_hash != latest_binary_hash:
                file_url = get_file_url(json_data)

                await async_download_binary(session, binary_path, file_url)
        except:
            return binary_path
    else:
        response = await session.get(latest_release_url)
        json_data = response.json()
        file_url = get_file_url(json_data)

        await async_download_binary(session, binary_path, file_url)

    return binary_path


# sync
def sync_download_binary(session, output_path, file_url):
    with open(output_path, "wb") as output_file:
        response = session.get(
            url=file_url, content_callback=lambda chunk: output_file.write(chunk)
        )


def sync_get_binary_path(session):
    if binary_path is None:
        return None

    if not os.path.exists(funcaptcha_bin_folder_path) or not os.path.isdir(
        funcaptcha_bin_folder_path
    ):
        os.mkdir(funcaptcha_bin_folder_path)

    if os.path.isfile(binary_path):
        try:
            local_binary_hash = calculate_file_md5(binary_path)
            response = session.get(latest_release_url)
            json_data = response.json()

            for line in json_data["body"].splitlines():
                if line.startswith(current_os):
                    latest_binary_hash = line.split("=")[-1]
                    break

            if local_binary_hash != latest_binary_hash:
                file_url = get_file_url(json_data)

                sync_download_binary(session, binary_path, file_url)
        except:
            return binary_path
    else:
        response = session.get(latest_release_url)
        json_data = response.json()
        file_url = get_file_url(json_data)

        sync_download_binary(session, binary_path, file_url)

    return binary_path


def get_model_slug(chat):
    """Return the model slug attached to a chat mapping.

    Some conversations (especially older or audio-forward ones) may omit
    ``metadata.model_slug`` entirely, so we defensively fall back to the
    conversation-level defaults instead of raising ``KeyError``.
    """

    default_slug = chat.get("default_model_slug") or chat.get("default_model")

    for message in chat.get("mapping", {}).values():
        message_payload = message.get("message")
        if not message_payload:
            continue
        if message_payload.get("author", {}).get("role") != "assistant":
            continue
        metadata = message_payload.get("metadata") or {}
        slug = metadata.get("model_slug") or metadata.get("default_model_slug")
        if slug:
            return slug

    return default_slug


def get_session_token(config_path: str = "config.ini") -> str:
    """Retrieve the ChatGPT session token.

    The search order is ``config.ini`` in the current working directory and
    ``~/.chatgpt_session`` in the user's home directory.

    Args:
        config_path (str, optional): Path to the configuration file. Defaults
            to ``"config.ini"``.

    Returns:
        str: The session token string.

    Raises:
        TokenNotProvided: If no token is found in either location.
    """

    config_file = Path(config_path)
    parser = configparser.ConfigParser()

    if config_file.is_file():
        parser.read(config_file)
        token = parser.get("session", "token", fallback="").strip()
        if token and token != "YOUR_SESSION_TOKEN":
            return token

    session_file = Path.home() / ".chatgpt_session"
    if session_file.is_file():
        token = session_file.read_text(encoding="utf-8").strip()
        if token:
            return token

    raise TokenNotProvided()


def get_default_model(config_path: str = "config.ini") -> Optional[str]:
    """Return the default model slug for new conversations, if configured."""

    env_model = os.environ.get("RE_GPT_MODEL")
    if env_model:
        return env_model.strip() or None

    config_file = Path(config_path)
    parser = configparser.ConfigParser()

    if config_file.is_file():
        parser.read(config_file)
        model = parser.get("session", "model", fallback="").strip()
        if model and model != "YOUR_MODEL_SLUG":
            return model

    return None


def get_default_timezone(config_path: str = "config.ini") -> Optional[str]:
    """Return the default timezone label for payloads, if configured."""

    env_tz = os.environ.get("RE_GPT_TIMEZONE")
    if env_tz:
        return env_tz.strip() or None

    config_file = Path(config_path)
    parser = configparser.ConfigParser()

    if config_file.is_file():
        parser.read(config_file)
        tz_name = parser.get("session", "timezone", fallback="").strip()
        if tz_name and tz_name != "YOUR_TIMEZONE":
            return tz_name

    tzname = time.tzname[0] if time.tzname else ""
    return tzname or "UTC"


def get_default_timezone_offset_min(config_path: str = "config.ini") -> Optional[int]:
    """Return the default timezone offset in minutes for payloads."""

    env_offset = os.environ.get("RE_GPT_TIMEZONE_OFFSET_MIN")
    if env_offset:
        try:
            return int(env_offset.strip())
        except ValueError:
            pass

    config_file = Path(config_path)
    parser = configparser.ConfigParser()

    if config_file.is_file():
        parser.read(config_file)
        offset_value = parser.get("session", "timezone_offset_min", fallback="").strip()
        if offset_value and offset_value != "YOUR_TIMEZONE_OFFSET_MIN":
            try:
                return int(offset_value)
            except ValueError:
                pass

    if time.daylight and time.localtime().tm_isdst:
        return -time.altzone // 60
    return -time.timezone // 60


def get_default_user_agent(config_path: str = "config.ini") -> Optional[str]:
    """Return the default user agent string, if configured."""

    env_ua = os.environ.get("RE_GPT_USER_AGENT")
    if env_ua:
        return env_ua.strip() or None

    config_file = Path(config_path)
    parser = configparser.ConfigParser()

    if config_file.is_file():
        parser.read(config_file)
        ua = parser.get("session", "user_agent", fallback="").strip()
        if ua and ua != "YOUR_USER_AGENT":
            return ua

    return None
