<div align="center">
  <a href="https://github.com/Zai-Kun/reverse-engineered-chatgpt">  </a>

<h1 align="center">Reverse Engineered <a href="https://openai.com/blog/chatgpt">ChatGPT</a> API</h1>

  <p align="center">
    Use OpenAI ChatGPT in your Python code without an API key

[![Stargazers][stars-badge]][stars-url]
[![Forks][forks-badge]][forks-url]
[![Discussions][discussions-badge]][discussions-url]
[![Issues][issues-badge]][issues-url]
[![MIT License][license-badge]][license-url]

  English | [简体中文](./docs/zh-README.md)

  </p>
    <p align="center">
    <a href="https://github.com/Zai-Kun/reverse-engineered-chatgpt"></a>
    <a href="https://github.com/Zai-Kun/reverse-engineered-chatgpt/issues">Report Bug</a>
    |
    <a href="https://github.com/Zai-Kun/reverse-engineered-chatgpt/discussions">Request Feature</a>
  </p>
</div>

<!-- TABLE OF CONTENTS -->
<details>
  <summary>Table of Contents</summary>
  <ol>
    <li>
      <a href="#about-the-project">About The Project</a>
      <ul>
        <li><a href="#inspiration">Inspiration</a></li>
        <li><a href="#how-it-works">How it works</a></li>
        <li><a href="#built-using">Built using</a></li>
      </ul>
    </li>
    <li>
      <a href="#getting-started">Getting Started</a>
      <ul>
        <li><a href="#prerequisites">Prerequisites</a></li>
        <li><a href="#installation">Installation</a></li>
        <li><a href="#configuration">Configuration</a></li>
        <li><a href="#obtaining-session-token">Obtaining Session Token</a></li>
      </ul>
    </li>
    <li><a href="#usage">Usage</a>
        <ul>
        <li><a href="#basic-example">Example Usage</a></li>
        <li><a href="#resume-a-previous-conversation-interactively">Resume a previous conversation interactively</a></li>
        </ul>
    </li>
    <li><a href="#roadmap">Roadmap</a></li>
    <li><a href="#contributing">Contributing</a></li>
    <li><a href="#license">License</a></li>
    <li><a href="#contact">Contact</a></li>
    <li><a href="#acknowledgments">Acknowledgments</a></li>
  </ol>
</details>

## About The Project

This project can be used to integrate OpenAI's ChatGPT services into your python code. You can use this project to prompt ChatGPT for responses directly from python, without using an official API key.

