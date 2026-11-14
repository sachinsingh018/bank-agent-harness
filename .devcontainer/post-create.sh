#!/usr/bin/env bash
set -euo pipefail

# The claude-home volume is created by Docker as root. Give it to vscode.
sudo chown -R vscode:vscode /home/vscode/.claude

# One-time restore of chat history saved before the volume existed.
BACKUP=/workspaces/bank-agent-harness/.claude-home-backup
if [ -d "$BACKUP" ] && [ ! -d /home/vscode/.claude/projects ]; then
  cp -a "$BACKUP"/. /home/vscode/.claude/
  echo "Restored Claude Code history from $BACKUP"
fi

# Git LFS hooks are installed in this repo, so the binary must exist.
sudo apt-get update -qq && sudo apt-get install -y -qq git-lfs

pip install --user uv pre-commit
uv sync
pre-commit install
echo "Dev environment ready."
