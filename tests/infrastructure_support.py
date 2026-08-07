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

# AN ACCOUNT ID IN SOURCE TEXT, WHICH IS NOT THE SAME QUESTION AS ONE IN A CAPTURED VALUE.
#
# This was `(?<!\d)\d{12}(?!\d)` until 2026-08-06: twelve decimal digits with no digit either
# side, asked of whole files. That says nothing about hexadecimal, and twelve consecutive
# decimal digits turn up inside hexadecimal constantly -- about one 64-character digest in six
# carries a run of them. Source text is full of hexadecimal: commit SHAs, content digests, lock
# hashes, and the UUIDv7 run ids this platform mints, whose final group is twelve hex
# characters and so is all decimal digits about one time in sixteen.
#
# The cost was paid on 2026-08-06, when `run_019fd520-999e-70d8-9003-183311915247` in a test
# file blocked a pull request for a night while reading as this account's real id, which it was
# not. **A guard that produces false positives is a guard people learn to route around**, and
# routing around this one means deleting the assertion that catches the real thing.
#
# So the question is asked about the token rather than about the twelve digits, by two
# lookbehinds that are both fixed-width, which is what lets them be lookbehinds at all:
#
# Not inside a longer hexadecimal run. Nothing in the middle of a digest says where an account
# id would begin or end, so a run of digits there is not one.
#
# Not the final group of a UUID, which is preceded by eight hex characters and three groups of
# four, each with a hyphen -- twenty-four characters exactly, so it is expressible. A hyphen is
# not excluded generally, because CDK writes its asset bucket with the account id between
# hyphens and this organisation deploys with CDK; excluding hyphens wholesale would trade this
# false positive for a false negative in the place an account id is most likely to be committed
# by accident. ``tests/test_evidence.py`` proves that shape is still caught.
#
# **This deliberately does not narrow ``AWS_ACCOUNT_ID_PATTERN``, which guards values.** That
# one is an ``AfterValidator`` on the evidence contracts, where a UUID's final group holding an
# account id is a leak rather than an identifier -- a CloudTrail event id is a UUID, and
# ``test_a_field_that_could_hold_an_account_id_refuses_one`` proves that field refuses one
# dressed as a UUID tail. No captured field legitimately holds a run id, so nothing there pays
# for the paranoia, and the two layers are left asking the two different questions.
UUID_FINAL_GROUP = r"(?<![0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-)"
INSIDE_A_HEX_RUN = r"(?<![0-9A-Fa-f])"
ACCOUNT_LITERAL = re.compile(UUID_FINAL_GROUP + INSIDE_A_HEX_RUN + r"\d{12}(?![0-9A-Fa-f])")
BOUNDARY = {
    "Fn::Sub": ("arn:${AWS::Partition}:iam::${AWS::AccountId}:policy/InternSandboxBoundary")
}
OIDC_PROVIDER = {
    "Fn::Sub": (
        "arn:${AWS::Partition}:iam::${AWS::AccountId}:"
        "oidc-provider/token.actions.githubusercontent.com"
    )
}


#: The one template whose Batch resource names are a substitution rather than a literal.
CAPACITY_BLOCK_TEMPLATE = "infra/batch-capacity-block.yaml"


def load_template(path: Path) -> dict[str, Any]:
    assert path.is_file(), f"required file is missing: {path.relative_to(PROJECT_ROOT)}"
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def block_stack_names() -> set[str]:
    """Every stack name ``infra/batch-capacity-block.yaml`` is deployed under.

    Read from the register rather than assembled from a prefix and the block-backed profiles,
    because that register is what the daily audit reconciles the account against -- so a stack
    this resolves a name for is a stack something else already expects to exist.
    """
    from edullm_platform.stack_templates import STACK_TEMPLATES

    return {stack for stack, template in STACK_TEMPLATES if template == CAPACITY_BLOCK_TEMPLATE}


def deployable_names(declared: Any) -> set[str]:
    """Every name a ``JobQueueName`` or ``JobDefinitionName`` can deploy under, as one set.

    The permanent stacks write the name out and the set has one member.
    ``infra/batch-capacity-block.yaml`` cannot write one: it is deployed once per block-backed
    profile, under a stack name carrying the profile, so a literal would collide on the second
    purchase. It substitutes ``${AWS::StackName}`` instead, which is one name per block-backed
    shape, and any of them is a real deployed name.

    Resolved against ``src/edullm_platform/stack_templates.py``, which is the register the deploy
    workflow builds the stack name from, so this substitutes the way the deploy will rather than
    by pattern-matching a string this file invented.

    Shared by the two modules that compare deployed names against configuration. It was written
    once in each, and one copy resolving a substitution the other does not is how the pair would
    come to disagree about which names exist.
    """
    if isinstance(declared, str):
        return {declared}
    body = declared["Fn::Sub"]
    resolved = {
        body.replace("${AWS::StackName}", stack)
        for stack in block_stack_names()
        if "${" not in body.replace("${AWS::StackName}", stack)
    }
    assert resolved, f"the name {body!r} resolves against no stack this template is deployed as"
    return resolved


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


def statement_resources(statement: dict[str, Any]) -> list[str]:
    """Every resource a statement names, rendered as the string the template writes.

    ``Resource`` is one of four shapes: a literal, an ``Fn::Sub``, or a list of either. A
    reader that handled only the single ``Fn::Sub`` was what every caller here did until a
    statement needed twenty-one ARNs, and each of them failed with a TypeError naming the
    subscript rather than the statement.
    """
    resource = statement["Resource"]
    entries = resource if isinstance(resource, list) else [resource]
    return [entry["Fn::Sub"] if isinstance(entry, dict) else entry for entry in entries]