This can be useful if you want to use ChatGPT API without a [ChatGPT Plus](https://openai.com/blog/chatgpt-plus) account.

### Inspiration

ChatGPT has an official API which can be used to interface your Python code to it, but it needs to be used with an API key. This API key can only be obtained if you have a [ChatGPT Plus](https://openai.com/blog/chatgpt-plus) account, which requires $20/month (as of 05/11/2023). But you can use ChatGPT for free, using the [ChatGPT web interface](https://chatgpt.com/). This project aims to interface your code to ChatGPT web version so you can use ChatGPT in your Python code without using an API key.

### How it works

[ChatGPT](https://chatgpt.com/) web interface's requests have been reverse engineered, and directly integrated into Python requests. Hence, any requests made using this script is a simulated as a request made by a user directly on the website. Hence, it is free and needs no API key.

### Built Using

- [![Python][python-badge]][python-url]

## Getting Started

### Prerequisites

- Python >= 3.9

> [!TIP]
> Some endpoints (e.g., the shared chat pages at `https://chatgpt.com/c/...`) are
> occasionally guarded by Cloudflare challenges. When that happens the CLI can
> launch a temporary Playwright browser so you can clear the check and reuse the
> resulting cookies. Install the optional browser helper with:
>
> ```sh
> pip install re-gpt[browser]
> playwright install firefox
> ```

### Installation

```sh
pip install re-gpt
```

For local editable installs (including restricted-network/offline fallbacks and
CLI token setup), see `docs/source-install.md`.

### Run the interactive CLI locally

On macOS or Linux you can bootstrap a local development environment and start the
interactive CLI with the provided helper script:

```sh
./scripts/run_app.sh
```

The script creates (or reuses) a `.venv` virtual environment in the project
root, upgrades `pip`, installs the package in editable mode, and then launches
the interactive CLI module.

> [!NOTE]
> The script relies on Bash and POSIX-style paths. Windows users can run it from
> Windows Subsystem for Linux (WSL) or follow the same steps manually using
> PowerShell (`python -m venv .venv`, `.venv\\Scripts\\Activate.ps1`, then
> `pip install --upgrade pip` and `pip install -e .`) before starting the CLI
> with `python -m re_gpt.cli`.

### Running Tests

To run the test suite, use the `run_tests.sh` script:

```sh
./scripts/run_tests.sh
```

This script will ensure the test dependencies are installed in the virtual
environment and then execute the tests. The asset download tests use an inline
HTML fixture, so no extra files are required in the repository root. The script
also installs the Playwright browser dependency, which requires network access
during setup.
### Configuration

Copy `config.example.ini` to `config.ini` and update the placeholder values or
store your token in a `~/.chatgpt_session` file:

```sh
cp config.example.ini config.ini
```

Edit `config.ini` and replace `token` with your ChatGPT session token. Set
`conversation_id` if you want to resume a specific chat, and optionally set
`model` to the model slug you want for new conversations (for example, a 5.x-era
slug from your account). You can also set `timezone` and `timezone_offset_min`
to match your environment, and optionally set `user_agent` if you need to
mirror a specific browser fingerprint. If a `config.ini` is not found,
`get_session_token` will look for a token in `~/.chatgpt_session` instead.
The CLI will try to detect a model slug from your most recent conversation and
will ask before switching if it differs from what you configured.

## Usage

### Basic example

```python
from re_gpt import SyncChatGPT
from re_gpt.utils import get_session_token

session_token = get_session_token()
conversation_id = None # conversation ID here


with SyncChatGPT(session_token=session_token) as chatgpt:
    prompt = input("Enter your prompt: ")

    if conversation_id:
        conversation = chatgpt.get_conversation(conversation_id)
    else:
        conversation = chatgpt.create_new_conversation()

    for message in conversation.chat(prompt):
        print(message["content"], flush=True, end="")

```

### Run the interactive CLI

1. Clone this repository and switch into the project directory:

   ```bash
   git clone https://github.com/Zai-Kun/reverse-engineered-chatgpt.git
   cd reverse-engineered-chatgpt
   ```

2. Ensure the launcher script is executable and run it with `bash`:

   ```bash
   chmod +x scripts/run_app.sh  # required on Unix-like systems after cloning
   bash scripts/run_app.sh
   ```

   > **Tip for Windows users:** Run the command from a Bash-compatible environment such as Git Bash or Windows Subsystem for Linux (WSL).

3. When the CLI starts, it prints guidance on how to obtain your session token and prompts you to paste it. You can review the full instructions in the [Obtaining Session Token](#obtaining-session-token) section.

After pasting your token, the launcher starts an interactive ChatGPT session where you can type prompts and read streamed responses directly from your terminal.

### Automation-friendly CLI commands

The CLI also exposes non-interactive helpers that are tailored for scripted or AI-assisted flows such as the `$chat-context-sync` example: request a conversation catalog, `rg` through exported history, inspect the metadata, and then surface the most recent lines (optionally persisting the chat). All commands emit plain text so you can pipe them through `rg`, `awk`, or other tools.

- `python -m re_gpt.cli --list`: Prints `CONVERSATION_ID<TAB>TITLE` for every saved chat so you can feed the IDs into downstream tooling.
- `python -m re_gpt.cli --inspect <CONVERSATION_ID|TITLE>`: Shows stored metadata for a conversation including remote update time, when it was last seen, and how many messages are cached locally. If storage is empty, it will still query the remote catalog to surface timestamps.
- `python -m re_gpt.cli --view "<CONVERSATION_ID|TITLE> [lines START[-END]] [since last update]"`: Streams the requested messages to stdout (no pager). Use the optional `lines` range or `since last update` tokens to limit the slice of messages you need to debug or sync with the UI.
- `python -m re_gpt.cli --download <CONVERSATION_ID|TITLE|all|list>`: Mirrors the interactive `download` command so automation can persist exports (`chat_exports/`) without manual input.

These helpers rely on the same `config.ini`/`~/.chatgpt_session` setup as the interactive CLI; they establish their own session and exit once the requested data has been emitted.

A typical automation flow looks like:

1. Run `python -m re_gpt.cli --list` and feed the ID or title into `rg` while searching the `chat_exports/` directory that `--download` keeps in sync.
2. Use `python -m re_gpt.cli --inspect <ID>` to confirm when the remote conversation last changed and how many messages are already cached.
3. Finally, emit the latest messages with `--view "<ID> since last update"` (or limit the output to a line range) so the next human-in-the-loop prompt can be composed.

For a repeatable baseline you can tweak, run `./scripts/context_sync.sh <conversation-id-or-title>`; it executes `--list`, `--inspect`, `--download`, and `--view` in order while letting you adjust the target, line range, or `rg` pattern from the top of the script. Each helper call is wrapped in `timeout 5s` to abort the newer CLI runs that tend to hang so you get a fresh failure instead of an indefinite wait.

The script relies on the CLI helpers implemented in `re_gpt/cli.py`:

- `run_noninteractive_view` builds the message filters and prints the requested slice of a conversation (used by `--view`).
- `run_inspect_command` pulls cached metadata from `ConversationStorage` and formats it for `--inspect`.
- `handle_download_command` already powers `download` in the interactive loop, so the script can persist JSON assets without additional glue.

If you extend the script, these internal helpers are the extension points to reuse; they share the same storage/timeout handling used by the interactive CLI.

### Resume a previous conversation interactively

```python
from re_gpt import SyncChatGPT
from re_gpt.utils import get_session_token

session_token = get_session_token()

with SyncChatGPT(session_token=session_token) as chatgpt:
    conversations = chatgpt.list_all_conversations()
    for idx, conv in enumerate(conversations):
        print(f"{idx}: {conv['title']}")

    selected = int(input("Select conversation number: "))
    conversation = chatgpt.get_conversation(conversations[selected]["id"])

    prompt = input("Enter your prompt: ")
    for message in conversation.chat(prompt):
        print(message["content"], flush=True, end="")
```

See [examples/select_chat.py](examples/select_chat.py) for the full script.


### Basic async example

```python
import asyncio
import sys

from re_gpt import AsyncChatGPT
from re_gpt.utils import get_session_token

session_token = get_session_token()
conversation_id = conversation_id = None # conversation ID here

if sys.version_info >= (3, 8) and sys.platform.lower().startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


async def main():
    async with AsyncChatGPT(session_token=session_token) as chatgpt:
        prompt = input("Enter your prompt: ")

        if conversation_id:
            conversation = chatgpt.get_conversation(conversation_id)
        else:
            conversation = chatgpt.create_new_conversation()

        async for message in conversation.chat(prompt):
            print(message["content"], flush=True, end="")


if __name__ == "__main__":
    asyncio.run(main())
```

### Send messages programmatically

Both the synchronous and asynchronous clients stream assistant replies by
`POST`ing conversation payloads to the ChatGPT `conversation` endpoint.  The
`SyncConversation.chat` and `AsyncConversation.chat` helpers take care of
constructing the JSON body, attaching the chat requirements token and decoding
the streamed response chunks for you.

```python
from re_gpt import SyncChatGPT
from re_gpt.utils import get_session_token

session_token = get_session_token()

with SyncChatGPT(session_token=session_token) as chatgpt:
    conversation = chatgpt.create_new_conversation()
    for message in conversation.chat("Explain HTTP POST streaming"):
        print(message["content"], end="", flush=True)
```

The asynchronous API exposes the same behaviour through
`AsyncConversation.chat` which awaits the POST response while yielding
assistant messages:

```python
import asyncio

from re_gpt import AsyncChatGPT
from re_gpt.utils import get_session_token


async def main():
    async with AsyncChatGPT(session_token=get_session_token()) as chatgpt:
        conversation = chatgpt.create_new_conversation()
        async for message in conversation.chat("List POST parameters"):
            print(message["content"], end="", flush=True)


asyncio.run(main())
```

### Chat from the terminal

If you would rather talk to ChatGPT directly from a shell, run the synchronous
example script.  It keeps prompting for user input, streams the assistant’s
reply, and works for both new and existing conversations:

```bash
python examples/basic_example.py
```

Update the `conversation_id` constant in the script (or leave it as `None` to
start fresh) before running it.  The more featureful
[`examples/complex_example.py`](examples/complex_example.py) variant colours the
terminal output, saves the latest `conversation_id` back to `config.ini`, and
prints the existing message history each time the script starts.

To resume an archived conversation with a full-screen selector, use the paging
helper:

```bash
python examples/select_chat.py --limit 5
```

`select_chat.py` lists your conversations page-by-page.  Pick a number to open a
chat, press `n` for the next page, `p` for the previous page, or `q` to exit.
The script writes fetched metadata to `conversations.json`, downloads the full
message history to `conversation_<id>.json`, and paginates messages twenty at a
time before handing you back to the live chat loop.

## More Examples

For a more complex example, check out the [examples](/examples) folder in the repository.

### Obtaining The Session Token

1. Go to <https://chatgpt.com/chat> and log in or sign up.
2. Open the developer tools in your browser.
3. Go to the `Application` tab and open the `Cookies` section for `https://chatgpt.com`.
4. Copy the value for `__Secure-next-auth.session-token` from the `chatgpt.com` cookies and save it.

## TODO

- [x] Add more examples
- [ ] Add better error handling
- [x] Implement a function to retrieve all ChatGPT chats
- [ ] Improve documentation

## Contributing

Contributions are what makes the open-source community such an amazing place to learn, inspire, and create. Any contributions you make are **greatly appreciated**.

If you have a suggestion that would make this better, please fork the repo and create a pull request.
Don't forget to give the project a star! Thanks again!

1. Fork the Project
2. Create your Feature Branch (`git checkout -b feature/AmazingFeature`)
3. Commit your Changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the Branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request

## License

Distributed under the Apache License 2.0. See [`LICENSE`](https://github.com/Zai-Kun/reverse-engineered-chatgpt/blob/main/LICENSE) for more information.

## Contact/Bug report

Zai-Kun - [Discord Server](https://discord.gg/ymcqxudVJG)

Repo Link: <https://github.com/Zai-Kun/reverse-engineered-chatgpt>

## Acknowledgments

- [sudoAlphaX (for writing this readme)](https://github.com/sudoAlphaX)

- [yifeikong (curl-cffi module)](https://github.com/yifeikong/curl_cffi)

- [acheong08 (implementation to obtain arkose_token)](https://github.com/acheong08/funcaptcha)

- [pyca (cryptography module)](https://github.com/pyca/cryptography/)

- [Legrandin (pycryptodome module)](https://github.com/Legrandin/pycryptodome/)

- [othneildrew (README Template)](https://github.com/othneildrew)

<!-- MARKDOWN LINKS & IMAGES -->

[forks-badge]: https://img.shields.io/github/forks/Zai-Kun/reverse-engineered-chatgpt
[forks-url]: https://github.com/Zai-Kun/reverse-engineered-chatgpt/network/members
[stars-badge]: https://img.shields.io/github/stars/Zai-Kun/reverse-engineered-chatgpt
[stars-url]: https://github.com/Zai-Kun/reverse-engineered-chatgpt/stargazers
[issues-badge]: https://img.shields.io/github/issues/Zai-Kun/reverse-engineered-chatgpt
[issues-url]: https://github.com/Zai-Kun/reverse-engineered-chatgpt/issues
[discussions-badge]: https://img.shields.io/github/discussions/Zai-Kun/reverse-engineered-chatgpt
[discussions-url]: https://github.com/Zai-Kun/reverse-engineered-chatgpt/discussions
[python-badge]: https://img.shields.io/badge/Python-blue?logo=python&logoColor=yellow
[python-url]: https://www.python.org/
[license-badge]: https://img.shields.io/github/license/Zai-Kun/reverse-engineered-chatgpt
[license-url]: https://github.com/Zai-Kun/reverse-engineered-chatgpt/blob/main/LICENSE
