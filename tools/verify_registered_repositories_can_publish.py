"""That every registration can actually reach the registry its registration gave it.

A registration is a claim that a repository may publish an image. Six things have to be
true for that claim to hold, and until this check existed the platform held **none** of
them: it holds the registry against the ECR template and the publisher role, and then
stops at the organization boundary. Everything on the far side of that boundary -- the
caller workflow, the job's ``id-token`` grant, the ref the ``uses:`` names, and the
repository Actions variable carrying the role ARN -- is invisible here, is written by
hand, and is not written by ``tools/register_repository.py``.

**This was shipped, and it cost the morning of 2026-08-06.** ``edullm-p1`` was registered
on 2026-08-04 with an ECR repository, a widened publisher role, a workload profile, a
Dockerfile the sibling check confirms is there, and a caller workflow that passes
``publisher_role_arn: ${{ vars.AWS_ECR_PUBLISHER_ROLE_ARN }}`` exactly as the other five
do. **Nobody set the variable.** An unset variable renders as the empty string, GitHub
drops an empty input, and ``aws-actions/configure-aws-credentials`` receives no
``role-to-assume`` and falls through to a default credential chain with nothing in it. The
error is ``Could not load credentials from any providers``, which names neither the
variable nor the repository nor the registration. Nothing anywhere went red until somebody
dispatched a build by hand and read the log, and ``sbsandbox-intern-edullm-p1`` had held
zero images for two days.

``docs-frank/reference/registering-a-repository.md`` §1.8 predicts this failure in as many
words. **Prediction is not prevention**, and a runbook is read by whoever is doing a
registration rather than by whoever registered one last week -- which is why this is a
check on the schedule rather than another paragraph.

WHY THE VARIABLE ITSELF IS NOT WHAT THIS READS, WHICH IS THE ONE DESIGN CONSTRAINT WORTH
KNOWING. The obvious check is to fetch each repository's ``AWS_ECR_PUBLISHER_ROLE_ARN``
and compare. It cannot be written here. Actions variables are readable only by a
collaborator on the repository holding them, ``GITHUB_TOKEN`` is a collaborator on nothing
but the repository whose workflow minted it, and this repository is asserted to hold no
credential at all by ``test_the_repository_holds_no_secret_a_branch_could_read`` and
``test_phase_two_introduced_no_credential_at_all``. Reading the variable therefore costs
the first stored credential in the platform, which is a much larger decision than the one
it would settle.

So this asks the question the variable is a means to, which is strictly broader and needs
nothing but a public read: **has a build of this registration ever succeeded?** A missing
variable, an undeployed publisher role, a SHA-pinned ``uses:``, an absent ``id-token``
grant and a caller workflow nobody ever wrote all answer no, and each of them is a
registration that is submittable and cannot produce the image a submission will be refused
for lacking.

The four static findings above it are the parts of the §1.8 caller contract that a file
can be held to. They cost nothing beyond the fetch this already makes, and they turn "no
build has ever succeeded" from a fact into a repair, because each names the line to change
and the repository to change it in.

**The subject is the registry and is never a list in here**, for the reason
``verify_registered_dockerfiles.py`` gives about itself: a check that restated which
repositories to ask about would go stale on the next registration in exactly the way the
thing it is checking did.

The two non-zero exits are not interchangeable. Exit 1 says a registration cannot publish
and sends a reader to a repository setting or a workflow file; exit 2 says this check did
not manage to look and sends them to a session or a rate limit.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

import yaml

from edullm_platform.config import load_yaml
from edullm_platform.contracts.repository_registry import (
    RegisteredRepository,
    RepositoryRegistry,
)

PROJECT_ROOT: Final = Path(__file__).resolve().parents[1]
REGISTRY_PATH: Final = PROJECT_ROOT / "config" / "repositories.yaml"

#: Spelled the way ``tools/verify_registered_dockerfiles.py`` spells it, and overridable
#: for the same reason.
DEFAULT_ORGANIZATION: Final = "edu-llm"

#: The reusable workflow every caller must name. The publisher role's trust policy pins
#: ``job_workflow_ref`` to this path with ``StringEquals``, so this string is not a
#: convention: a caller naming anything else mints a claim IAM will never accept, and a
#: rename of the file here silently revokes every publish in the organization.
BUILD_WORKFLOW: Final = "edu-llm/platform/.github/workflows/build-research-image.yml"

#: The only ref the trust policy accepts, matched against ``@refs/heads/main``. This is
#: the one place in the platform where pinning a ``uses:`` to a SHA makes things worse.
REQUIRED_REF: Final = "main"

#: Where a caller workflow has to live for GitHub to run it at all.
WORKFLOWS_DIRECTORY: Final = ".github/workflows"

#: The repository Actions variable each caller reads the publisher role ARN out of. Named
#: here because the caller names it, and because a caller wiring the input to some other
#: variable is a finding this cannot otherwise see -- the value is unreadable from here
#: (see the module docstring), but the name in the call site is not.
PUBLISHER_ROLE_VARIABLE: Final = "AWS_ECR_PUBLISHER_ROLE_ARN"

EXIT_OK: Final = 0

#: A registration cannot publish. A definite answer about a repository, whose repair is a
#: workflow file or a repository setting.
EXIT_CANNOT_PUBLISH: Final = 1

#: Nothing was read, so nothing is claimed. Never reported as a pass, because a check that
#: cannot look is not a check that found nothing.
EXIT_UNUSABLE: Final = 2

__all__ = [
    "BUILD_WORKFLOW",
    "DEFAULT_ORGANIZATION",
    "EXIT_CANNOT_PUBLISH",
    "EXIT_OK",
    "EXIT_UNUSABLE",
    "PUBLISHER_ROLE_VARIABLE",
    "Caller",
    "Finding",
    "build_parser",
    "check_registration",
    "contract_findings",
    "main",
    "subjects",
]


class GitHubUnreachable(Exception):
    """``gh`` did not answer, so the question was never put to GitHub."""


@dataclass(frozen=True)
class Finding:
    """One answer about one registration, with the exit code it argues for."""

    reason: str
    message: str
    code: int


@dataclass(frozen=True)
class Caller:
    """One job, in one workflow file, that calls the platform's build workflow."""

    path: str
    job_id: str
    definition: dict[str, Any]


