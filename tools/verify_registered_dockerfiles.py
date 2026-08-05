"""That every registration in ``config/repositories.yaml`` names a Dockerfile that is there.

A registration is eight fields, and seven of them are checked by something. The repository
name, the GitHub id and the ECR repository are held against the publisher role and the ECR
template by ``tests/test_repository_registry.py``. The base image and its digest are held
against ``config/image-exceptions.yaml`` and read by the scan. ``dockerfile_path`` is held
against nothing at all: ``tools/register_repository.py`` takes it as a required argument,
writes it into the file, and never asks the repository whether it has one. Its own help text
says "conventionally .edullm/Dockerfile", which is the convention four registrations follow
and the fifth was written from.

**This was shipped.** ``open-instruct-scored-rewards`` was registered with
``dockerfile_path: .edullm/Dockerfile`` and has no ``.edullm`` directory on ``main``. The
registration validated, merged, created its ECR repository, widened the publisher role and
reached the submission form's dropdown. Nothing went red, because nothing looks: the file is
read by ``docker build`` inside the caller repository's own workflow, and that repository has
no caller workflow either, so no build has ever run to discover it. A registration in that
state is worse than an absent one -- it is submittable, and what a submitter gets is a build
failure in somebody else's repository rather than a refusal that names the cause.

**The subject is the registry and is never a list in here.** A check that restated which
repositories to ask about would go stale on the next registration in exactly the way the
thing it is checking did, and the sixth repository would be as unchecked as the fifth.
``tests/test_registered_dockerfiles.py`` asserts that the questions this asks are derived
from the committed file, so a registration added tomorrow is asked about tomorrow.

**The repository is resolved by id rather than by name.** Both are in the registration and
only one of them is immutable. A repository renamed on GitHub leaves ``repository:`` stale
while ``github_repository_id`` stays right -- and the name is what every path in here is
built from, so resolving by id turns a rename from a confusing 404 about a missing Dockerfile
into a finding that says the repository moved.

Like its siblings the first line of a finding is a machine-readable reason, and the two
non-zero exits are not interchangeable. Exit 1 says a registration points at something that
is not there and sends a reader to a repository; exit 2 says this check did not manage to
look and sends them to a session or a rate limit. A reader who cannot tell them apart goes
hunting a missing Dockerfile on the morning ``gh`` was logged out.
"""

from __future__ import annotations

import argparse
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
    "DEFAULT_ORGANIZATION",
    "EXIT_MISSING",
    "EXIT_OK",
    "EXIT_UNUSABLE",
    "Finding",
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


def check_registration(entry: RegisteredRepository, organization: str) -> Finding | None:
    """Whether this registration's Dockerfile is on the branch it names, or why not.

    ``None`` is the pass. Everything else is a finding carrying the exit code it argues for,
    so that a caller which collects several of them can rank a definite answer above an
    unanswered question without re-deriving which was which.
    """
    expected_slug = f"{organization}/{entry.repository}"
    try:
        repository = _github(f"repositories/{entry.github_repository_id}")
    except GitHubUnreachable as error:
        return Finding("repository_not_read", f"{entry.repository}: {error}", EXIT_UNUSABLE)

    if repository is None:
        return Finding(
            "registered_repository_does_not_exist",
            f"{entry.repository} is registered as GitHub id {entry.github_repository_id} "
            "and no repository with that id is readable. The id is immutable, so this is a "
            "repository that was deleted or made private rather than one that was renamed.",
            EXIT_MISSING,
        )

    actual_slug = str(repository.get("full_name") or "")
    if actual_slug.lower() != expected_slug.lower():
        return Finding(
            "registered_repository_was_renamed",
            f"{entry.repository} is registered as GitHub id {entry.github_repository_id}, "
            f"which is now {actual_slug}. The id still resolves, so nothing is broken in the "
            "publisher role, but every path derived from the name is stale -- including the "
            "one this check would have asked about.",
            EXIT_MISSING,
        )

    path = entry.dockerfile_path
    try:
        contents = _github(
            f"repos/{actual_slug}/contents/{path}?ref={entry.default_branch}"
        )
    except GitHubUnreachable as error:
        return Finding("dockerfile_not_read", f"{entry.repository}: {error}", EXIT_UNUSABLE)

    if contents is None:
        return Finding(
            "registered_dockerfile_is_absent",
            f"{entry.repository} is registered with dockerfile_path {path!r} and "
            f"{actual_slug} has no such file on {entry.default_branch}. Either the "
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
            f"{actual_slug} has something else of that name on {entry.default_branch}. "
            "docker build needs a file.",
            EXIT_MISSING,
        )
    return None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Check that every registered repository has the Dockerfile its "
        "registration names, on the branch it names."
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
        finding = check_registration(entry, str(options.organization))
        if finding is None:
            print(
                f"{entry.repository} has {entry.dockerfile_path} on {entry.default_branch}.",
                flush=True,
            )
            continue
        findings.append(finding)
        print(finding.reason, file=sys.stderr)
        print(finding.message, file=sys.stderr, flush=True)

    if not findings:
        print("Every registration names a Dockerfile that is on the branch it names.")
        return EXIT_OK

    # A definite finding outranks an unanswered question, the way verify_deployed_stacks.py
    # ranks them. Somebody with one broken registration has to repair it whatever happened
    # to the others, and the others are printed above rather than hidden behind the code.
    if any(finding.code == EXIT_MISSING for finding in findings):
        return EXIT_MISSING
    return EXIT_UNUSABLE


if __name__ == "__main__":  # pragma: no cover - exercised through main()
    raise SystemExit(main())
