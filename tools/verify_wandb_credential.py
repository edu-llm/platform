"""Report whether the stored W&B key is well formed and which entity W&B resolves it to.

Prints a length, a truncated digest and the resolved entity. Never prints the key.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import subprocess
import sys
import urllib.error
import urllib.request
from collections.abc import Sequence
from typing import Any

SECRET_NAME = "sbsandbox-intern-edullm-wandb-api-key"

WANDB_GRAPHQL = "https://api.wandb.ai/graphql"
VIEWER_QUERY = '{"query":"query { viewer { username entity } }"}'

#: Service-account keys carry this prefix. Personal keys predate it and are forty hex
#: characters. An unrecognised shape is reported rather than rejected, so a form W&B adds
#: later does not fail here.
SERVICE_ACCOUNT_PREFIX = "wandb_v1_"
LEGACY_KEY_LENGTH = 40

__all__ = [
    "LEGACY_KEY_LENGTH",
    "SECRET_NAME",
    "SERVICE_ACCOUNT_PREFIX",
    "WandbCredentialError",
    "ask_wandb_who_this_is",
    "build_parser",
    "describe",
    "main",
    "read_the_secret",
    "what_looks_wrong",
]


class WandbCredentialError(RuntimeError):
    pass


def describe(value: str) -> dict[str, Any]:
    """What can be said about a key in a log anyone may read."""
    return {
        "length": len(value),
        "prefix4": value[:4],
        "fingerprint": hashlib.sha256(value.encode()).hexdigest()[:8],
    }


def what_looks_wrong(value: str) -> list[str]:
    """Faults visible without asking W&B, most actionable first."""
    faults: list[str] = []
    if value != value.strip():
        faults.append("the stored value has whitespace around it")
    bare = value.strip()
    if not bare:
        faults.append("the stored value is empty")
        return faults
    if bare.startswith("api") and bare[3:].startswith(SERVICE_ACCOUNT_PREFIX):
        faults.append("the stored value is prefixed with the literal word `api`")
    elif not bare.startswith(SERVICE_ACCOUNT_PREFIX) and len(bare) != LEGACY_KEY_LENGTH:
        faults.append(
            f"the stored value is neither a service-account key (`{SERVICE_ACCOUNT_PREFIX}...`) "
            f"nor a personal key ({LEGACY_KEY_LENGTH} hex characters)"
        )
    return faults


def read_the_secret(secret_name: str, *, profile: str | None, region: str) -> str:
    """The current value, through the CLI because this package does not depend on boto3.

    Only the newline ``--output text`` appends is removed; anything else the value carries
    reaches ``what_looks_wrong``.
    """
    call = [
        "aws",
        "secretsmanager",
        "get-secret-value",
        "--secret-id",
        secret_name,
        "--region",
        region,
    ]
    if profile:
        call += ["--profile", profile]
    call += ["--query", "SecretString", "--output", "text"]
    try:
        finished = subprocess.run(call, capture_output=True, text=True, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise WandbCredentialError(
            f"could not read {secret_name}: {exc.__class__.__name__}"
        ) from exc
    if finished.returncode != 0:
        raise WandbCredentialError(
            f"could not read {secret_name}: the CLI exited {finished.returncode}"
        )
    return finished.stdout.removesuffix("\n")


def ask_wandb_who_this_is(value: str, *, timeout: int = 30) -> dict[str, Any]:
    """W&B's answer as ``{"entity": ...}`` or ``{"error": ...}``.

    An unrecognised key is a 200 with a null viewer, not a 401, so this reads the body.
    """
    request = urllib.request.Request(
        WANDB_GRAPHQL,
        data=VIEWER_QUERY.encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": "Basic " + base64.b64encode(f"api:{value}".encode()).decode(),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, ValueError) as exc:
        return {"error": f"could not reach W&B: {exc.__class__.__name__}"}
    viewer = (body.get("data") or {}).get("viewer")
    if not viewer:
        return {"error": "W&B does not recognise this key"}
    return {"entity": viewer.get("entity"), "username": viewer.get("username")}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check the stored W&B key without printing it.")
    parser.add_argument("--secret-name", default=SECRET_NAME)
    parser.add_argument("--profile", default=None)
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument(
        "--expect-entity",
        default=None,
        help="Fail unless W&B resolves the key to this entity. Defaults to the platform's own.",
    )
    parser.add_argument("--offline", action="store_true", help="Check the shape only.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    options = build_parser().parse_args(argv)
    expected = options.expect_entity
    if expected is None:
        from edullm_platform.execution import WANDB_ENTITY

        expected = WANDB_ENTITY

    value = read_the_secret(options.secret_name, profile=options.profile, region=options.region)
    report: dict[str, Any] = {"secret": options.secret_name, **describe(value)}
    faults = what_looks_wrong(value)
    report["looks_wrong"] = faults

    if not options.offline:
        answer = ask_wandb_who_this_is(value.strip())
        report["wandb"] = answer
        if "entity" in answer and answer["entity"] != expected:
            faults = [*faults, f"W&B resolves this key to {answer['entity']!r}, not {expected!r}"]
        elif "error" in answer:
            faults = [*faults, answer["error"]]
        report["looks_wrong"] = faults

    print(json.dumps(report, indent=2, sort_keys=True))
    return 1 if faults else 0


if __name__ == "__main__":
    sys.exit(main())
