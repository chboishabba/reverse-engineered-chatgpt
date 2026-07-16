#!/usr/bin/env python3
"""Bridge bounded Codex sessions and an authenticated ChatGPT DOM relay.

The bridge uses Codex's machine-readable output surfaces instead of scraping
terminal UI: JSONL provides the Codex session id and --output-last-message
provides the final response to hand to ChatGPT.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
RELAY = ROOT / "chatgpt_dom_relay.py"
DEFAULT_STATE_ROOT = ROOT / ".autonomous-orchestrator" / "codex-chatgpt-loops"
DEFAULT_MAPPING = ROOT / "codex_chatgpt_threads.json"


@dataclass
class CodexResult:
    session_id: str
    final_text: str
    stdout: str


def parse_thread_id(stdout: str) -> str:
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "thread.started" and event.get("thread_id"):
            return str(event["thread_id"])
    raise RuntimeError("Codex output did not contain a thread.started event.")


def read_final_message(path: Path) -> str:
    if not path.exists():
        raise RuntimeError(f"Codex did not create --output-last-message file: {path}")
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise RuntimeError(f"Codex final message was empty: {path}")
    return text


def run_codex(
    *,
    codex_bin: str,
    repo: Path,
    prompt: str,
    output_path: Path,
    session_id: str | None,
    model: str | None,
    sandbox: str,
    timeout: int,
) -> CodexResult:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    command = [codex_bin, "--yolo", "exec"]
    if session_id:
        command += [
            "resume",
            "--json",
            "--output-last-message",
            str(output_path),
        ]
    else:
        command += [
            "--json",
            "--output-last-message",
            str(output_path),
            "--sandbox",
            sandbox,
            "--cd",
            str(repo),
        ]
    if model:
        command += ["--model", model]
    if session_id:
        command.append(session_id)
    command.append(prompt)
    completed = subprocess.run(
        command,
        cwd=repo,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"Codex exited with {completed.returncode}:\n{completed.stdout[-4000:]}"
        )
    resolved_session_id = session_id or parse_thread_id(completed.stdout)
    return CodexResult(
        session_id=resolved_session_id,
        final_text=read_final_message(output_path),
        stdout=completed.stdout,
    )


def run_chatgpt(
    *,
    python_bin: str,
    thread: str,
    prompt_path: Path,
    headed: bool,
    timeout: int,
) -> dict[str, Any]:
    command = [python_bin, str(RELAY), "--json", "--prompt-file", str(prompt_path)]
    if headed:
        command.append("--headed")
    command += ["--timeout", str(timeout), thread]
    completed = subprocess.run(
        command,
        cwd=ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout + 30,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"ChatGPT relay exited with {completed.returncode}:\n"
            f"{completed.stderr[-4000:]}"
        )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"ChatGPT relay did not return JSON: {completed.stdout[-2000:]}") from exc
    response = str(payload.get("latest_assistant") or "").strip()
    if not response:
        raise RuntimeError("ChatGPT relay returned no assistant response.")
    return payload


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def load_pair(path: Path, alias: str) -> tuple[str, str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    pair = payload.get(alias.lower())
    if not isinstance(pair, dict):
        raise ValueError(f"Unknown Codex/ChatGPT pair alias: {alias}")
    codex_id = str(pair.get("codex_session_id") or "").strip()
    chatgpt_id = str(pair.get("chatgpt_thread_id") or "").strip()
    if not codex_id or not chatgpt_id:
        raise ValueError(f"Pair alias is missing an id: {alias}")
    return codex_id, chatgpt_id


def read_chatgpt_latest(
    *, python_bin: str, thread: str, headed: bool, timeout: int
) -> dict[str, Any]:
    command = [python_bin, str(RELAY), "--json", "--print-latest"]
    if headed:
        command.append("--headed")
    command += ["--timeout", str(timeout), thread]
    completed = subprocess.run(
        command,
        cwd=ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout + 30,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"ChatGPT latest-read exited with {completed.returncode}:\n"
            f"{completed.stderr[-4000:]}"
        )
    return json.loads(completed.stdout)


def build_feedback_prompt(chatgpt_response: str) -> str:
    return (
        "The supervised ChatGPT thread replied with the following text. "
        "Continue the current coding task using it as external feedback. "
        "Make concrete changes and validation progress; do not merely summarize it.\n\n"
        "--- ChatGPT response ---\n"
        f"{chatgpt_response}\n"
        "--- End ChatGPT response ---"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=ROOT.parent, help="Codex working repository.")
    parser.add_argument("--thread", help="ChatGPT conversation UUID or alias.")
    parser.add_argument("--pair", help="Alias from codex_chatgpt_threads.json.")
    parser.add_argument("--mapping-file", type=Path, default=DEFAULT_MAPPING)
    parser.add_argument("--codex-session-id", help="Existing Codex session to resume.")
    parser.add_argument(
        "--feedback-only",
        action="store_true",
        help="Read the latest ChatGPT reply and resume Codex without sending a new ChatGPT prompt.",
    )
    prompt = parser.add_mutually_exclusive_group(required=False)
    prompt.add_argument("--prompt", help="Initial Codex task prompt.")
    prompt.add_argument("--prompt-file", type=Path, help="Read the initial Codex prompt from a file.")
    parser.add_argument("--rounds", type=int, default=2, help="Maximum Codex/ChatGPT feedback rounds.")
    parser.add_argument("--loop-id", default=None, help="Durable loop id (default: generated UUID).")
    parser.add_argument("--codex-bin", default="codex")
    parser.add_argument("--python-bin", default="/home/c/Documents/code/ITIR-suite/.venv/bin/python")
    parser.add_argument("--model", default=None)
    parser.add_argument("--sandbox", default="workspace-write", choices=("read-only", "workspace-write", "danger-full-access"))
    parser.add_argument("--codex-timeout", type=int, default=1800)
    parser.add_argument("--chatgpt-timeout", type=int, default=900)
    parser.add_argument("--headless", action="store_true", help="Use headless relay; headed is the reliable default.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.rounds < 1:
        raise SystemExit("--rounds must be at least 1")
    if args.pair:
        mapped_codex_id, mapped_chatgpt_id = load_pair(args.mapping_file, args.pair)
        args.codex_session_id = args.codex_session_id or mapped_codex_id
        args.thread = args.thread or mapped_chatgpt_id
    if not args.thread:
        raise SystemExit("Provide --thread or --pair")
    if args.feedback_only and not args.codex_session_id:
        raise SystemExit("--feedback-only requires --codex-session-id or --pair")
    if not args.feedback_only and args.prompt is None and args.prompt_file is None:
        raise SystemExit("Provide --prompt or --prompt-file unless using --feedback-only")
    repo = args.repo.resolve()
    loop_id = args.loop_id or uuid.uuid4().hex[:12]
    loop_root = DEFAULT_STATE_ROOT / loop_id
    state_path = loop_root / "state.json"
    prompt_path = loop_root / "codex-current.txt"
    initial_prompt = (
        args.prompt
        if args.prompt is not None
        else args.prompt_file.read_text(encoding="utf-8")
        if args.prompt_file is not None
        else None
    )
    state: dict[str, Any] = {
        "schema_version": "codex_chatgpt_loop.v0_1",
        "loop_id": loop_id,
        "repo": str(repo),
        "chatgpt_thread": args.thread,
        "rounds_requested": args.rounds,
        "rounds": [],
        "status": "running",
    }
    write_json(state_path, state)

    session_id: str | None = args.codex_session_id
    codex_prompt = initial_prompt or ""
    try:
        if args.feedback_only:
            chatgpt_payload = read_chatgpt_latest(
                python_bin=args.python_bin,
                thread=args.thread,
                headed=not args.headless,
                timeout=args.chatgpt_timeout,
            )
            for round_number in range(1, args.rounds + 1):
                response = str(chatgpt_payload.get("latest_assistant") or "").strip()
                if not response:
                    raise RuntimeError("ChatGPT latest-read returned no assistant response.")
                output_path = loop_root / f"feedback-{round_number}.codex.txt"
                codex_result = run_codex(
                    codex_bin=args.codex_bin,
                    repo=repo,
                    prompt=build_feedback_prompt(response),
                    output_path=output_path,
                    session_id=session_id,
                    model=args.model,
                    sandbox=args.sandbox,
                    timeout=args.codex_timeout,
                )
                session_id = codex_result.session_id
                round_record: dict[str, Any] = {
                    "round": round_number,
                    "codex_session_id": session_id,
                    "codex_output": str(output_path),
                    "chatgpt_output": chatgpt_payload,
                }
                state["rounds"].append(round_record)
                state["codex_session_id"] = session_id
                state["last_completed_round"] = round_number
                write_json(state_path, state)
                if round_number < args.rounds:
                    prompt_path.write_text(codex_result.final_text, encoding="utf-8")
                    chatgpt_payload = run_chatgpt(
                        python_bin=args.python_bin,
                        thread=args.thread,
                        prompt_path=prompt_path,
                        headed=not args.headless,
                        timeout=args.chatgpt_timeout,
                    )
            state["status"] = "complete"
            write_json(state_path, state)
            print(json.dumps(state, indent=2, sort_keys=True))
            return 0
        for round_number in range(1, args.rounds + 1):
            codex_output_path = loop_root / f"round-{round_number}.codex.txt"
            codex_result = run_codex(
                codex_bin=args.codex_bin,
                repo=repo,
                prompt=codex_prompt,
                output_path=codex_output_path,
                session_id=session_id,
                model=args.model,
                sandbox=args.sandbox,
                timeout=args.codex_timeout,
            )
            session_id = codex_result.session_id
            prompt_path.write_text(codex_result.final_text, encoding="utf-8")
            chatgpt_payload = run_chatgpt(
                python_bin=args.python_bin,
                thread=args.thread,
                prompt_path=prompt_path,
                headed=not args.headless,
                timeout=args.chatgpt_timeout,
            )
            chatgpt_output_path = loop_root / f"round-{round_number}.chatgpt.json"
            write_json(chatgpt_output_path, chatgpt_payload)
            state["rounds"].append(
                {
                    "round": round_number,
                    "codex_output": str(codex_output_path),
                    "codex_session_id": session_id,
                    "chatgpt_output": str(chatgpt_output_path),
                }
            )
            state["codex_session_id"] = session_id
            state["last_completed_round"] = round_number
            write_json(state_path, state)
            codex_prompt = build_feedback_prompt(str(chatgpt_payload["latest_assistant"]))
        state["status"] = "complete"
        write_json(state_path, state)
        print(json.dumps(state, indent=2, sort_keys=True))
        return 0
    except Exception as exc:  # noqa: BLE001
        state["status"] = "failed"
        state["error"] = str(exc)
        write_json(state_path, state)
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
