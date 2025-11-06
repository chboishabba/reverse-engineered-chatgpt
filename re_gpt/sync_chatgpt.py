import ctypes
import inspect
import time
import uuid
import websockets
from websockets.exceptions import ConnectionClosed
import json
import base64
import asyncio
import html
import re
from queue import Queue
from threading import Thread
from typing import Any, Callable, Generator, Optional

from curl_cffi.requests import Session

from .async_chatgpt import (
    BACKUP_ARKOSE_TOKEN_GENERATOR,
    CHATGPT_API,
    USER_AGENT,
    AsyncChatGPT,
    AsyncConversation,
    MODELS,
    WS_REGISTER_URL,
    CHATGPT_FREE_API,
)
from .errors import (
    BackendError,
    InvalidSessionToken,
    RetryError,
    TokenNotProvided,
    UnexpectedResponseError,
    InvalidModelName,
)
from .utils import sync_get_binary_path, get_model_slug
from .storage import AssetDownload


class SyncConversation(AsyncConversation):
    def __init__(self, chatgpt, conversation_id: Optional[str] = None, model=None, title=None):
        super().__init__(chatgpt, conversation_id, model, title)

    def fetch_chat(self) -> dict:
        """
        Fetches the chat of the conversation from the API.

        Returns:
            dict: The JSON response from the API containing the chat if the conversation_id is not none, else returns an empty dict.

        Raises:
            UnexpectedResponseError: If the response is not a valid JSON object or if the response json is not in the expected format
        """
        if not self.conversation_id:
            return {}

        url = CHATGPT_API.format(f"conversation/{self.conversation_id}")
        response = self.chatgpt.session.get(
            url=url, headers=self.chatgpt.build_request_headers()
        )

        error = None
        try:
            chat = response.json()
            self.parent_id = list(chat.get("mapping", {}))[-1]
            self.title = chat.get("title")
            model_slug = get_model_slug(chat)
            self.model = next(
                (
                    key
                    for key, value in MODELS.items()
                    if value["slug"] == model_slug
                ),
                None,
            )
            if self.model is None:
                self.model = model_slug
        except Exception as e:
            error = e
        if error is not None:
            raise UnexpectedResponseError(error, response.text)

        return chat

    def chat(self, user_input: str) -> Generator[dict, None, None]:
        """
        As the name implies, chat with ChatGPT.

        Args:
            user_input (str): The user's input message.

        Yields:
            dict: A dictionary representing assistant responses.

        Returns:
            Generator[dict, None]: A generator object that yields assistant responses.

        Raises:
            UnexpectedResponseError: If the response is not a valid JSON object or if the response json is not in the expected format
        """

        payload = self.build_message_payload(user_input)

        server_response = (
            ""  # To store what the server returned for debugging in case of an error
        )
        error = None
        try:
            full_message = None
            while True:
                response = self.send_message(payload=payload) if not self.chatgpt.websocket_mode else self.send_websocket_message(payload=payload)
                for chunk in response:
                    decoded_chunk = chunk.decode() if not self.chatgpt.websocket_mode else chunk

                    server_response += decoded_chunk
                    for line in decoded_chunk.splitlines():
                        if not line.startswith("data: "):
                            continue

                        raw_json_data = line[6:]
                        if not (decoded_json := self.decode_raw_json(raw_json_data)):
                            continue

                        if (
                            "message" in decoded_json
                            and decoded_json["message"]["author"]["role"] == "assistant"
                        ):
                            processed_response = self.filter_response(decoded_json)
                            if full_message:
                                prev_resp_len = len(
                                    full_message["message"]["content"]["parts"][0]
                                )
                                processed_response["content"] = processed_response[
                                    "content"
                                ][prev_resp_len::]

                            yield processed_response
                            full_message = decoded_json
                if not full_message:
                    raise UnexpectedResponseError(
                        "No message received", server_response
                    )
                self.conversation_id = full_message["conversation_id"]
                self.parent_id = full_message["message"]["id"]
                if (
                    full_message["message"]["metadata"]["finish_details"]["type"]
                    == "max_tokens"
                ):
                    payload = self.build_message_continuation_payload()
                else:
                    break
        except Exception as e:
            error = e

        # raising the error outside the 'except' block to prevent the 'During handling of the above exception, another exception occurred' error
        if error is not None:
            raise UnexpectedResponseError(error, server_response)

    def fetch_share_html(self, allow_browser_fallback: bool = True) -> str:
        """
        Retrieve the rendered conversation page from chatgpt.com.

        Args:
            allow_browser_fallback (bool): Launch a Playwright browser if
                Cloudflare challenges the request. Defaults to True.

        Returns:
            str: HTML content of the conversation page.
        """
        if not self.conversation_id:
            raise ValueError("conversation_id must be provided")

        return self.chatgpt.fetch_conversation_page(
            self.conversation_id,
            allow_browser_fallback=allow_browser_fallback,
        )

    def send_message(self, payload: dict) -> Generator[bytes, None, None]:
        """
        Send a message payload to the server and receive the response.

        Args:
            payload (dict): Payload containing message information.

        Yields:
            bytes: Chunk of data received as a response.
        """
        response_queue = Queue()

        def perform_request():
            def content_callback(chunk):
                response_queue.put(chunk)

            url = CHATGPT_API.format("conversation")
            headers = self.chatgpt.build_request_headers()
            # Add Chat Requirements Token
            chat_requriments_token = self.chatgpt.create_chat_requirements_token()
            if chat_requriments_token:
                headers["openai-sentinel-chat-requirements-token"] = chat_requriments_token

            response = self.chatgpt.session.post(
                url=url,
                headers=headers,
                json=payload,
                content_callback=content_callback,
            )
            response_queue.put(None)

        Thread(target=perform_request).start()

        while True:
            chunk = response_queue.get()
            if chunk is None:
                break
            yield chunk
    
    def send_websocket_message(self, payload: dict) -> Generator[str, None, None]:
        """
        Send a message payload via WebSocket and receive the response.

        Args:
            payload (dict): Payload containing message information.

        Yields:
            str: Chunk of data received as a response.
        """

        response_queue = Queue()
        websocket_request_id = None

        def perform_request():
            nonlocal websocket_request_id
            
            url = CHATGPT_API.format("conversation")
            headers = self.chatgpt.build_request_headers()
            # Add Chat Requirements Token
            chat_requriments_token = self.chatgpt.create_chat_requirements_token()
            if chat_requriments_token:
                headers["openai-sentinel-chat-requirements-token"] = chat_requriments_token

            response = (self.chatgpt.session.post(
                url=url,
                headers=headers,
                json=payload,
            )).json()

            websocket_request_id = response.get("websocket_request_id")
            
            if websocket_request_id is None:
                raise UnexpectedResponseError("WebSocket request ID not found in response", response)

            if websocket_request_id not in self.chatgpt.ws_conversation_map:
                self.chatgpt.ws_conversation_map[websocket_request_id] = response_queue
            
        Thread(target=perform_request).start()

        while True:
            chunk = response_queue.get()
            if chunk is None:
                break
            yield chunk

        del self.chatgpt.ws_conversation_map[websocket_request_id]

    def build_message_payload(self, user_input: str) -> dict:
        """
        Build a payload for sending a user message.

        Returns:
            dict: Payload containing message information.
        """
        if self.conversation_id and (self.parent_id is None or self.model is None):
            self.fetch_chat()  # it will automatically fetch the chat and set the parent id

        if self.model not in MODELS:
            raise InvalidModelName(self.model, MODELS)

        payload = {
            "conversation_mode": {"conversation_mode": {"kind": "primary_assistant"}},
            "conversation_id": self.conversation_id,
            "action": "next",
            "arkose_token": self.arkose_token_generator()
            if self.chatgpt.generate_arkose_token
            or MODELS[self.model]["needs_arkose_token"]
            else None,
            "force_paragen": False,
            "history_and_training_disabled": False,
            "messages": [
                {
                    "author": {"role": "user"},
                    "content": {"content_type": "text", "parts": [user_input]},
                    "id": str(uuid.uuid4()),
                    "metadata": {},
                }
            ],
            "model": MODELS[self.model]["slug"],
            "parent_message_id": str(uuid.uuid4())
            if not self.parent_id
            else self.parent_id,
            "websocket_request_id": str(uuid.uuid4())
            if self.chatgpt.websocket_mode
            else None,
        }

        return payload

    def build_message_continuation_payload(self) -> dict:
        """
        Build a payload for continuing ChatGPT's cut off response.

        Returns:
            dict: Payload containing message information for continuation.
        """
        if self.model not in MODELS:
            raise InvalidModelName(self.model, MODELS)

        payload = {
            "conversation_mode": {"conversation_mode": {"kind": "primary_assistant"}},
            "action": "continue",
            "arkose_token": self.arkose_token_generator()
            if self.chatgpt.generate_arkose_token
            or MODELS[self.model]["needs_arkose_token"]
            else None,
            "conversation_id": self.conversation_id,
            "force_paragen": False,
            "history_and_training_disabled": False,
            "model": MODELS[self.model]["slug"],
            "parent_message_id": self.parent_id,
            "timezone_offset_min": -300,
        }

        return payload

    def arkose_token_generator(self) -> str:
        """
        Generate an Arkose token.

        Returns:
            str: Arkose token.
        """
        if not self.chatgpt.tried_downloading_binary:
            self.chatgpt.binary_path = sync_get_binary_path(self.chatgpt.session)

            if self.chatgpt.binary_path:
                self.chatgpt.arkose = ctypes.CDLL(self.chatgpt.binary_path)
                self.chatgpt.arkose.GetToken.restype = ctypes.c_char_p

            self.chatgpt.tried_downloading_binary = True

        if self.chatgpt.binary_path:
            try:
                result = self.chatgpt.arkose.GetToken()
                return ctypes.string_at(result).decode("utf-8")
            except:
                pass

        for _ in range(5):
            response = self.chatgpt.session.get(BACKUP_ARKOSE_TOKEN_GENERATOR)
            if response.text == "null":
                raise BackendError(error_code=505)
            try:
                return response.json()["token"]
            except:
                time.sleep(0.7)

        raise RetryError(website=BACKUP_ARKOSE_TOKEN_GENERATOR)

    def delete(self) -> None:
        """
        Deletes the conversation.
        """
        if self.conversation_id:
            self.chatgpt.delete_conversation(self.conversation_id)

            self.conversation_id = None
            self.parent_id = None


