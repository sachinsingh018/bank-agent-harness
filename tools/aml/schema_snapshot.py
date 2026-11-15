"""The tool schema snapshot: names, descriptions and schemas the model sees.

Needs no database. Regenerate after a deliberate change with
`uv run python -m tools.aml.schema_snapshot --write` and review the diff.
"""

import argparse
import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from tools.aml.descriptions import load_descriptions
from tools.aml.query_registry import QUERY_REGISTRY
from tools.aml.server import build_server

SNAPSHOT_PATH = Path(__file__).with_name("tool_schemas.snapshot.json")
log = logging.getLogger("aml_schema_snapshot")


def snapshot_from_tools(tools: list[Any], version: str) -> dict[str, Any]:
    """The comparable form of a tool list, from the server or from a client."""
    return {
        "descriptions_version": version,
        "tools": [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema,
                "output_schema": tool.output_schema,
            }
            for tool in sorted(tools, key=lambda t: str(t.name))
        ],
    }


def live_snapshot() -> dict[str, Any]:
    version = load_descriptions(entry.name for entry in QUERY_REGISTRY).version
    tools = asyncio.run(build_server().list_tools())
    return snapshot_from_tools(list(tools), version)


def render(snapshot: dict[str, Any]) -> str:
    return json.dumps(snapshot, indent=2, sort_keys=True) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Check or write the tool schema snapshot.")
    parser.add_argument("--write", action="store_true", help="write the snapshot file")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")  # stderr
    text = render(live_snapshot())
    if args.write:
        SNAPSHOT_PATH.write_text(text, encoding="utf-8")
        log.info("wrote %s", SNAPSHOT_PATH.name)
        return
    if SNAPSHOT_PATH.read_text(encoding="utf-8") != text:
        raise SystemExit("snapshot differs from the live tools: rerun with --write and review")
    log.info("snapshot matches")


if __name__ == "__main__":
    main()
