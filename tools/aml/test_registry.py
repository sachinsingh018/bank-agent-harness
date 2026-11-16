"""The registry must list every public query function, in every tools module that has them."""

import asyncio
import inspect
import sys
from types import ModuleType
from typing import get_type_hints

from tools.aml import detector_docs, patterns, queries
from tools.aml.descriptions import load_descriptions
from tools.aml.query_registry import QUERY_REGISTRY
from tools.aml.server import build_server

QUERY_MODULES: tuple[ModuleType, ...] = (queries, patterns, detector_docs)


def _public_functions(module: ModuleType) -> set[str]:
    return {
        name
        for name, fn in inspect.getmembers(module, inspect.isfunction)
        if not name.startswith("_") and fn.__module__ == module.__name__
    }


def test_every_public_query_function_is_registered() -> None:
    registered = {entry.name for entry in QUERY_REGISTRY}
    public: set[str] = set()
    for module in QUERY_MODULES:
        public |= _public_functions(module)
    assert public == registered


def test_registry_entries_point_at_the_named_function() -> None:
    assert len(QUERY_REGISTRY) == 10
    assert len({entry.name for entry in QUERY_REGISTRY}) == 10
    for entry in QUERY_REGISTRY:
        module = sys.modules[entry.func.__module__]
        assert module in QUERY_MODULES
        assert getattr(module, entry.name) is entry.func


def test_only_describe_detector_needs_no_connection() -> None:
    assert [e.name for e in QUERY_REGISTRY if not e.needs_connection] == ["describe_detector"]


def test_every_registered_tool_has_a_description_and_is_served() -> None:
    names = {entry.name for entry in QUERY_REGISTRY}
    assert set(load_descriptions(names).tools) == names
    served = {tool.name for tool in asyncio.run(build_server().list_tools())}
    assert served == names


def test_registry_models_match_the_function_and_the_server_schema() -> None:
    tools = {tool.name: tool for tool in asyncio.run(build_server().list_tools())}
    for entry in QUERY_REGISTRY:
        hints = get_type_hints(entry.func)
        assert hints["return"] is entry.output_model, entry.name
        assert tools[entry.name].output_schema is not None, entry.name
        assert set(tools[entry.name].input_schema["properties"]) == set(
            entry.input_model.model_fields
        ), entry.name
