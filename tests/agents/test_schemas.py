"""Agent output schemas must satisfy Azure OpenAI structured-output strict mode.

Strict mode rejects schemas where any object has optional properties, allows extra
properties, or uses defaults. Breaking these rules fails at request time in production,
so they are enforced here instead.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel

from marketlens.agents.schemas import ExecutiveBriefing, MarketAnalysis, NewsAnalysis


def _violations(node: Any, path: str = "$") -> list[str]:
    problems: list[str] = []
    if isinstance(node, dict):
        if node.get("type") == "object" or "properties" in node:
            properties = set(node.get("properties", {}))
            if set(node.get("required", [])) != properties:
                problems.append(f"{path}: every property must be required")
            if node.get("additionalProperties") is not False:
                problems.append(f"{path}: additionalProperties must be false")
        if "default" in node:
            problems.append(f"{path}: defaults are not allowed")
        for key, value in node.items():
            problems += _violations(value, f"{path}.{key}")
    elif isinstance(node, list):
        for i, value in enumerate(node):
            problems += _violations(value, f"{path}[{i}]")
    return problems


@pytest.mark.parametrize("schema", [MarketAnalysis, NewsAnalysis, ExecutiveBriefing])
def test_schema_is_strict_mode_compatible(schema: type[BaseModel]) -> None:
    assert _violations(schema.model_json_schema()) == []


def test_checker_catches_an_optional_field() -> None:
    class Loose(BaseModel):
        name: str = "x"

    assert _violations(Loose.model_json_schema())
