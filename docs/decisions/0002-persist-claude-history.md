# 0002: Persist Claude Code chat history across rebuilds

**What:** Mount a named Docker volume `claude-home` at `/home/vscode/.claude` in `.devcontainer/docker-compose.yml`. `post-create.sh` fixes its ownership and does a one-time restore from the gitignored `.claude-home-backup/` folder.

**Why:** A container rebuild wiped the home folder and all earlier Claude Code chats. Named volumes survive rebuilds inside the same Codespace.

**Alternatives:**
- Bind-mount history into the repo folder: rejected, too easy to commit private chats or credentials to a public repo.
- A hook that copies transcripts after each turn: more moving parts for the same result.

**Limit:** Deleting the Codespace deletes the volume too.
