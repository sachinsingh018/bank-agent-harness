"""How the harness starts the AML tools server as a child process over stdio."""

import sys
from pathlib import Path

from mcp import StdioServerParameters

REPO_ROOT = Path(__file__).resolve().parents[2]


def server_params(database_url: str) -> StdioServerParameters:
    """Launch parameters. The env we pass contains exactly DATABASE_URL and PYTHONPATH. The MCP
    client adds only its own safe basics (HOME, LOGNAME, PATH, SHELL, TERM, USER), no secrets."""
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "tools.aml.server"],
        cwd=REPO_ROOT,
        env={"DATABASE_URL": database_url, "PYTHONPATH": str(REPO_ROOT)},
    )