def subjects(registry: RepositoryRegistry) -> tuple[RegisteredRepository, ...]:
    """Every registration, in the order the file writes them.

    A function rather than an inlined attribute, so that the test holding the questions
    to the committed file has one thing to hold and a future exemption is a change
    somewhere a reader looks.
    """
    return registry.repositories


def _github(*arguments: str) -> Any:
    """One ``gh api`` call, parsed, telling "GitHub said no" apart from "GitHub was not asked".

    A 404 is an answer and is returned as ``None``. Anything else -- no session, no
    network, a rate limit -- never established anything and is raised, so it leaves by the
    ``EXIT_UNUSABLE`` door rather than being reported as a repository that cannot publish.
    """
    completed = subprocess.run(
        ["gh", "api", *arguments], capture_output=True, text=True, check=False
    )
    if completed.returncode == 0:
        return json.loads(completed.stdout or "null")
    if "HTTP 404" in completed.stderr:
        return None
    raise GitHubUnreachable(
        f"gh api {' '.join(arguments)} failed with {completed.returncode}: "
        f"{completed.stderr.strip()[:400]}"
    )


def _decoded(contents: Any) -> str | None:
    """The text of a contents-API file payload, or ``None`` if it is not one.

    The base64 body rather than the raw media type, so that every call in this module goes
    through one code path and a file that turned out to be a directory is a shape this
    function reports rather than a parse error somewhere else.
    """
    if not isinstance(contents, dict) or contents.get("type") != "file":
        return None
    try:
        return base64.b64decode(str(contents.get("content") or "")).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return None


def callers(slug: str, ref: str) -> tuple[tuple[Caller, ...], Finding | None]:
    """Every job in the repository that calls the platform's build workflow.

    The whole directory is listed and every file in it is read, rather than the
    conventional ``edullm-platform-build.yml`` being fetched by name. Five of the six
    registrations use that name and ``edullm-p1`` calls its file
    ``publish-research-image.yml``, so a check that assumed the convention would have
    reported the one repository this module exists for as having no caller at all.
    """
    try:
        listing = _github(f"repos/{slug}/contents/{WORKFLOWS_DIRECTORY}?ref={ref}")
    except GitHubUnreachable as error:
        return (), Finding("workflows_not_read", str(error), EXIT_UNUSABLE)
    if not isinstance(listing, list):
        return (), None

    found: list[Caller] = []
    for item in listing:
        name = str(item.get("name") or "")
        if not name.endswith((".yml", ".yaml")):
            continue
        try:
            contents = _github(f"repos/{slug}/contents/{item['path']}?ref={ref}")
        except GitHubUnreachable as error:
            return (), Finding("workflow_not_read", str(error), EXIT_UNUSABLE)
        body = _decoded(contents)
        if body is None:
            continue
        try:
            document = yaml.safe_load(body)
        except yaml.YAMLError:
            # GitHub is the authority on whether a workflow parses, and it has already
            # decided: a file it rejects runs nothing, which this reports as no caller
            # rather than as a check that broke.
            continue
        if not isinstance(document, dict):
            continue
        for job_id, definition in (document.get("jobs") or {}).items():
            if not isinstance(definition, dict):
                continue
            uses = str(definition.get("uses") or "")
            if uses.split("@", 1)[0] == BUILD_WORKFLOW:
                found.append(Caller(str(item["path"]), str(job_id), definition))
    return tuple(found), None


