# Reinstalling Codex Inside a ROCm Docker Container (Commands-Only)

Scope: reuse the working recipe from the chat titled “Reinstalling Codex in Docker” when you are already **inside a ROCm-pinned container** (apt wants to upgrade core libs, so apt-installed Node breaks). This is the commands-only path—no Dockerfile required.

## Quick command sequence

1) Safe base packages (no node/npm via apt)

```bash
apt-get update
apt-get install -y \
  ca-certificates \
  curl \
  git \
  xz-utils \
  tini
```

2) Install Node via tarball (ROCm-safe)

```bash
NODE_VERSION=20.11.1
curl -fsSL https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.xz \
  -o /tmp/node.tar.xz
tar -xJf /tmp/node.tar.xz -C /usr/local --strip-components=1
node --version
npm --version
```

3) Install Codex CLI

```bash
npm install -g @openai/codex
codex --version
```

4) Persist Codex state (bind-mount this when launching the container)

```bash
mkdir -p ~/.codex
```

Example container start that keeps state on the host:

```bash
docker run -it --rm \
  --gpus all \
  -v ~/.codex:/root/.codex \
  rocm/dev-ubuntu-22.04:6.4 \
  /bin/bash
```

5) Authenticate and health-check inside Codex

```text
codex
/auth
/status
```

## Known pitfalls (avoid these)

- Do **not** `apt install nodejs` or `npm` inside ROCm images; dependency resolution upgrades core libs and breaks the stack.
- Forgetting the `~/.codex` bind mount causes auth to be lost every run.
- Mixing multiple MCP server types (Playwright + chrome-devtools) before Codex is stable complicates debugging—bring MCP up only after Codex is working.

## Why this path exists

ROCm base images pin glibc/LLVM/mesa. The tarball install keeps Node isolated from those pins and matches the working recipe captured in the “Reinstalling Codex in Docker” conversation.
