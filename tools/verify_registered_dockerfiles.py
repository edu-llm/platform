"""That every registration in ``config/repositories.yaml`` describes a repository that is there.

**REGISTRATION RECORDS CLAIMS ABOUT SOMEBODY ELSE'S REPOSITORY AND USED TO CHECK NONE OF
THEM.** Four of a registration's eight fields are about this repository and are held against
it: the ECR repository is held against the template and the publisher role by
``tests/test_repository_registry.py``, and the base image and its digest are held against
``config/image-exceptions.yaml`` and read by the scan. The other four -- the repository name,
the GitHub id, the branch and ``dockerfile_path`` -- describe a tree nothing here owns, and
until this existed nothing anywhere asked whether any of them was true.
``tools/register_repository.py`` took them as arguments, wrote them into the file, and never
put the question to GitHub.

**This was shipped.** ``open-instruct-scored-rewards`` was registered on 2026-08-04 with
``dockerfile_path: .edullm/Dockerfile`` and no ``.edullm`` directory on ``main``. The
registration validated, merged, created its ECR repository, widened the publisher role and
reached the submission form's dropdown. Nothing went red, because nothing looked: the file is
read by ``docker build`` inside the caller repository's own workflow, and that repository had
no caller workflow either, so no build had run to discover it. A registration in that state
is worse than an absent one -- it is submittable, and what a submitter gets is a build
failure in somebody else's repository rather than a refusal that names the cause.

**That repository is out of that state and the paragraph above is history, not the registry
as it stands.** Both sentences were written in the present tense on 2026-08-05, seven hours
before the merge that ended them, which is the argument for the paragraph below rather than
an exception to it: what goes stale here is any sentence naming a repository, so the check
itself names none and no count of what it found is written down anywhere.

**THE REGISTRATION IS NOW ASKED THE SAME QUESTIONS BEFORE IT IS WRITTEN, AND THAT DOES NOT
MAKE THIS REDUNDANT.** ``tools/register_repository.py`` imports :func:`check_registration`
and refuses a registration whose claims are already false, so a repository in
``open-instruct-scored-rewards``'s state never reaches a pull request. What that cannot do is
see tomorrow. Every finding here is a claim that was true when it was written and stopped
being true afterwards -- a branch renamed, a Dockerfile deleted, a repository made private, a
caller workflow removed -- and a check that runs once at registration time is structurally
blind to all of it. One list of questions, asked in two places, for two different reasons.

**The subject is the registry and is never a list in here.** A check that restated which
repositories to ask about would go stale on the next registration in exactly the way the
thing it is checking did, and the seventh repository would be as unchecked as the fifth was.
``tests/test_registered_dockerfiles.py`` asserts that the questions this asks are derived
from the committed file, so a registration added tomorrow is asked about tomorrow.

**The repository is resolved by id rather than by name.** Both are in the registration and
only one of them is immutable. A repository renamed on GitHub leaves ``repository:`` stale
while ``github_repository_id`` stays right -- and the name is what every path in here is
built from, so resolving by id turns a rename from a confusing 404 about a missing Dockerfile
into a finding that says the repository moved.

**THE CALLER WORKFLOW IS ASKED ABOUT BECAUSE IT IS THE OTHER HALF OF THE SAME ABSENCE, AND
BECAUSE THE ANSWER IS EXACT.** The publisher role's trust policy pins ``job_workflow_ref``
to ``build-research-image.yml`` at ``refs/heads/main`` with ``StringEquals``, so a job that
does not go through that reusable workflow cannot obtain a credential for this account at
all. A ``uses:`` reference cannot be interpolated -- Actions resolves it before any
expression is evaluated -- so a repository with no literal mention of that path in
``.github/workflows`` is a repository from which no image can ever be published, whatever
else it holds. That is the same unbuildable-but-submittable state a missing Dockerfile
produces, arriving by the other door.

Like its siblings the first line of a finding is a machine-readable reason, and the two
non-zero exits are not interchangeable. Exit 1 says a registration points at something that
is not there and sends a reader to a repository; exit 2 says this check did not manage to
look and sends them to a session or a rate limit. A reader who cannot tell them apart goes
hunting a missing Dockerfile on the morning ``gh`` was logged out.
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

from edullm_platform.config import load_yaml
from edullm_platform.contracts.repository_registry import (
    RegisteredRepository,
    RepositoryRegistry,
)

PROJECT_ROOT: Final = Path(__file__).resolve().parents[1]
REGISTRY_PATH: Final = PROJECT_ROOT / "config" / "repositories.yaml"

#: Spelled the way ``tools/report_team_assignments.py`` and
#: ``tools/report_onboarding_readiness.py`` spell it, and overridable for the same reason.
DEFAULT_ORGANIZATION: Final = "edu-llm"

#: The reusable workflow a research repository has to call to publish anything, spelled
#: exactly as a ``uses:`` reference spells it. The publisher role's trust policy matches
#: ``job_workflow_ref`` against this path with ``StringEquals``, so this string is the whole
#: of what makes a repository able to publish, and a repository whose workflows never name it
#: cannot obtain a credential for this account however its build is written.
PLATFORM_BUILD_WORKFLOW: Final = (
    "edu-llm/platform/.github/workflows/build-research-image.yml"
)

#: EVERY CLAIM A REGISTRATION MAKES ABOUT SOMEBODY ELSE'S REPOSITORY THAT THIS CAN ANSWER.
#:
#: Named rather than counted, and exported rather than restated, because
#: ``tools/register_repository.py`` prints this list into the pull request body it opens so a
#: reviewer can see which claims were read off the repository and which were taken on trust.
#: A second copy of that list over there would be a sentence that drifts from the questions
#: actually asked, which is the whole defect this module exists about.
#: ``test_every_finding_this_can_report_falsifies_a_claim_it_declares`` holds each entry to
#: the reasons below it.
CLAIMS: Final[tuple[tuple[str, tuple[str, ...]], ...]] = (
    (
        "the GitHub id resolves to the repository the registration names",
        ("registered_repository_does_not_exist", "registered_repository_was_renamed"),
    ),
    (
        "the branch the registration names is on that repository",
        ("registered_branch_is_absent",),
    ),
    (
        "dockerfile_path is a file on that branch",
        ("registered_dockerfile_is_absent", "registered_dockerfile_is_not_a_file"),
    ),
    (
        "build_context is a directory on that branch",
        (
            "registered_build_context_is_absent",
            "registered_build_context_is_not_a_directory",
        ),
    ),
    (
        "a workflow on that branch calls the platform's reusable build",
        ("no_workflow_calls_the_platform_build",),
    ),
)

EXIT_OK: Final = 0

#: A registration names something the repository does not have. A definite answer about the
#: tree, which is why a missing file and a path that turned out to be a directory are both
#: here: the reader's next move is the same for both, and it is to go and look at a
#: repository.
EXIT_MISSING: Final = 1

#: Nothing was read, so nothing is claimed. Never reported as a pass, because a check that
#: cannot look is not a check that found nothing.
EXIT_UNUSABLE: Final = 2

__all__ = [
    "CLAIMS",
    "DEFAULT_ORGANIZATION",
    "EXIT_MISSING",
    "EXIT_OK",
    "EXIT_UNUSABLE",
    "PLATFORM_BUILD_WORKFLOW",
    "Finding",
    "GitHubUnreachable",
    "build_parser",
    "check_registration",
    "main",
    "subjects",
]


class GitHubUnreachable(Exception):
    """``gh`` did not answer, so the question was never put to GitHub."""


@dataclass(frozen=True)
class Finding:
    """One registration's answer, with the exit code it argues for."""

    reason: str
    message: str
    code: int


