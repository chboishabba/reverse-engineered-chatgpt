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
environment and then execute the tests.

### Configuration

Copy `config.example.ini` to `config.ini` and update the placeholder values or
store your token in a `~/.chatgpt_session` file:

```sh
cp config.example.ini config.ini
```

Edit `config.ini` and replace `token` with your ChatGPT session token and `conversation_id` with the ID of an existing conversation if you want to resume one. If a `config.ini` is not found, `get_session_token` will look for a token in `~/.chatgpt_session` instead.

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

### Resume existing chat

Page through your existing conversations and choose one to continue:

```bash
python examples/select_chat.py --limit 5
```

Use the numeric menu to pick a conversation from the current page.  Press
`n` for the next page, `p` for the previous page or `q` to quit.  Fetched
metadata is written to `conversations.json`.  After selecting a conversation,
its full history is saved to `conversation_<id>.json` and displayed twenty
messages at a time.  Navigate the message viewer with `n`, `p` and `q` before
continuing the chat.

## More Examples

For a more complex example, check out the [examples](/examples) folder in the repository.

### Obtaining The Session Token

1. Go to <https://chatgpt.com/> and log in or sign up.
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
