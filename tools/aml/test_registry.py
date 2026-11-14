"""The registry must list every public query function, in every tools module that has them."""

import inspect
import sys
from types import ModuleType

from tools.aml import detector_docs, patterns, queries
from tools.aml.query_registry import QUERY_REGISTRY

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
    assert len(QUERY_REGISTRY) == 9
    assert len({entry.name for entry in QUERY_REGISTRY}) == 9
    for entry in QUERY_REGISTRY:
        module = sys.modules[entry.func.__module__]
        assert module in QUERY_MODULES
        assert getattr(module, entry.name) is entry.func


def test_only_describe_detector_needs_no_connection() -> None:
    assert [e.name for e in QUERY_REGISTRY if not e.needs_connection] == ["describe_detector"]
