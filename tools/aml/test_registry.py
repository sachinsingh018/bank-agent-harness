"""The registry must list every public query function."""

import inspect

from tools.aml import queries
from tools.aml.query_registry import QUERY_REGISTRY


def _public_query_functions() -> set[str]:
    return {
        name
        for name, fn in inspect.getmembers(queries, inspect.isfunction)
        if not name.startswith("_") and fn.__module__ == queries.__name__
    }


def test_every_public_query_function_is_registered() -> None:
    registered = {entry.name for entry in QUERY_REGISTRY}
    assert _public_query_functions() == registered


def test_registry_entries_point_at_the_named_function() -> None:
    assert len(QUERY_REGISTRY) == 6
    for entry in QUERY_REGISTRY:
        assert getattr(queries, entry.name) is entry.func
