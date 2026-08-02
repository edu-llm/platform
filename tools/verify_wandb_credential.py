"""Report whether the stored W&B key is well formed and which entity W&B resolves it to.

Prints a length, a truncated digest and the resolved entity. Never prints the key.

The report is also read by something other than a person. ``nightly.yml`` publishes it as
an artifact and ``submit-run.yml`` refuses a submission on the strength of it, because no
identity the submit path can obtain holds the read this tool makes and
``edullm_platform.wandb_preflight`` argues at length why none should. That is what the
``verdict`` and ``checked_at`` fields are for: a second reader deciding "refused" by
matching strings against ``looks_wrong``, which is a list of sentences written for a human,
would be a second definition of the word that drifts from this one without failing.
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
from datetime import UTC, datetime
from typing import Any

from edullm_platform.wandb_preflight import CHECKED_AT_FIELD, VERDICT_FIELD, Verdict

SECRET_NAME = "sbsandbox-intern-edullm-wandb-api-key"

WANDB_GRAPHQL = "https://api.wandb.ai/graphql"
VIEWER_QUERY = '{"query":"query { viewer { username entity } }"}'

#: Service-account keys carry this prefix. Personal keys predate it and are forty hex
#: characters. An unrecognised shape is reported rather than rejected, so a form W&B adds
#: later does not fail here.
SERVICE_ACCOUNT_PREFIX = "wandb_v1_"
LEGACY_KEY_LENGTH = 40

#: How a network failure introduces itself in the ``error`` this reports. Hoisted to a
#: constant because the verdict below has to tell "W&B says no" from "W&B did not answer",
#: and those two must never be separated by a string literal written twice.
UNREACHABLE_PREFIX = "could not reach W&B"

__all__ = [
    "CHECKED_AT_FIELD",
    "LEGACY_KEY_LENGTH",
    "SECRET_NAME",
    "SERVICE_ACCOUNT_PREFIX",
    "UNREACHABLE_PREFIX",
    "VERDICT_FIELD",
    "Verdict",
    "WandbCredentialError",
    "ask_wandb_who_this_is",
    "build_parser",
    "describe",
    "main",
    "read_the_secret",
    "verdict_for",
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
        return {"error": f"{UNREACHABLE_PREFIX}: {exc.__class__.__name__}"}
    viewer = (body.get("data") or {}).get("viewer")
    if not viewer:
        return {"error": "W&B does not recognise this key"}
    return {"entity": viewer.get("entity"), "username": viewer.get("username")}


def verdict_for(answer: dict[str, Any], *, faults: Sequence[str]) -> Verdict:
    """What W&B's answer amounts to, in the vocabulary the submit path reads.

    An outage is not a refusal, and keeping them apart is the whole reason this returns
    three values rather than a boolean. A preflight that read "could not reach W&B" as a bad
    key would turn every W&B outage into a platform outage, and this repository has already
    paid once for a check that could not say "I did not find out".

    Faults visible without asking W&B still count as a refusal. A key with the literal word
    ``api`` glued to the front is the fault that produced the last rotation incident, and
    W&B refuses it -- so a shape W&B will not accept is a refusal whether or not the round
    trip happened to confirm it.
    """
    error = answer.get("error")
    if isinstance(error, str) and error.startswith(UNREACHABLE_PREFIX):
        return Verdict.UNREACHABLE
    return Verdict.REFUSED if faults else Verdict.ACCEPTED


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
    # When W&B was asked, not when the artifact carrying this was uploaded. The preflight
    # ages the verdict off this field, so it has to be the moment the answer was true.
    report[CHECKED_AT_FIELD] = datetime.now(tz=UTC).isoformat()

    if not options.offline:
        answer = ask_wandb_who_this_is(value.strip())
        report["wandb"] = answer
        if "entity" in answer and answer["entity"] != expected:
            faults = [*faults, f"W&B resolves this key to {answer['entity']!r}, not {expected!r}"]
        elif "error" in answer:
            faults = [*faults, answer["error"]]
        report["looks_wrong"] = faults
        report[VERDICT_FIELD] = verdict_for(answer, faults=faults)
    # No verdict at all under --offline, rather than one derived from the shape. A shape
    # check is not W&B's answer, and the last incident was a key of exactly the right shape
    # that W&B refused; publishing "accepted" for it would be the preflight passing on the
    # strength of the measurement that already failed to catch it.

    print(json.dumps(report, indent=2, sort_keys=True))
    return 1 if faults else 0


if __name__ == "__main__":
    sys.exit(main())
