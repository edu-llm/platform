"""Capture the GitHub configuration Phase 2's gate has no other way to read.

Three Phase 2 criteria are about GitHub's own settings rather than about code: the reviewer
lists on the two approval environments, the branch policy those environments enforce, and
the absence of repository-level secrets. Nothing in this repository could read any of them,
so all three were gaps citing no test at all. The configuration existed and was believed.

A GitHub setting can be changed in a browser in ten seconds, by anybody with admin, leaving
no artifact in any repository. That is the reason these records expire: a capture is a
statement about one moment, and the freshness window is what stops it reading as a
statement about now.

**Secret names, never values.** The models this writes have no field a value could occupy,
so this tool cannot leak one by being careless. It reads the endpoints that return names,
and there is no code path here that asks for a value.

**Writes only under the working directory.** Like the Phase 1 capture, output is refused
anywhere but ``docs-frank/working/phase-2-evidence/``. A capture is local until somebody
reads it and copies what they want into ``fixtures/``, which is a review step rather than a
formality: this reads a live account, and the difference between what it found and what the
repository already claims is exactly what a reader is there to notice.

The GitHub CLI is invoked through ``subprocess`` rather than an SDK, matching Phase 1's use
of the AWS CLI. It also means the tool inherits whatever session the operator already has,
so there is no credential for it to store or mishandle.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from edullm_platform.canonical import canonical_json_bytes
from edullm_platform.phase2_evidence import (
    EnvironmentInventory,
    EnvironmentReviewer,
    ProtectedEnvironment,
    SecretInventory,
)

__all__ = ["ALLOWED_OUTPUT_SUFFIX", "CaptureError", "capture_environments", "capture_secrets", "main"]

#: A capture is local until somebody reads it and copies what they want into fixtures/.
#: Writing anywhere else is refused rather than discouraged.
ALLOWED_OUTPUT_SUFFIX: Final = Path("docs-frank/working/phase-2-evidence")


class CaptureError(RuntimeError):
    """The capture could not be taken, or would have been written somewhere it must not be."""


def _resolved_output_dir(output_dir: Path) -> Path:
    resolved = output_dir.resolve()
    if not resolved.as_posix().endswith(ALLOWED_OUTPUT_SUFFIX.as_posix()):
        raise CaptureError(
            "output_dir_outside_working_directory\n"
            f"{resolved} is not {ALLOWED_OUTPUT_SUFFIX}/. A capture reads a live account "
            "and is local until somebody has read it and copied what they want into "
            f"fixtures/, so this tool writes only under {ALLOWED_OUTPUT_SUFFIX}/ and "
            "refuses anywhere else."
        )
    return resolved


def _gh(*arguments: str) -> Any:
    """One `gh api` call, parsed. A non-zero exit is a capture failure, never an empty record."""
    completed = subprocess.run(
        ["gh", "api", *arguments], capture_output=True, text=True, check=False
    )
    if completed.returncode != 0:
        raise CaptureError(
            f"gh api {' '.join(arguments)} failed with {completed.returncode}: "
            f"{completed.stderr.strip()[:400]}"
        )
    return json.loads(completed.stdout or "null")


def _observed_at() -> datetime:
    return datetime.now(tz=UTC)


def capture_environments(organization: str, repository: str) -> EnvironmentInventory:
    """Every environment on the repository, with the protection each one actually carries.

    All of them, not only the two this phase expects. An environment is auto-created with
    no protection rules by anyone who names one in a workflow file, so a capture restricted
    to the expected names would report a healthy gate with an unprotected environment
    sitting beside it.
    """
    slug = f"{organization}/{repository}"
    observed_at = _observed_at()
    listing = _gh(f"repos/{slug}/environments")

    captured: list[ProtectedEnvironment] = []
    for entry in listing.get("environments") or []:
        name = entry["name"]
        rules = {rule["type"]: rule for rule in entry.get("protection_rules") or []}
        required = rules.get("required_reviewers", {})
        reviewers = tuple(
            EnvironmentReviewer(
                observed_at=observed_at,
                kind=reviewer["type"],
                name=reviewer["reviewer"].get("slug") or reviewer["reviewer"]["login"],
            )
            for reviewer in required.get("reviewers") or []
        )
        policy = entry.get("deployment_branch_policy") or {}
        branch_names: tuple[str, ...] = ()
        if policy.get("custom_branch_policies"):
            branches = _gh(f"repos/{slug}/environments/{name}/deployment-branch-policies")
            branch_names = tuple(
                sorted(item["name"] for item in branches.get("branch_policies") or [])
            )
        captured.append(
            ProtectedEnvironment(
                observed_at=observed_at,
                source="github",
                environment="sandbox",
                organization=organization,
                repository=repository,
                name=name,
                reviewers=reviewers,
                prevent_self_review=bool(required.get("prevent_self_review", False)),
                can_admins_bypass=bool(entry.get("can_admins_bypass", True)),
                protected_branches=bool(policy.get("protected_branches", False)),
                custom_branch_policies=bool(policy.get("custom_branch_policies", False)),
                branch_policy_names=branch_names,
                wait_timer_minutes=int(rules.get("wait_timer", {}).get("wait_timer", 0) or 0),
            )
        )

    return EnvironmentInventory(
        observed_at=observed_at,
        source="github",
        environment="sandbox",
        organization=organization,
        repository=repository,
        environments=tuple(sorted(captured, key=lambda item: item.name)),
    )


def capture_secrets(organization: str, repository: str) -> SecretInventory:
    """Secret and variable names at every level a workflow can read from.

    Names only, and the model has nowhere to put a value even if this asked for one. The
    endpoints used return names and metadata; none of them returns a secret.
    """
    slug = f"{organization}/{repository}"
    observed_at = _observed_at()

    def names(path: str, key: str) -> tuple[str, ...]:
        payload = _gh(path)
        return tuple(sorted(item["name"] for item in payload.get(key) or []))

    environment_secrets: dict[str, tuple[str, ...]] = {}
    listing = _gh(f"repos/{slug}/environments")
    for entry in listing.get("environments") or []:
        name = entry["name"]
        environment_secrets[name] = names(
            f"repos/{slug}/environments/{name}/secrets", "secrets"
        )

    return SecretInventory(
        observed_at=observed_at,
        source="github",
        environment="sandbox",
        organization=organization,
        repository=repository,
        repository_secret_names=names(f"repos/{slug}/actions/secrets", "secrets"),
        organization_secret_names=names(f"orgs/{organization}/actions/secrets", "secrets"),
        dependabot_secret_names=names(f"repos/{slug}/dependabot/secrets", "secrets"),
        environment_secret_names=dict(sorted(environment_secrets.items())),
        repository_variable_names=names(f"repos/{slug}/actions/variables", "variables"),
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--organization", default="edu-llm")
    parser.add_argument("--repository", default="platform")
    parser.add_argument(
        "--target",
        action="append",
        choices=["environments", "secrets"],
        help="repeatable; defaults to every target",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    arguments = parser.parse_args(argv)
    targets = arguments.target or ["environments", "secrets"]

    try:
        output_dir = _resolved_output_dir(arguments.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        written: list[str] = []
        for target in targets:
            record = (
                capture_environments(arguments.organization, arguments.repository)
                if target == "environments"
                else capture_secrets(arguments.organization, arguments.repository)
            )
            path = output_dir / f"{target}.sanitized.json"
            path.write_bytes(canonical_json_bytes(record) + b"\n")
            written.append(path.name)
    except CaptureError as error:
        print(str(error), file=sys.stderr)
        return 2
    except Exception as error:  # noqa: BLE001 - CLI maps unexpected failures to exit 2
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        return 2

    print(json.dumps({"written": sorted(written), "targets": targets}, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    raise SystemExit(main())
