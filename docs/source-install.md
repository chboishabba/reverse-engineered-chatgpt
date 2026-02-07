# Source Install and CLI Setup

This guide covers common ways to run `re_gpt` from a local checkout.

## 1. Editable install (recommended for development)

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install -e .
```

Verify the install:

```bash
python -m re_gpt.cli --help
```

## 2. Restricted-network or offline fallback

If editable install fails while `pip` tries to fetch build dependencies, install
without build isolation:

```bash
python -m pip install --no-build-isolation -e .
```

If dependencies are already present in the environment, you can also skip
dependency resolution:

```bash
python -m pip install --no-deps --no-build-isolation -e .
```

## 3. Configure session token for CLI commands

The CLI reads session token from either:
- `CHATGPT_SESSION_TOKEN` environment variable, or
- first line of `~/.chatgpt_session`.

Example:

```bash
export CHATGPT_SESSION_TOKEN="<your-token>"
python -m re_gpt.cli --list
python -m re_gpt.cli --view <conversation-id>
```

## 4. Use `re_gpt` from another project

If another repository should depend on this local checkout, add an editable path
to its requirements file:

```text
-e ./reverse-engineered-chatgpt
```

Then install from that repository's virtual environment:

```bash
python -m pip install -r requirements.txt
```
