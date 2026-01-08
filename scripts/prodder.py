#!/usr/bin/env python3
"""Helper to inspect conversation metadata and send a test message."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from re_gpt import SyncChatGPT
from re_gpt.errors import TokenNotProvided
from re_gpt.utils import get_model_slug, get_session_token, get_default_user_agent


def main() -> int:
    settings_path = Path(__file__).with_name("prodder.vars.json")
    message = "hello reddit"
    show_metadata = False
    offline_validate = False
    expected_payload_path = Path(__file__).with_name("expected_payload.json")
    generated_payload_path = Path(__file__).with_name("generated_payload.json")
    match_title = None
    match_titles = []
    match_user_content = None
    inject_message_fields = False
    send_to_matched_conversation = False
    user_agent = None
    report_message_times = False
    timezone_name = None
    playwright_refresh = False
    if settings_path.is_file():
        try:
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
            message = (settings.get("message") or message).strip() or message
            show_metadata = bool(settings.get("show_metadata", show_metadata))
            offline_validate = bool(settings.get("offline_validate", offline_validate))
            expected_payload_path = Path(
                settings.get("expected_payload_path", expected_payload_path)
            )
            generated_payload_path = Path(
                settings.get("generated_payload_path", generated_payload_path)
            )
            match_title = settings.get("match_title")
            match_titles = settings.get("match_titles", match_titles) or match_titles
            match_user_content = settings.get("match_user_content")
            inject_message_fields = bool(
                settings.get("inject_message_fields", inject_message_fields)
            )
            send_to_matched_conversation = bool(
                settings.get("send_to_matched_conversation", send_to_matched_conversation)
            )
            user_agent = settings.get("user_agent")
            report_message_times = bool(
                settings.get("report_message_times", report_message_times)
            )
            timezone_name = settings.get("timezone")
            playwright_refresh = bool(
                settings.get("playwright_refresh", playwright_refresh)
            )
        except Exception:
            print("Failed to read scripts/prodder.vars.json; using defaults.")

    try:
        token = get_session_token()
    except TokenNotProvided:
        print("No session token found in config.ini or ~/.chatgpt_session.")
        return 2

    user_agent = user_agent or get_default_user_agent()
    with SyncChatGPT(session_token=token, user_agent=user_agent) as chatgpt:
        if playwright_refresh:
            try:
                chatgpt._launch_browser_challenge_solver("https://chatgpt.com/")
            except Exception as exc:
                print(f"Playwright refresh failed: {exc}")
        page = chatgpt.list_conversations_page(offset=0, limit=1)
        items = page.get("items", [])
        if not items:
            print("No conversations found.")
            return 1
        conversation_id = items[0].get("id")
        if not conversation_id:
            print("Latest conversation missing id.")
            return 1

        conversation = chatgpt.get_conversation(conversation_id)
        chat = conversation.fetch_chat()
        detected_model = get_model_slug(chat)

        if show_metadata:
            print("conversation_mode:", chat.get("conversation_mode"))
            print("default_model_slug:", chat.get("default_model_slug"))
            print("default_model:", chat.get("default_model"))

        print(f"Detected model slug: {detected_model}")

        def pick_conversation_by_title(title_query: Optional[str]) -> Optional[str]:
            if not title_query:
                return None
            lowered = title_query.lower()
            offset = 0
            limit = 20
            for _ in range(10):
                page = chatgpt.list_conversations_page(offset=offset, limit=limit)
                items = page.get("items", [])
                for item in items:
                    title = (item.get("title") or "").lower()
                    if lowered in title:
                        return item.get("id")
                if len(items) < limit:
                    break
                offset += limit
            return None

        def find_user_message(chat_payload: dict) -> Optional[dict]:
            mapping = chat_payload.get("mapping", {}) or {}
            best = None
            best_time = -1
            for entry in mapping.values():
                message_payload = entry.get("message") if isinstance(entry, dict) else None
                if not message_payload:
                    continue
                author = message_payload.get("author", {}).get("role")
                if author != "user":
                    continue
                content = message_payload.get("content", {})
                parts = content.get("parts") or []
                text = "".join(part for part in parts if isinstance(part, str))
                if match_user_content and match_user_content not in text:
                    continue
                create_time = message_payload.get("create_time") or 0
                if create_time >= best_time:
                    best_time = create_time
                    best = message_payload
            return best

        def format_timestamp(value: Optional[float], tz_name: Optional[str]) -> str:
            if not value:
                return "unknown"
            tz = timezone.utc
            if tz_name:
                try:
                    tz = ZoneInfo(tz_name)
                except Exception:
                    tz = timezone.utc
            return datetime.fromtimestamp(value, tz=tz).isoformat()

        def report_messages(chat_payload: dict, title: str, tz_name: Optional[str]) -> None:
            mapping = chat_payload.get("mapping", {}) or {}
            entries = []
            for entry in mapping.values():
                message_payload = entry.get("message") if isinstance(entry, dict) else None
                if not message_payload:
                    continue
                if message_payload.get("author", {}).get("role") != "user":
                    continue
                content = message_payload.get("content", {})
                parts = content.get("parts") or []
                text = "".join(part for part in parts if isinstance(part, str))
                if match_user_content and match_user_content not in text:
                    continue
                create_time = message_payload.get("create_time") or 0
                entries.append((create_time, message_payload, text))
            entries.sort(key=lambda item: item[0], reverse=True)
            print(f"User messages for '{title}':")
            for create_time, message_payload, text in entries[:5]:
                print(
                    f"- {format_timestamp(create_time, tz_name)} "
                    f"(id={message_payload.get('id')}) "
                    f"{text[:80]}"
                )

        expected_payload = None
        if expected_payload_path.is_file():
            try:
                expected_payload = json.loads(
                    expected_payload_path.read_text(encoding="utf-8")
                )
            except Exception:
                expected_payload = None

        if expected_payload:
            chatgpt.client_contextual_info = expected_payload.get(
                "client_contextual_info", chatgpt.client_contextual_info
            )
            chatgpt.timezone = expected_payload.get("timezone", chatgpt.timezone)
            chatgpt.timezone_offset_min = expected_payload.get(
                "timezone_offset_min", chatgpt.timezone_offset_min
            )
            expected_mode = expected_payload.get("conversation_mode")
            if isinstance(expected_mode, dict):
                chatgpt.conversation_mode = expected_mode

        selected_title = match_title
        if match_titles:
            selected_title = match_titles[0]

        matched_conversation = None
        if selected_title:
            matched_id = pick_conversation_by_title(selected_title)
            if matched_id:
                matched_conversation = chatgpt.get_conversation(matched_id)
                matched_chat = matched_conversation.fetch_chat()
                matched_message = find_user_message(matched_chat)
            else:
                matched_message = None
        else:
            matched_message = find_user_message(chat)

        if report_message_times:
            titles = match_titles or ([match_title] if match_title else [])
            tz_name = timezone_name or chatgpt.timezone
            for title in titles:
                if not title:
                    continue
                convo_id = pick_conversation_by_title(title)
                if not convo_id:
                    print(f"No conversation found for title '{title}'.")
                    continue
                convo_chat = chatgpt.get_conversation(convo_id).fetch_chat()
                report_messages(convo_chat, title, tz_name)

        if send_to_matched_conversation and matched_conversation:
            target_conversation = matched_conversation
        else:
            target_conversation = chatgpt.create_new_conversation(model=detected_model)

        if offline_validate:
            payload = target_conversation.build_message_payload(message)
            if inject_message_fields and matched_message:
                payload_message = payload.get("messages", [{}])[0]
                payload_message["id"] = matched_message.get("id", payload_message.get("id"))
                payload_message["create_time"] = matched_message.get(
                    "create_time", payload_message.get("create_time")
                )
                payload_message["metadata"] = matched_message.get(
                    "metadata", payload_message.get("metadata")
                )
                payload_message["content"] = matched_message.get(
                    "content", payload_message.get("content")
                )
            generated_payload_path.write_text(
                json.dumps(payload, indent=2, sort_keys=True),
                encoding="utf-8",
            )
            print(f"Wrote generated payload to {generated_payload_path}")
            if expected_payload_path.is_file():
                expected = json.loads(expected_payload_path.read_text(encoding="utf-8"))
                expected_text = json.dumps(expected, indent=2, sort_keys=True)
                generated_text = json.dumps(payload, indent=2, sort_keys=True)
                if expected_text == generated_text:
                    print("Payload matches expected.")
                else:
                    print("Payload differs from expected.")
            else:
                print(f"No expected payload found at {expected_payload_path}")
            return 0

        response_text = ""
        for chunk in target_conversation.chat(message):
            content = chunk.get("content")
            if content:
                response_text += content
        print("Response:")
        print(response_text)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