def subjects(registry: RepositoryRegistry) -> tuple[RegisteredRepository, ...]:
    """Every registration, in the order the file writes them.

    A function rather than an inlined attribute so that the test which holds the questions
    to the committed file has one thing to hold, and so that a future filter here -- an
    excused repository, say -- is a change somewhere a reader looks rather than a condition
    buried in the loop.
    """
    return registry.repositories


def _github(*arguments: str) -> Any:
    """One ``gh api`` call, parsed, telling "GitHub said no" apart from "GitHub was not asked".

    A non-zero exit carrying an HTTP status is an answer: 404 is the finding this tool
    exists to report, and it is returned rather than raised. Anything else -- no session, no
    network, a rate limit -- never established what is on the branch, and is raised so it
    leaves by the ``EXIT_UNUSABLE`` door instead of being reported as a missing file.
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


def _workflow_calling_the_platform_build(slug: str, branch: str) -> str | None:
    """The name of a workflow on this branch that calls the platform's build, if there is one.

    A literal text search rather than a parse, and that is exact rather than approximate.
    ``uses:`` is resolved by Actions before any expression is evaluated, so the reference
    cannot be interpolated, assembled or aliased -- the path is either spelled out in the
    file or that file does not call this workflow.

    A file this cannot read raises rather than counting as a file that does not call it,
    for the reason the module docstring gives about exit 2. The contents API returns no body
    for a blob over a megabyte, which is the one way that happens to a workflow.
    """
    listing = _github(f"repos/{slug}/contents/.github/workflows?ref={branch}")
    if not isinstance(listing, list):
        return None
    for item in listing:
        if not isinstance(item, dict) or item.get("type") != "file":
            continue
        if not str(item.get("name", "")).endswith((".yml", ".yaml")):
            continue
        blob = _github(f"repos/{slug}/contents/{item['path']}?ref={branch}")
        if not isinstance(blob, dict) or blob.get("encoding") != "base64":
            raise GitHubUnreachable(
                f"{item.get('path')} on {slug} answered with no readable body, so whether "
                "it calls the platform build was never established"
            )
        try:
            text = base64.b64decode(str(blob.get("content", ""))).decode("utf-8", "replace")
        except (binascii.Error, ValueError) as error:
            raise GitHubUnreachable(
                f"{item.get('path')} on {slug} did not decode: {error}"
            ) from error
        if PLATFORM_BUILD_WORKFLOW in text:
            return str(item["name"])
    return None


def check_registration(
    entry: RegisteredRepository, organization: str
) -> tuple[Finding, ...]:
    """Every claim in :data:`CLAIMS` this registration fails, or an empty tuple.

    EVERY ANSWERABLE CLAIM IS ANSWERED RATHER THAN THE FIRST FAILURE RETURNED, because the
    caller that refuses a registration is a person waiting on a dispatch and one refusal per
    attempt is three dispatches to learn three things. ``tools/register_repository.py``
    prints all of them at once for the reason ``AGENTS.md`` gives about ``edullm check``.

    The three file reads are independent and are all made. The two above them are not: a
    repository that does not resolve has no branch to ask about, and a branch that is not
    there makes every path on it absent for a reason that has nothing to do with the path.
    So those two short-circuit, and a rename is reported as a rename rather than as four
    missing files.
    """
    expected_slug = f"{organization}/{entry.repository}"
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
                f"{entry.github_repository_id} and no repository with that id is readable. "
                "The id is immutable, so this is either an id nobody has -- a digit wrong "
                "in the registration -- or a repository that was deleted or made private.",
                EXIT_MISSING,
            ),
        )

    actual_slug = str(repository.get("full_name") or "")
    if actual_slug.lower() != expected_slug.lower():
        return (
            Finding(
                "registered_repository_was_renamed",
                f"{entry.repository} is registered as GitHub id "
                f"{entry.github_repository_id}, which is {actual_slug}. The id still "
                "resolves, so nothing is broken in the publisher role, but every path "
                "derived from the name is stale -- including the ones this check would "
                "have asked about.",
                EXIT_MISSING,
            ),
        )

    try:
        branch = _github(f"repos/{actual_slug}/branches/{entry.default_branch}")
    except GitHubUnreachable as error:
        return (Finding("branch_not_read", f"{entry.repository}: {error}", EXIT_UNUSABLE),)

    if branch is None:
        return (
            Finding(
                "registered_branch_is_absent",
                f"{entry.repository} is registered against branch "
                f"{entry.default_branch!r} and {actual_slug} has no such branch. Every "
                "other claim in the registration is about a tree on that branch, so none "
                "of them was asked. The build resolves its commit against this branch, so "
                "until it exists nothing this registration names can be built.",
                EXIT_MISSING,
            ),
        )

    findings: list[Finding] = []
    for finding in (
        _check_dockerfile(entry, actual_slug),
        _check_build_context(entry, actual_slug),
        _check_caller_workflow(entry, actual_slug),
    ):
        if finding is not None:
            findings.append(finding)
    return tuple(findings)


def _check_dockerfile(entry: RegisteredRepository, slug: str) -> Finding | None:
    path = entry.dockerfile_path
    try:
        contents = _github(f"repos/{slug}/contents/{path}?ref={entry.default_branch}")
    except GitHubUnreachable as error:
        return Finding("dockerfile_not_read", f"{entry.repository}: {error}", EXIT_UNUSABLE)

    if contents is None:
        return Finding(
            "registered_dockerfile_is_absent",
            f"{entry.repository} is registered with dockerfile_path {path!r} and "
            f"{slug} has no such file on {entry.default_branch}. Either the "
            "registration names the wrong path, or the repository never received the image "
            "definition its registration promises. Until one of those is true the "
            "registration is submittable and unbuildable: the build fails inside the caller "
            "repository's own workflow rather than as a refusal naming the cause.",
            EXIT_MISSING,
        )

    # A directory answers with a JSON array rather than an object, which is how a
    # ``dockerfile_path`` naming a folder gets all the way here looking like a hit.
    if isinstance(contents, list) or contents.get("type") != "file":
        return Finding(
            "registered_dockerfile_is_not_a_file",
            f"{entry.repository} is registered with dockerfile_path {path!r} and "
            f"{slug} has something else of that name on {entry.default_branch}. "
            "docker build needs a file.",
            EXIT_MISSING,
        )
    return None


def _check_build_context(entry: RegisteredRepository, slug: str) -> Finding | None:
    """Whether ``build_context`` names a directory, which nothing asked until now.

    THE SAME FIELD AS ``dockerfile_path`` WEARING A DIFFERENT HAT, and it fails the same
    way: ``docker build`` is handed the context by the caller's own workflow, so a context
    naming a directory that is not there is a build failure in somebody else's repository
    rather than a refusal here. Every registration so far declares ``.``, which is the
    repository root and is proven by the repository resolving at all -- so that case is not
    a read. A registration is not obliged to declare ``.`` and the field exists because one
    day something will not.
    """
    context = entry.build_context
    if context == ".":
        return None
    try:
        contents = _github(f"repos/{slug}/contents/{context}?ref={entry.default_branch}")
    except GitHubUnreachable as error:
        return Finding(
            "build_context_not_read", f"{entry.repository}: {error}", EXIT_UNUSABLE
        )

    if contents is None:
        return Finding(
            "registered_build_context_is_absent",
            f"{entry.repository} is registered with build_context {context!r} and "
            f"{slug} has no such path on {entry.default_branch}. docker build is given "
            "this directory as the context by the caller's own workflow, so the build "
            "fails there rather than as a refusal naming the cause.",
            EXIT_MISSING,
        )

    if not isinstance(contents, list):
        return Finding(
            "registered_build_context_is_not_a_directory",
            f"{entry.repository} is registered with build_context {context!r} and "
            f"{slug} has a file of that name on {entry.default_branch}. A build context "
            "is a directory.",
            EXIT_MISSING,
        )
    return None


def _check_caller_workflow(entry: RegisteredRepository, slug: str) -> Finding | None:
    try:
        caller = _workflow_calling_the_platform_build(slug, entry.default_branch)
    except GitHubUnreachable as error:
        return Finding(
            "caller_workflow_not_read", f"{entry.repository}: {error}", EXIT_UNUSABLE
        )

    if caller is None:
        return Finding(
            "no_workflow_calls_the_platform_build",
            f"{entry.repository} is registered and no workflow on "
            f"{slug}'s {entry.default_branch} references "
            f"{PLATFORM_BUILD_WORKFLOW}. The publisher role's trust policy pins "
            "job_workflow_ref to that path, so no other route to a credential exists and "
            "no image can ever be published for this registration. The registration is "
            "submittable regardless, and a submitter gets a refusal about a digest that "
            "does not resolve rather than one naming the absent workflow.",
            EXIT_MISSING,
        )
    return None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Check that every registered repository is the repository its "
        "registration describes, on the branch it names."
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

    findings: list[Finding] = []
    for entry in subjects(registry):
        answers = check_registration(entry, str(options.organization))
        if not answers:
            print(
                f"{entry.repository} is on {entry.default_branch} with "
                f"{entry.dockerfile_path} and a workflow that calls the platform build.",
                flush=True,
            )
            continue
        findings.extend(answers)
        for finding in answers:
            print(finding.reason, file=sys.stderr)
            print(finding.message, file=sys.stderr, flush=True)

    if not findings:
        print("Every registration describes the repository it names.")
        return EXIT_OK

    # A definite finding outranks an unanswered question, the way verify_deployed_stacks.py
    # ranks them. Somebody with one broken registration has to repair it whatever happened
    # to the others, and the others are printed above rather than hidden behind the code.
    if any(finding.code == EXIT_MISSING for finding in findings):
        return EXIT_MISSING
    return EXIT_UNUSABLE


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    raise SystemExit(main())