class SyncChatGPT(AsyncChatGPT):
    def __init__(
        self,
        proxies: Optional[dict] = None,
        session_token: Optional[str] = None,
        exit_callback_function: Optional[Callable] = None,
        auth_token: Optional[str] = None,
        websocket_mode: Optional[bool] = False,
        browser_challenge_solver: Optional[str] = "firefox",
    ):
        """
        Initializes an instance of the class.

        Args:
            proxies (Optional[dict]): A dictionary of proxy settings. Defaults to None.
            session_token (Optional[str]): A session token. Defaults to None.
            exit_callback_function (Optional[callable]): A function to be called on exit. Defaults to None.
            auth_token (Optional[str]): An authentication token. Defaults to None.
            websocket_mode (Optional[bool]): Toggle whether to use WebSocket for chat. Defaults to False.
            browser_challenge_solver (Optional[str]): Browser engine to use when solving
                interactive Cloudflare challenges (``\"firefox\"`` by default). Set to
                ``None`` to disable the Playwright fallback.
        """
        super().__init__(
            proxies=proxies,
            session_token=session_token,
            exit_callback_function=exit_callback_function,
            auth_token=auth_token,
            websocket_mode=websocket_mode,
        )

        self.browser_challenge_solver = browser_challenge_solver
        self._frontend_cookies: dict[str, str] = {}
        self._conversation_page_cache: dict[str, str] = {}

        self.stop_websocket_flag = False
        self.stop_websocket = None

    def __enter__(self):
        self.session = Session(
            impersonate="chrome110", timeout=99999, proxies=self.proxies
        )
        self._frontend_cookies = {}
        if self.session_token:
            self._frontend_cookies["__Secure-next-auth.session-token"] = (
                self.session_token
            )
            try:
                self.session.cookies.set(
                    "__Secure-next-auth.session-token",
                    self.session_token,
                    domain="chatgpt.com",
                    path="/",
                )
            except Exception:
                self.session.cookies.set(
                    "__Secure-next-auth.session-token", self.session_token
                )

        if self.generate_arkose_token:
            self.binary_path = sync_get_binary_path(self.session)

            if self.binary_path:
                self.arkose = ctypes.CDLL(self.binary_path)
                self.arkose.GetToken.restype = ctypes.c_char_p

            self.tried_downloading_binary = True

        if not self.auth_token:
            if not self.free_mode:
                if self.session_token is None:
                    raise TokenNotProvided
                self.auth_token = self.fetch_auth_token()
            else:
                self.auth_cookie = self.fetch_free_mode_cookies()
            
        # automaticly check the status of websocket_mode
        if not self.websocket_mode:
            self.websocket_mode = self.check_websocket_availability()
            
        if self.websocket_mode:
            def run_websocket():
                asyncio.run(self.ensure_websocket())
            self.ws_loop = Thread(target=run_websocket)
            self.ws_loop.start()

        return self

    def __exit__(self, *args):
        try:
            if self.exit_callback_function and callable(self.exit_callback_function):
                if not inspect.iscoroutinefunction(self.exit_callback_function):
                    self.exit_callback_function(self)
        finally:
            self.session.close()

        if self.websocket_mode:
            self.stop_websocket_flag = True
            self.ws_loop.join()

    def get_conversation(self, conversation_id: str, title: Optional[str] = None) -> SyncConversation:
        """
        Makes an instance of class Conversation and return it.

        Args:
            conversation_id (str): The ID of the conversation to fetch.
            title (Optional[str]): The title of the conversation.

        Returns:
            Conversation: Conversation object.
        """

        return SyncConversation(self, conversation_id, title=title)

    def create_new_conversation(
        self, model: Optional[str] = "gpt-3.5", title: Optional[str] = None
    ) -> SyncConversation:
        if model not in MODELS:
            raise InvalidModelName(model, MODELS)
        return SyncConversation(self, model=model, title=title)

    def delete_conversation(self, conversation_id: str) -> dict:
        """
        Delete a conversation.

        Args:
            conversation_id (str): Unique identifier for the conversation.

        Returns:
            dict: Server response json.
        """
        url = CHATGPT_API.format(f"conversation/{conversation_id}")
        response = self.session.patch(
            url=url, headers=self.build_request_headers(), json={"is_visible": False}
        )

        return response.json()

    def resolve_asset_pointer(self, asset_pointer: str, conversation_id: Optional[str] = None) -> str:
        """
        Resolve an asset pointer into a downloadable URL.

        Args:
            asset_pointer (str): The asset pointer returned by the ChatGPT API.

        Returns:
            str: A signed download URL that can be used to fetch the asset.
        """
        if not asset_pointer:
            raise ValueError("asset_pointer must be provided")

        pointer = asset_pointer.strip()
        if not pointer:
            raise ValueError("asset_pointer must be provided")

        if pointer.startswith(("http://", "https://")):
            return pointer

        url = CHATGPT_API.format("asset/get")
        headers = dict(self.build_request_headers())
        headers["Accept"] = "application/json"

        def _register_candidate(value: str, store: list[str]) -> None:
            candidate = value.strip()
            if candidate and candidate not in store:
                store.append(candidate)

        candidates: list[str] = []
        _register_candidate(pointer, candidates)

        scheme = ""
        remainder = pointer
        if "://" in pointer:
            scheme, remainder = pointer.split("://", 1)
            scheme = scheme.strip().lower()
            remainder = remainder.strip()
        else:
            remainder = remainder.strip()

        if not scheme and remainder:
            _register_candidate(f"file-service://{remainder}", candidates)
        elif scheme in {"file", "fileservice"} and remainder:
            _register_candidate(f"file-service://{remainder}", candidates)
        elif scheme == "file-service" and remainder:
            _register_candidate(f"file-service://{remainder}", candidates)
        elif scheme == "sediment" and remainder:
            _register_candidate(f"file-service://{remainder}", candidates)
            if remainder.startswith("file_"):
                _register_candidate(f"file-service://{remainder.replace('file_', 'file-', 1)}", candidates)

        attempt_errors: list[str] = []
        for candidate in candidates:
            response = self.session.post(
                url=url,
                headers=headers,
                json={"asset_pointer": candidate},
            )
            if response.status_code != 200:
                attempt_errors.append(
                    f"{candidate} -> {response.status_code}: {getattr(response, 'text', '')}"
                )
                continue

            try:
                payload = response.json()
            except Exception as exc:  # noqa: BLE001 - bubble unexpected payload issues.
                attempt_errors.append(f"{candidate} -> invalid JSON: {exc}")
                continue

            for key in ("download_url", "url", "signed_url", "downloadUrl", "content_url"):
                download_url = payload.get(key)
                if download_url:
                    return download_url

            attempt_errors.append(f"{candidate} -> missing download URL")

        def _iter_file_ids(pointer_value: str) -> list[str]:
            if "://" in pointer_value:
                _, raw = pointer_value.split("://", 1)
            else:
                raw = pointer_value
            raw = raw.strip().strip("/")
            if not raw:
                return []
            options: list[str] = []

            def _register(value: str) -> None:
                cleaned = value.strip()
                if cleaned and cleaned not in options:
                    options.append(cleaned)

            _register(raw)
            if raw.startswith("file_"):
                _register(raw.replace("file_", "file-", 1))
            if raw.startswith("file-"):
                _register(raw.replace("file-", "file_", 1))
            return options

        def _resolve_via_files_api(pointer_value: str) -> Optional[str]:
            file_ids = _iter_file_ids(pointer_value)
            if not file_ids:
                return None

            for file_id in file_ids:
                files_url = f"https://chatgpt.com/backend-api/files/{file_id}/download"
                files_headers = dict(self.build_request_headers())
                files_headers.pop("Content-Type", None)

                response = self.session.get(files_url, headers=files_headers)
                if response.status_code != 200:
                    attempt_errors.append(
                        f"{files_url} -> {response.status_code}: {getattr(response, 'text', '')}"
                    )
                    continue

                try:
                    payload = response.json()
                except Exception as exc:
                    attempt_errors.append(f"{files_url} -> invalid JSON: {exc}")
                    continue

                for key in ("download_url", "url", "signed_url", "downloadUrl", "content_url"):
                    download_url = payload.get(key)
                    if download_url:
                        return download_url

                attempt_errors.append(f"{files_url} -> missing download URL")

            return None

        for candidate in candidates:
            download_url = _resolve_via_files_api(candidate)
            if download_url:
                return download_url

        def _resolve_via_conversation_page(conv_id: str, pointer_values: list[str]) -> Optional[str]:
            try:
                cached = self._conversation_page_cache.get(conv_id)
                if cached is None:
                    cached = self.fetch_conversation_page(conv_id)
                    self._conversation_page_cache[conv_id] = cached
            except Exception as exc:
                attempt_errors.append(f"conversation page {conv_id} -> {exc}")
                return None

            identifiers: list[str] = []
            for pointer_value in pointer_values:
                for candidate_id in _iter_file_ids(pointer_value):
                    if candidate_id not in identifiers:
                        identifiers.append(candidate_id)

            if not identifiers:
                return None

            def _search(source_html: str) -> Optional[str]:
                if not source_html:
                    return None
                decoded = html.unescape(source_html)
                pattern = re.compile(r"https://chatgpt\.com/backend-api/[^\s\"'>]+")
                for match in pattern.finditer(decoded):
                    url_match = match.group(0)
                    if any(identifier in url_match for identifier in identifiers):
                        return url_match
                return None

            url_match = _search(cached)
            if url_match:
                return url_match

            if self.browser_challenge_solver:
                try:
                    rendered = self._render_frontend_page(f"https://chatgpt.com/c/{conv_id}")
                except Exception as exc:
                    attempt_errors.append(f"rendered conversation page {conv_id} -> {exc}")
                else:
                    self._conversation_page_cache[conv_id] = rendered
                    url_match = _search(rendered)
                    if url_match:
                        return url_match

            return None

        if conversation_id:
            download_url = _resolve_via_conversation_page(conversation_id, candidates)
            if download_url:
                return download_url

        raise UnexpectedResponseError(
            f"Asset pointer {asset_pointer} did not include a download URL",
            "; ".join(error for error in attempt_errors if error) or "",
        )

    def download_asset(self, asset_pointer: str, conversation_id: Optional[str] = None) -> AssetDownload:
        """
        Download the binary payload for an asset pointer.

        Args:
            asset_pointer (str): Asset pointer returned by the ChatGPT API.

        Returns:
            AssetDownload: Binary payload and optional content type.
        """
        download_url = self.resolve_asset_pointer(asset_pointer, conversation_id=conversation_id)

        response = self.session.get(
            download_url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "*/*",
            },
        )
        if response.status_code != 200:
            raise UnexpectedResponseError(
                f"Failed to download asset for {asset_pointer}",
                response.text,
            )

        content_type = (
            response.headers.get("Content-Type")
            or response.headers.get("content-type")
            or None
        )
        return AssetDownload(content=response.content, content_type=content_type)

    def fetch_auth_token(self) -> str:
        """
        Fetch the authentication token for the session.

        Raises:
            InvalidSessionToken: If the session token is invalid.

        Returns: authentication token.
        """
        url = "https://chatgpt.com/api/auth/session"
        cookies = {"__Secure-next-auth.session-token": self.session_token}

        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.5",
            "Alt-Used": "chatgpt.com",
            "Connection": "keep-alive",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "Sec-GPC": "1",
            "Cookie": "; ".join(
                [
                    f"{cookie_key}={cookie_value}"
                    for cookie_key, cookie_value in cookies.items()
                ]
            ),
        }

        response = self.session.get(url=url, headers=headers)
        try:
            response.raise_for_status()
        except Exception as e:
            raise InvalidSessionToken from e
        response_json = response.json()

        access_token = response_json.get("accessToken")
        if access_token:
            return access_token

        raise UnexpectedResponseError(
            "accessToken missing in auth response", response.text
        )

    def set_custom_instructions(
        self,
        about_user: Optional[str] = "",
        about_model: Optional[str] = "",
        enable_for_new_chats: Optional[bool] = True,
    ) -> dict:
        """
        Set cuteom instructions for ChatGPT.

        Args:
            about_user (str): What would you like ChatGPT to know about you to provide better responses?
            about_model (str): How would you like ChatGPT to respond?
            enable_for_new_chats (bool): Enable for new chats.
        Returns:
            dict: Server response json.
        """
        data = {
            "about_user_message": about_user,
            "about_model_message": about_model,
            "enabled": enable_for_new_chats,
        }
        url = CHATGPT_API.format("user_system_messages")
        response = self.session.post(
            url=url, headers=self.build_request_headers(), json=data
        )

        return response.json()

    def list_conversations_page(
        self, offset: Optional[int] = 0, limit: Optional[int] = 28
    ) -> dict:
        """Retrieve a single page of conversations.

        Args:
            offset (Optional[int]): Starting index of the page.
            limit (Optional[int]): Maximum number of conversations to return.

        Returns:
            dict: JSON response containing one page of conversations.
        """
        params = {
            "offset": offset,
            "limit": limit,
            "order": "updated",
        }
        url = CHATGPT_API.format("conversations")
        response = self.session.get(
            url=url, params=params, headers=self.build_request_headers()
        )

        return response.json()

    def list_all_conversations(self, limit: int = 28) -> list[dict]:
        """Retrieve metadata for all conversations.

        Args:
            limit: Maximum number of conversations to fetch per request.

        Returns:
            List of dictionaries containing ``id``, ``title`` and
            ``last_updated`` for each conversation.
        """

        conversations: list[dict] = []
        offset = 0

        while True:
            data = self.list_conversations_page(offset=offset, limit=limit)
            items = data.get("items", [])

            for item in items:
                conversations.append(
                    {
                        "id": item.get("id"),
                        "title": item.get("title"),
                        "last_updated": item.get("update_time"),
                    }
                )

            if len(items) < limit:
                break

            offset += limit

        return conversations
    
    def check_websocket_availability(self) -> bool:
        """
        Check if WebSocket is available.

        Returns:
            bool: True if WebSocket is available, otherwise False.
        """
        url = CHATGPT_API.format("accounts/check/v4-2023-04-27")

        headers = self.build_request_headers()

        try:
            raw_response = self.session.get(url=url, headers=headers)
            raw_response.raise_for_status()
        except Exception:
            return False

        try:
            response = raw_response.json()
        except Exception:
            return False

        if "account_ordering" in response and "accounts" in response:
            account_id = response["account_ordering"][0]
            account_data = response["accounts"].get(account_id, {})
            features = account_data.get("features", [])
            return "shared_websocket" in features

        return False
    
    async def ensure_websocket(self):
        ws_url_rsp = self.session.post(WS_REGISTER_URL, headers=self.build_request_headers()).json()
        ws_url = ws_url_rsp['wss_url']
        access_token = self.extract_access_token(ws_url)
        asyncio.create_task(self.ensure_close_websocket())
        await self.listen_to_websocket(ws_url, access_token)
        
    async def ensure_close_websocket(self):
        while True:
            if self.stop_websocket_flag:
                break
            await asyncio.sleep(1)
        await self.stop_websocket()

    async def listen_to_websocket(self, ws_url: str, access_token: str):
        headers = {'Authorization': f'Bearer {access_token}'}
        async with websockets.connect(ws_url, extra_headers=headers) as websocket:
            async def stop_websocket():
                await websocket.close()
            self.stop_websocket = stop_websocket

            while True:
                message = None
                try:
                    message = await websocket.recv()
                except ConnectionClosed:
                    break
                message_data = json.loads(message)
                body_encoded = message_data.get("body", "")
                ws_id = message_data.get("websocket_request_id", "")
                decoded_body = base64.b64decode(body_encoded).decode('utf-8')
                response_queue = self.ws_conversation_map.get(ws_id)
                if response_queue is None:
                    continue
                response_queue.put_nowait(decoded_body)
                if '[DONE]' in decoded_body or '[ERROR]' in decoded_body:
                    response_queue.put(None)
                    continue

    def create_chat_requirements_token(self):
        """
        Get a chat requirements token from chatgpt server

        Returns:
            str: chat requirements token
        """
        url = CHATGPT_API.format("sentinel/chat-requirements")
        
        if self.free_mode:
            url = CHATGPT_FREE_API.format("sentinel/chat-requirements")
        
        response = self.session.post(
            url=url, headers=self.build_request_headers()
        )
        body = response.json()
        token = body.get("token", None)
        return token

    def fetch_free_mode_cookies(self):
        home_url = "https://chatgpt.com/"
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "*/*",
            "Accept-Language": "en-US,en;q=0.5",
            "Alt-Used": "chatgpt.com",
            "Connection": "keep-alive",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "Sec-GPC": "1",
        }

        response = self._perform_frontend_get(
            home_url,
            headers=headers,
            purpose="ChatGPT home page",
            allow_browser_fallback=True,
        )
        response_cookies = response.cookies
        self.devive_id = response_cookies.get("oai-did")

        return response_cookies

    def _build_frontend_page_headers(self) -> dict[str, str]:
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Connection": "keep-alive",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
        }
        return headers

    def _merge_cookie_container(self, cookies: Any) -> None:
        if not cookies:
            return

        jar = None
        if hasattr(cookies, "jar"):
            jar = cookies.jar
        elif isinstance(cookies, dict):
            jar = [
                type("CookieTuple", (), {"name": key, "value": value, "domain": "chatgpt.com", "path": "/"})
                for key, value in cookies.items()
            ]
        elif isinstance(cookies, list):
            jar = cookies

        if jar is None:
            return

        for cookie in jar:
            if isinstance(cookie, dict):
                name = cookie.get("name")
                value = cookie.get("value")
                domain = cookie.get("domain", "") or ""
                path = cookie.get("path", "/") or "/"
            else:
                name = getattr(cookie, "name", None)
                value = getattr(cookie, "value", None)
                domain = getattr(cookie, "domain", "") or ""
                path = getattr(cookie, "path", "/") or "/"
            if not name or value is None:
                continue
            normalized_domain = domain.lstrip(".") or "chatgpt.com"
            if not normalized_domain.endswith("chatgpt.com"):
                continue
            self._frontend_cookies[name] = value
            try:
                self.session.cookies.set(name, value, domain=normalized_domain, path=path)
            except Exception:
                self.session.cookies.set(name, value)

    @staticmethod
    def _looks_like_cloudflare_challenge(response) -> bool:
        if response is None:
            return False
        if response.status_code in {403, 503} or "cf-mitigated" in response.headers:
            try:
                snippet = response.text
            except Exception:
                snippet = ""
            snippet = (snippet or "").lower()
            if "just a moment" in snippet or "__cf_chl_" in snippet or "cloudflare" in snippet:
                return True
        return False

    def _launch_browser_challenge_solver(self, url: str) -> None:
        if not self.browser_challenge_solver:
            raise UnexpectedResponseError(
                "Cloudflare challenge encountered, but browser fallback is disabled.",
                "Set 'browser_challenge_solver' to a Playwright engine (e.g. 'firefox') to enable it.",
            )

        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise UnexpectedResponseError(
                "Cloudflare challenge encountered, but Playwright is not installed.",
                "Install with `pip install playwright` and run `playwright install firefox`.",
            ) from exc

        print(
            f"Cloudflare challenged the request for {url}.\n"
            "Launching Playwright so you can solve it (a browser window should appear). "
            "Complete the verification, confirm the target page loads, then return here "
            "and press Enter to continue."
        )

        with sync_playwright() as playwright:
            solver = (self.browser_challenge_solver or "firefox").lower()
            if solver == "chromium":
                browser = playwright.chromium.launch(headless=False)
            elif solver == "webkit":
                browser = playwright.webkit.launch(headless=False)
            else:
                browser = playwright.firefox.launch(headless=False)

            context = browser.new_context(user_agent=USER_AGENT)

            if self._frontend_cookies:
                cookie_payload = []
                for name, value in self._frontend_cookies.items():
                    cookie_payload.append(
                        {
                            "name": name,
                            "value": value,
                            "domain": "chatgpt.com",
                            "path": "/",
                            "secure": True,
                        }
                    )
                try:
                    context.add_cookies(cookie_payload)
                except Exception:
                    # If a cookie add fails, fall back to launching without them.
                    pass

            page = context.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
            except Exception:
                # Allow manual navigation if automatic load times out.
                pass

            input("Press Enter after the page finishes loading and the challenge is cleared...")

            cookies = context.cookies()
            browser.close()

        self._merge_cookie_container(cookies)

    def _render_frontend_page(self, url: str) -> str:
        if not self.browser_challenge_solver:
            raise UnexpectedResponseError(
                "Playwright rendering requested, but browser fallback is disabled.",
                "Set 'browser_challenge_solver' to an engine (e.g. 'firefox') to enable it.",
            )

        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise UnexpectedResponseError(
                "Playwright rendering requested, but Playwright is not installed.",
                "Install with `pip install playwright` and run `playwright install firefox`.",
            ) from exc

        solver = (self.browser_challenge_solver or "firefox").lower()
        with sync_playwright() as playwright:
            if solver == "chromium":
                browser = playwright.chromium.launch(headless=True)
            elif solver == "webkit":
                browser = playwright.webkit.launch(headless=True)
            else:
                browser = playwright.firefox.launch(headless=True)

            context = browser.new_context(user_agent=USER_AGENT)

            if self._frontend_cookies:
                cookie_payload = []
                for name, value in self._frontend_cookies.items():
                    cookie_payload.append(
                        {
                            "name": name,
                            "value": value,
                            "domain": "chatgpt.com",
                            "path": "/",
                            "secure": True,
                        }
                    )
                try:
                    context.add_cookies(cookie_payload)
                except Exception:
                    pass

            page = context.new_page()
            page.goto(url, wait_until="networkidle", timeout=60000)
            html_content = page.content()
            cookies = context.cookies()
            browser.close()

        self._merge_cookie_container(cookies)
        return html_content

    def _perform_frontend_get(
        self,
        url: str,
        headers: dict[str, str],
        purpose: str,
        allow_browser_fallback: bool = True,
    ):
        response = self.session.get(
            url=url,
            headers=headers,
            cookies=dict(self._frontend_cookies) or None,
        )
        self._merge_cookie_container(response.cookies)

        if self._looks_like_cloudflare_challenge(response) and allow_browser_fallback:
            self._launch_browser_challenge_solver(url)
            response = self.session.get(
                url=url,
                headers=headers,
                cookies=dict(self._frontend_cookies) or None,
            )
            self._merge_cookie_container(response.cookies)

        if self._looks_like_cloudflare_challenge(response):
            raise UnexpectedResponseError(
                f"Unable to retrieve {purpose} due to Cloudflare blocking the request.",
                response.text if hasattr(response, "text") else "",
            )

        return response

    def fetch_conversation_page(
        self,
        conversation_id: str,
        allow_browser_fallback: bool = True,
    ) -> str:
        if not conversation_id:
            raise ValueError("conversation_id must be provided")

        url = f"https://chatgpt.com/c/{conversation_id}"
        headers = self._build_frontend_page_headers()
        response = self._perform_frontend_get(
            url,
            headers=headers,
            purpose=f"conversation page {conversation_id}",
            allow_browser_fallback=allow_browser_fallback,
        )
        return response.text