def contract_findings(entry: RegisteredRepository, slug: str, caller: Caller) -> tuple[Finding, ...]:
    """The parts of the §1.8 caller contract a file can be held to, all of them at once.

    Every one of these fails at build as an ``AssumeRole`` denial reading like a broken
    role ARN, so the value of catching them here is not that they are undetectable but
    that the detection currently costs a dispatch, a runner and somebody who can read a
    trust policy. All four are returned rather than the first, because a caller written
    from the wrong template is usually wrong in more than one way and finding out one per
    build is four builds.
    """
    where = f"{slug} {caller.path} job {caller.job_id!r}"
    findings: list[Finding] = []

    ref = str(caller.definition.get("uses") or "").partition("@")[2]
    if ref != REQUIRED_REF:
        findings.append(
            Finding(
                "build_caller_is_not_pinned_to_main",
                f"{entry.repository}: {where} calls the build workflow @{ref or '(no ref)'}. "
                f"The publisher role's trust policy matches job_workflow_ref with "
                f"StringEquals against @refs/heads/{REQUIRED_REF}, so this mints a claim IAM "
                "will never accept. This is the one place where pinning a ref makes things "
                "worse rather than better.",
                EXIT_CANNOT_PUBLISH,
            )
        )

    permissions = caller.definition.get("permissions")
    granted = permissions.get("id-token") if isinstance(permissions, dict) else None
    if granted != "write":
        findings.append(
            Finding(
                "build_caller_cannot_mint_a_token",
                f"{entry.repository}: {where} does not grant permissions.id-token: write. "
                "A called workflow can only downgrade the permissions it is handed, so the "
                "grant on the platform's publish job cannot create itself and no OIDC token "
                "is minted at all.",
                EXIT_CANNOT_PUBLISH,
            )
        )

    inputs = caller.definition.get("with")
    inputs = inputs if isinstance(inputs, dict) else {}

    if PUBLISHER_ROLE_VARIABLE not in str(inputs.get("publisher_role_arn") or ""):
        findings.append(
            Finding(
                "build_caller_names_no_publisher_role_variable",
                f"{entry.repository}: {where} does not pass publisher_role_arn from "
                f"vars.{PUBLISHER_ROLE_VARIABLE}. Whatever it passes instead, the platform "
                "cannot read it from here, and an input GitHub resolves to the empty string "
                "is an input GitHub drops.",
                EXIT_CANNOT_PUBLISH,
            )
        )

    declared = str(inputs.get("repository") or "")
    if declared != entry.repository:
        findings.append(
            Finding(
                "build_caller_names_another_registration",
                f"{entry.repository}: {where} passes repository: {declared!r}. The registry "
                "key is case-sensitive and is matched exactly, so this either publishes into "
                "another registration's ECR repository or is refused as "
                "unregistered_repository.",
                EXIT_CANNOT_PUBLISH,
            )
        )
    return tuple(findings)


