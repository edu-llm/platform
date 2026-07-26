"""Helpers shared by the CloudFormation template test modules.

Not collected by pytest: the filename deliberately does not start with ``test_``.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
INFRA_ROOT = PROJECT_ROOT / "infra"
IAM_ROOT = INFRA_ROOT / "iam"

ACCOUNT_LITERAL = re.compile(r"(?<!\d)\d{12}(?!\d)")
BOUNDARY = {
    "Fn::Sub": ("arn:${AWS::Partition}:iam::${AWS::AccountId}:policy/InternSandboxBoundary")
}
OIDC_PROVIDER = {
    "Fn::Sub": (
        "arn:${AWS::Partition}:iam::${AWS::AccountId}:"
        "oidc-provider/token.actions.githubusercontent.com"
    )
}


def load_template(path: Path) -> dict[str, Any]:
    assert path.is_file(), f"required file is missing: {path.relative_to(PROJECT_ROOT)}"
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def walk_strings(value: object) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, nested in value.items():
            yield from walk_strings(key)
            yield from walk_strings(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from walk_strings(nested)


def iam_roles(template: dict[str, Any]) -> Iterator[dict[str, Any]]:
    resources = template.get("Resources", {})
    assert isinstance(resources, dict)
    for resource in resources.values():
        if isinstance(resource, dict) and resource.get("Type") == "AWS::IAM::Role":
            properties = resource.get("Properties")
            assert isinstance(properties, dict)
            yield properties


def resource_of_type(template: dict[str, Any], resource_type: str) -> tuple[str, dict[str, Any]]:
    matching = [
        (logical_id, resource)
        for logical_id, resource in template["Resources"].items()
        if isinstance(resource, dict) and resource.get("Type") == resource_type
    ]
    assert len(matching) == 1
    return matching[0]


def statement_actions(statement: dict[str, Any]) -> list[str]:
    action = statement["Action"]
    return action if isinstance(action, list) else [action]
