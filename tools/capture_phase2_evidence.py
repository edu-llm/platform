"""Capture the GitHub configuration Phase 2's gate has no other way to read.

Three Phase 2 criteria are about GitHub's own settings rather than about code: the reviewer
lists on the two approval environments, the branch policy those environments enforce, and
the absence of repository-level secrets. Nothing in this repository could read any of them,
so all three were gaps citing no test at all. The configuration existed and was believed.

A GitHub setting can be changed in a browser in ten seconds, by anybody with admin, leaving
no artifact in any repository. That is the reason these records expire: a capture is a
statement about one moment, and the freshness window is what stops it reading as a
statement about now.

**A fourth setting joined those three on 2026-07-31, and it is the one that is not a
repository setting at all, which is why it was the last to be captured.** The lead gate's
only reviewer is the ``team-leads`` team, so who may release a routine run is a list held
in organization settings that no file here follows and that an owner can edit without
leaving an artifact anywhere. It is the ``lead-team`` target, and :func:`capture_lead_team`
argues at length about the one thing it can find: a login the platform's own roster does
not declare.

**Secret names, never values.** The models this writes have no field a value could occupy,
so this tool cannot leak one by being careless. It reads the endpoints that return names,
and there is no code path here that asks for a value.

**Writes only under the working directory.** Like the Phase 1 capture, output is refused
anywhere but ``docs-frank/working/phase-2-evidence/``. A capture is local until somebody
reads it and copies what they want into ``fixtures/``, which is a review step rather than a
formality: this reads a live account, and the difference between what it found and what the
repository already claims is exactly what a reader is there to notice.

**What is shared with the other capture tools, and what cannot be.** The stored lineage
bodies go out through :func:`edullm_platform.capture_tooling.write_sanitized_text`, which
is the one write here that carries text nobody in this repository composed and therefore
the one that could commit an account id. Everything else this writes is
``canonical_json_bytes`` -- compact, key-sorted, no trailing structure -- because the
inventory has to be byte-comparable against what the store holds, and that is a different
serialization from the indented one the shared record writer produces. The two cannot be
the same function without one of them changing what it commits.

The GitHub CLI is invoked through ``subprocess`` rather than an SDK, matching Phase 1's use
of the AWS CLI. It also means the tool inherits whatever session the operator already has,
so there is no credential for it to store or mishandle.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, Literal, cast

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from edullm_platform.canonical import canonical_json_bytes
from edullm_platform.capture_tooling import CaptureFailedError, write_sanitized_text
from edullm_platform.phase2_evidence import (
    LEAD_APPROVAL_TEAM_SLUG,
    AdmissionExecution,
    AdmissionExecutionInventory,
    EnvironmentInventory,
    EnvironmentReviewer,
    LeadTeamMembership,
    LineageInventory,
    LineageObject,
    ProtectedEnvironment,
    SecretInventory,
)

#: The deployed names. Written here rather than derived, so a capture aimed at a bucket
#: this project does not own fails on the name instead of quietly recording somebody
#: else's objects as Phase 2 lineage.
LINEAGE_BUCKET: Final = "sbsandbox-intern-edullm-lineage"
STATE_MACHINE_NAME: Final = "sbsandbox-intern-edullm-admission"

#: What ``--target`` accepts and what it does with no flag at all. One tuple rather than
#: two lists, because the two have to agree and a target added to only the second is a
#: capture nobody can ask for by name.
CAPTURE_TARGETS: Final = ("environments", "secrets", "lead-team", "lineage", "executions")

__all__ = [
    "ALLOWED_OUTPUT_SUFFIX",
    "CAPTURE_TARGETS",
    "CaptureError",
    "capture_environments",
    "capture_executions",
    "capture_lead_team",
    "capture_lineage",
    "capture_secrets",
    "main",
]

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


def _is_canonical_json(body: bytes) -> bool:
    """Whether these bytes are exactly canonical_json_bytes of the record they hold.

    True is the property the design claims: the state machine writes the handler's mapping
    and S3 stores the canonical serialization, so the object and the digest quoted for it
    describe the same bytes. False is what records written before the encoding fix look
    like -- a JSON string rather than an object, because the S3 SDK integration encodes
    whatever the Body path yields. Both are recorded; the older shape is history rather
    than a defect to hide.
    """
    try:
        parsed = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return False
    if not isinstance(parsed, dict):
        return False
    canonical = json.dumps(
        parsed, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return body == canonical


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


def capture_lead_team(organization: str, repository: str) -> LeadTeamMembership:
    """Everyone GitHub holds in the team that reviews ``run-approval-lead``.

    The environment capture records that the lead gate's one reviewer is a team. This
    records who that is, which is organization state an owner edits in a browser and which
    no file in this repository follows. Without it a member added to the team is a reviewer
    on the gate and nothing here can say so.

    Sorted, so two captures of an unchanged team are byte-identical apart from the
    observation instant. The committed records are canonical JSON for that reason, and an
    unstable capture would put a diff in front of a reviewer on every re-capture until
    they stopped reading them.

    **WHAT THIS DOES WITH A LOGIN ``config/organization.yaml`` DOES NOT DECLARE: writes it
    down, unchanged.** Every login this endpoint returns is by construction supposed to be
    in ``team_leads``, and the entire reason the record exists is the case where one is
    not. Each way of not writing it down destroys that.

    Folding it to a placeholder the way ``phase1_evidence`` handles an IAM role this
    repository does not declare is the nearest analogue and does not carry over. That
    placeholder protects people in a sandbox AWS account shared with teams this project has
    nothing to do with, whose per-person role names are not this project's to publish.
    Nobody is in ``edu-llm/team-leads`` incidentally: membership is approval authority over
    this platform's runs, granted by an owner of this organization, so an undeclared login
    is not a stranger's name but this project's own unrecorded grant. Two of them would
    also fold to one string, leaving a failure that can say neither who nor how many.

    Dropping it makes the two lists agree by construction, which is this exact hole wearing
    a green tick. Recording a digest instead buys nothing either: a GitHub login has a
    candidate space small enough to invert against the organization's member list, so it
    protects nobody while making the failure message unreadable.

    The privacy cost is real and is paid deliberately, one step later. This tool writes only
    under ``docs-frank/working/``; copying a capture into ``fixtures/`` is a person's
    decision, taken with the unrecognized login in front of them. That review is the
    control, and what it must not be is a decision this code takes silently on their behalf.

    What makes that cost small in practice is worth stating rather than leaving to be
    rediscovered: every login in the committed capture is already in ``team_leads`` in
    ``config/organization.yaml``, which this repository publishes. So the marginal
    disclosure is not the team's membership, which is public here already -- it is confined
    to exactly the undeclared login this record exists to surface, and that one is a grant
    somebody made over this platform's runs rather than a fact about a stranger.
    """
    observed_at = _observed_at()
    # Every page, because truncation here is silent in the direction that matters. The
    # default page holds thirty and the maximum is a hundred, and this asked for a hundred
    # on the argument that a team past the cap would be captured short in a way the roster
    # comparison reports as leads missing from GitHub -- a false alarm rather than a
    # silence. That holds only for a login the roster declares. An undeclared login past
    # the cut is simply absent, and the comparison that would have named it checks the
    # captured logins against the roster, so a login nobody captured fails nothing. The
    # one case this record exists to surface is the one a short capture drops quietly.
    #
    # --slurp is what makes paginating readable back: bare --paginate emits one JSON array
    # per page, which json.loads cannot parse as a single document, and --slurp (gh 2.44
    # and later) wraps the pages into one outer array, flattened below. An older gh fails
    # on the unknown flag and the capture stops, which is the right way to lose.
    #
    # role defaults to all, so maintainers come back beside members, which is right:
    # GitHub asks either of them to review. Members of child teams come back too, carrying
    # inherited true, and they are kept for the same reason -- a child team's member can
    # release a deployment the parent team is the reviewer on, so dropping them would make
    # this record smaller than the set of people who can open the gate.
    pages = _gh(
        "--paginate",
        "--slurp",
        f"orgs/{organization}/teams/{LEAD_APPROVAL_TEAM_SLUG}/members?per_page=100",
    )
    logins = sorted(str(member["login"]) for page in pages or [] for member in page or [])
    return LeadTeamMembership(
        observed_at=observed_at,
        source="github",
        environment="sandbox",
        organization=organization,
        repository=repository,
        team_slug=LEAD_APPROVAL_TEAM_SLUG,
        member_logins=tuple(logins),
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


def _aws(profile: str, region: str, *arguments: str) -> Any:
    completed = subprocess.run(
        ["aws", *arguments, "--profile", profile, "--region", region, "--output", "json"],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise CaptureError(
            f"aws {' '.join(arguments)} failed with {completed.returncode}: "
            f"{completed.stderr.strip()[:400]}"
        )
    return json.loads(completed.stdout or "null")


def capture_lineage(profile: str, region: str) -> tuple[LineageInventory, dict[str, bytes]]:
    """Every lineage object, with the digest and version S3 attests for it.

    ``canonical`` is computed here rather than taken on trust: the stored bytes are parsed
    and re-serialized through ``canonical_json_bytes`` and compared. Objects written before
    the encoding fix are recorded as non-canonical rather than skipped, because a capture
    that hid them would make the store look more uniform than it is.
    """
    observed_at = _observed_at()
    listing = _aws(profile, region, "s3api", "list-objects-v2", "--bucket", LINEAGE_BUCKET)
    objects: list[LineageObject] = []
    records: dict[str, bytes] = {}
    for entry in sorted(listing.get("Contents") or [], key=lambda item: str(item["Key"])):
        key = str(entry["Key"])
        head = _aws(
            profile,
            region,
            "s3api",
            "head-object",
            "--bucket",
            LINEAGE_BUCKET,
            "--key",
            key,
            "--checksum-mode",
            "ENABLED",
        )
        # Downloaded to a file rather than to /dev/stdout: get-object writes its metadata
        # response to stdout as well as the body, so reading the pipe compares the object
        # against a document that also contains a JSON summary of itself. That reported
        # every object as non-canonical, including ones already verified byte-identical
        # by hand, which is the failure mode where a check answers a question nobody asked.
        with tempfile.TemporaryDirectory() as directory:
            downloaded = Path(directory) / "object"
            _aws(
                profile, region, "s3api", "get-object",
                "--bucket", LINEAGE_BUCKET, "--key", key, str(downloaded),
            )
            body = downloaded.read_bytes()
        canonical = _is_canonical_json(body)
        records[key] = body
        objects.append(
            LineageObject(
                observed_at=observed_at,
                key=key,
                version_id=str(head["VersionId"]),
                checksum_sha256=str(head["ChecksumSHA256"]),
                content_length=int(head["ContentLength"]),
                canonical=canonical,
            )
        )
    return (
        LineageInventory(
            observed_at=observed_at,
            source="aws",
            environment="sandbox",
            bucket=LINEAGE_BUCKET,
            objects=tuple(objects),
        ),
        records,
    )


def capture_executions(profile: str, region: str, account_id: str) -> AdmissionExecutionInventory:
    """Every admission execution and where it ended."""
    observed_at = _observed_at()
    arn = f"arn:aws:states:{region}:{account_id}:stateMachine:{STATE_MACHINE_NAME}"
    listing = _aws(
        profile, region, "stepfunctions", "list-executions", "--state-machine-arn", arn
    )
    executions: list[AdmissionExecution] = []
    for entry in sorted(listing.get("executions") or [], key=lambda item: str(item["name"])):
        described = _aws(
            profile,
            region,
            "stepfunctions",
            "describe-execution",
            "--execution-arn",
            str(entry["executionArn"]),
        )
        executions.append(
            AdmissionExecution(
                observed_at=observed_at,
                name=str(entry["name"]),
                # Narrowed for the type checker; the model rejects anything Step Functions
                # would not have said, so an unexpected value fails here rather than being
                # recorded as evidence of a status this platform does not model.
                status=cast(
                    "Literal['SUCCEEDED', 'FAILED', 'TIMED_OUT', 'ABORTED', 'RUNNING']",
                    str(entry["status"]),
                ),
                error=described.get("error") or None,
            )
        )
    return AdmissionExecutionInventory(
        observed_at=observed_at,
        source="aws",
        environment="sandbox",
        state_machine_name=STATE_MACHINE_NAME,
        executions=tuple(executions),
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--organization", default="edu-llm")
    parser.add_argument("--repository", default="platform")
    parser.add_argument("--aws-profile", default="sbsandbox")
    parser.add_argument("--aws-region", default="us-east-1")
    parser.add_argument(
        "--target",
        action="append",
        choices=CAPTURE_TARGETS,
        help="repeatable; defaults to every target",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    arguments = parser.parse_args(argv)
    targets = arguments.target or list(CAPTURE_TARGETS)

    try:
        output_dir = _resolved_output_dir(arguments.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        written: list[str] = []
        account_id = ""
        if {"executions"}.intersection(targets):
            account_id = str(
                _aws(
                    arguments.aws_profile,
                    arguments.aws_region,
                    "sts",
                    "get-caller-identity",
                )["Account"]
            )
        for target in targets:
            if target == "environments":
                record: Any = capture_environments(arguments.organization, arguments.repository)
            elif target == "secrets":
                record = capture_secrets(arguments.organization, arguments.repository)
            elif target == "lead-team":
                record = capture_lead_team(arguments.organization, arguments.repository)
            elif target == "lineage":
                record, bodies = capture_lineage(arguments.aws_profile, arguments.aws_region)
                # The records themselves, beside the inventory. The inventory says what S3
                # attests about each object; only the body says what the platform decided,
                # and the criteria about record content have to read one.
                for key, body in bodies.items():
                    write_sanitized_text(output_dir / "records" / key, body.decode("utf-8"))
                    written.append(f"records/{key}")
            else:
                record = capture_executions(
                    arguments.aws_profile, arguments.aws_region, account_id
                )
            path = output_dir / f"{target}.sanitized.json"
            path.write_bytes(canonical_json_bytes(record) + b"\n")
            written.append(path.name)
    except (CaptureError, CaptureFailedError) as error:
        print(str(error), file=sys.stderr)
        return 2
    except Exception as error:  # noqa: BLE001 - CLI maps unexpected failures to exit 2
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        return 2

    print(json.dumps({"written": sorted(written), "targets": targets}, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    raise SystemExit(main())