def check_registration(
    entry: RegisteredRepository, organization: str
) -> tuple[Finding, ...]:
    """Whether this registration can publish, or every reason it cannot.

    An empty tuple is the pass.
    """
    try:
        repository = _github(f"repositories/{entry.github_repository_id}")
    except GitHubUnreachable as error:
        return (
            Finding("repository_not_read", f"{entry.repository}: {error}", EXIT_UNUSABLE),
        )
    if repository is None:
        return (
            Finding(
                "registered_repository_does_not_exist",
                f"{entry.repository} is registered as GitHub id "
                f"{entry.github_repository_id} and no repository with that id is readable, "
                "so nothing it was registered to do can happen.",
                EXIT_CANNOT_PUBLISH,
            ),
        )

    # Resolved by id and used by the name GitHub answers with, so a renamed repository is
    # still asked the right questions here. That the registration's own name is stale is a
    # finding tools/verify_registered_dockerfiles.py already makes, and making it twice
    # would put two pull requests on one rename.
    slug = str(repository.get("full_name") or f"{organization}/{entry.repository}")

    found, unreadable = callers(slug, entry.default_branch)
    if unreadable is not None:
        return (Finding(unreadable.reason, f"{entry.repository}: {unreadable.message}", EXIT_UNUSABLE),)

    if not found:
        return (
            Finding(
                "no_build_caller_workflow",
                f"{entry.repository}: no workflow on {slug}@{entry.default_branch} calls "
                f"{BUILD_WORKFLOW}. The registration holds an ECR repository and a widened "
                "publisher role and there is nothing anywhere that could use them, so every "
                "submission naming this repository is refused for a commit that published no "
                "image.",
                EXIT_CANNOT_PUBLISH,
            ),
        )

    findings: list[Finding] = []
    for caller in found:
        findings.extend(contract_findings(entry, slug, caller))

    # THE ONE THAT CATCHES WHAT THE FOUR ABOVE CANNOT SEE. Every static check above passed
    # on edullm-p1 the morning it could not publish, because the file was right and the
    # repository variable it named was never set. A variable is unreadable from here and a
    # green build is not: whatever the reason a registration has never produced an image,
    # this is where it surfaces, with no credential and no guess about which of the reasons
    # it was.
    workflow_paths = sorted({caller.path for caller in found})
    succeeded = 0
    unread = False
    for path in workflow_paths:
        # The file name, not the path. This endpoint takes a numeric workflow id or the
        # bare file name, and answers a path with a 404 -- which, counted as zero, is a
        # green repository reported as one that has never built. Asserted by
        # ``test_a_history_that_could_not_be_read_is_not_a_history_of_no_builds``.
        name = path.rsplit("/", 1)[-1]
        query = f"repos/{slug}/actions/workflows/{name}/runs?status=success&per_page=1"
        try:
            runs = _github(query)
        except GitHubUnreachable as error:
            unread = True
            findings.append(
                Finding("build_history_not_read", f"{entry.repository}: {error}", EXIT_UNUSABLE)
            )
            continue
        if runs is None:
            unread = True
            findings.append(
                Finding(
                    "build_history_not_read",
                    f"{entry.repository}: {slug} answered 404 for the run history of "
                    f"{name}, so whether it has ever published is unknown rather than no.",
                    EXIT_UNUSABLE,
                )
            )
            continue
        succeeded += int(runs.get("total_count") or 0)

    if succeeded == 0 and not unread:
        findings.append(
            Finding(
                "no_build_has_ever_succeeded",
                f"{entry.repository}: {', '.join(workflow_paths)} on {slug} has never "
                "completed successfully, so no image has ever been published from this "
                "registration and a submission naming any commit of it is refused. If the "
                "findings above are empty the caller file is correct and the cause is "
                f"outside it -- the {PUBLISHER_ROLE_VARIABLE} repository variable being "
                "unset renders it as the empty string, which GitHub drops, which the build "
                "reports as 'Could not load credentials from any providers'. Check "
                f"`gh variable list --repo {slug}` first.",
                EXIT_CANNOT_PUBLISH,
            )
        )
    return tuple(findings)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Check that every registered repository has a caller workflow that "
        "satisfies the build contract, and has published at least once."
    )
    parser.add_argument("--registry", type=Path, default=REGISTRY_PATH)
    parser.add_argument("--organization", default=DEFAULT_ORGANIZATION)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    options = build_parser().parse_args(argv)

    try:
        registry = load_yaml(Path(options.registry), RepositoryRegistry)
    except Exception as error:  # noqa: BLE001 - any unreadable registry is the same answer
        print(f"registry_unreadable: {error}", file=sys.stderr)
        return EXIT_UNUSABLE

    all_findings: list[Finding] = []
    for entry in subjects(registry):
        findings = check_registration(entry, str(options.organization))
        if not findings:
            print(f"{entry.repository} has published an image from a conforming caller.", flush=True)
            continue
        all_findings.extend(findings)
        for finding in findings:
            print(finding.reason, file=sys.stderr)
            print(finding.message, file=sys.stderr, flush=True)

    if not all_findings:
        print("Every registration has a caller that conforms and a build that has published.")
        return EXIT_OK

    # A definite finding outranks an unanswered question, the way its siblings rank them.
    # Somebody with one registration that cannot publish has to repair it whatever happened
    # to the others.
    if any(finding.code == EXIT_CANNOT_PUBLISH for finding in all_findings):
        return EXIT_CANNOT_PUBLISH
    return EXIT_UNUSABLE


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    raise SystemExit(main())
