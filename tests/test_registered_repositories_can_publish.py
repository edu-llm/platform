"""That the publishability check asks about the registry, and answers about what it read.

``edullm-p1`` was registered on 2026-08-04 with an ECR repository, a widened publisher role,
a workload profile, a Dockerfile the sibling check confirms is there, and a caller workflow
whose every line is correct. Nobody set the ``AWS_ECR_PUBLISHER_ROLE_ARN`` repository
variable, so the build could not assume anything and its ECR repository held zero images
for two days with nothing on the audit schedule able to say so. The tool under test is how
the platform notices. These are the ways *it* can be wrong.

The first is the one that matters. A check whose subject is a list written inside it goes
stale on the next registration in exactly the way the registration process did, so the
subject is read from the committed registry and held in both directions.

The second is the one that is easy to get subtly wrong and impossible to see: the run
history endpoint takes a workflow's *file name* and answers a *path* with a 404. Counted as
zero, that 404 reports every green repository in the organization as one that has never
built -- which is what the first draft of this tool did, and what
``test_a_history_that_could_not_be_read_is_not_a_history_of_no_builds`` exists to keep
fixed. A finding that fires on everything is a finding nobody reads.

The third is finding the caller at all. Five of the six registrations call their file
``edullm-platform-build.yml`` and ``edullm-p1`` calls its one ``publish-research-image.yml``,
so a tool that fetched the conventional name would report the single repository this module
exists for as having no caller workflow, which is a true-sounding finding about the wrong
thing.

Nothing here reaches GitHub. ``subprocess.run`` is replaced, and the derivation test answers
from the committed registry itself.
"""

from __future__ import annotations

import base64
import json
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "tools"))

import verify_registered_repositories_can_publish as tool

from edullm_platform.config import load_yaml
from edullm_platform.contracts.repository_registry import RepositoryRegistry

REGISTRY_PATH = PROJECT_ROOT / "config" / "repositories.yaml"

#: The name five of the six registrations use. Never the name the tool looks for, which is
#: the subject of test_the_caller_is_found_by_what_it_calls_rather_than_by_its_filename.
CONVENTIONAL_CALLER = "edullm-platform-build.yml"


def committed_registry() -> RepositoryRegistry:
    return load_yaml(REGISTRY_PATH, RepositoryRegistry)


def completed(returncode: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["gh"], returncode=returncode, stdout=stdout, stderr=stderr
    )


def answer_ok(payload: object) -> subprocess.CompletedProcess[str]:
    return completed(0, stdout=json.dumps(payload))


def not_found() -> subprocess.CompletedProcess[str]:
    return completed(1, stderr="gh: Not Found (HTTP 404)")


def cannot_look() -> subprocess.CompletedProcess[str]:
    return completed(4, stderr="gh: To get started with GitHub CLI, please run: gh auth login")


def caller_document(repository: str, *, ref: str = "main", **overrides: Any) -> dict[str, Any]:
    """A caller workflow that satisfies every part of the contract, before overrides."""
    job: dict[str, Any] = {
        "permissions": {"contents": "read", "id-token": "write"},
        "uses": f"{tool.BUILD_WORKFLOW}@{ref}",
        "with": {
            "repository": repository,
            "publisher_role_arn": "${{ vars." + tool.PUBLISHER_ROLE_VARIABLE + " }}",
        },
    }
    job.update(overrides)
    return {"name": "Build eduLLM research image", "on": {"workflow_dispatch": None}, "jobs": {"publish": job}}


def file_payload(body: str) -> dict[str, object]:
    return {"type": "file", "content": base64.b64encode(body.encode("utf-8")).decode("ascii")}


def listing_payload(*names: str) -> list[dict[str, str]]:
    return [{"name": name, "path": f"{tool.WORKFLOWS_DIRECTORY}/{name}"} for name in names]


def install(
    monkeypatch: pytest.MonkeyPatch,
    responder: Callable[[str], subprocess.CompletedProcess[str]],
) -> list[str]:
    """Replace ``gh`` with ``responder``, and record the API path of every call made."""
    asked: list[str] = []

    def fake_run(command: Sequence[str], **_: object) -> subprocess.CompletedProcess[str]:
        assert list(command[:2]) == ["gh", "api"], command
        path = command[2]
        asked.append(path)
        return responder(path)

    monkeypatch.setattr(tool.subprocess, "run", fake_run)
    return asked


def everything_publishes(
    organization: str = "edu-llm",
    *,
    caller_name: str = CONVENTIONAL_CALLER,
    successes: int = 3,
) -> Callable[[str], subprocess.CompletedProcess[str]]:
    """Answer every question the tool can ask about the committed registry with a pass."""
    registry = committed_registry()
    by_id = {
        str(entry.github_repository_id): f"{organization}/{entry.repository}"
        for entry in registry.repositories
    }
    by_slug = {f"{organization}/{entry.repository}": entry.repository for entry in registry.repositories}
    by_branch = {
        f"{organization}/{entry.repository}": entry.default_branch
        for entry in registry.repositories
    }

    def responder(path: str) -> subprocess.CompletedProcess[str]:
        if path.startswith("repositories/"):
            return answer_ok({"full_name": by_id[path.removeprefix("repositories/")]})
        if "/actions/workflows/" in path:
            return answer_ok({"total_count": successes})
        slug = path.removeprefix("repos/").split("/contents/")[0]
        if path.endswith(f"/contents/{tool.WORKFLOWS_DIRECTORY}?ref={by_branch[slug]}"):
            return answer_ok(listing_payload(caller_name, "tests.yml"))
        if caller_name in path:
            return answer_ok(file_payload(yaml.safe_dump(caller_document(by_slug[slug]))))
        return answer_ok(file_payload("name: Tests\non: [push]\njobs: {}\n"))

    return responder


def one_repository(**overrides: Any) -> RepositoryRegistry:
    """A registry of exactly one entry, so a case is about one repository."""
    entry: dict[str, Any] = {
        "repository": "edullm-p1",
        "github_repository_id": 1314176548,
        "default_branch": "main",
        "ecr_repository": "sbsandbox-intern-edullm-p1",
        "base_image_repository": "docker.io/library/python",
        "base_image_digest": "sha256:" + "a" * 64,
        "dockerfile_path": ".edullm/Dockerfile",
        "build_context": ".",
    }
    entry.update(overrides)
    return RepositoryRegistry.model_validate({"repositories": [entry]})


def single(
    monkeypatch: pytest.MonkeyPatch,
    *,
    document: dict[str, Any] | None = None,
    caller_name: str = CONVENTIONAL_CALLER,
    successes: int = 3,
    runs: Callable[[], subprocess.CompletedProcess[str]] | None = None,
) -> list[str]:
    """Point the tool at one registration and answer for it, overriding one thing at a time."""
    monkeypatch.setattr(tool, "load_yaml", lambda *_, **__: one_repository())
    body = yaml.safe_dump(document if document is not None else caller_document("edullm-p1"))

    def responder(path: str) -> subprocess.CompletedProcess[str]:
        if path.startswith("repositories/"):
            return answer_ok({"full_name": "edu-llm/edullm-p1"})
        if "/actions/workflows/" in path:
            return runs() if runs is not None else answer_ok({"total_count": successes})
        if path.endswith(f"/contents/{tool.WORKFLOWS_DIRECTORY}?ref=main"):
            return answer_ok(listing_payload(caller_name))
        return answer_ok(file_payload(body))

    return install(monkeypatch, responder)


# ---------------------------------------------------------------------------------------
# The subject is the registry
# ---------------------------------------------------------------------------------------


def test_the_check_asks_about_every_registration_and_only_about_registrations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE ONE THAT KEEPS THIS TRUE FOR THE SEVENTH REPOSITORY. Mutation: write the list down.

    A check with its subject spelled out inside it would pass this module forever and stop
    describing the registry the day somebody registers something, which is the same defect
    as the registration process that produced ``edullm-p1``. Asserted in both directions: a
    registration nothing asks about, and a question about something no registration names.
    """
    asked = install(monkeypatch, everything_publishes())

    assert tool.main(["--registry", str(REGISTRY_PATH)]) == tool.EXIT_OK

    registry = committed_registry()
    assert {
        path.removeprefix("repositories/") for path in asked if path.startswith("repositories/")
    } == {str(entry.github_repository_id) for entry in registry.repositories}
    assert {path for path in asked if path.endswith(f"?ref={registry.repositories[0].default_branch}")} >= {
        f"repos/edu-llm/{entry.repository}/contents/{tool.WORKFLOWS_DIRECTORY}"
        f"?ref={entry.default_branch}"
        for entry in registry.repositories
    }


def test_the_branch_asked_about_is_the_one_the_registration_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mutation: ask about ``main`` whatever the entry says.

    A caller workflow only fires from the branch it is on, so a registration whose
    ``default_branch`` is not ``main`` is asked about the wrong tree by a tool that assumed.
    """
    monkeypatch.setattr(tool, "load_yaml", lambda *_, **__: one_repository(default_branch="release"))
    asked = install(
        monkeypatch,
        lambda path: (
            answer_ok({"full_name": "edu-llm/edullm-p1"})
            if path.startswith("repositories/")
            else answer_ok({"total_count": 1})
            if "/actions/workflows/" in path
            else answer_ok(listing_payload(CONVENTIONAL_CALLER))
            if path.endswith(f"{tool.WORKFLOWS_DIRECTORY}?ref=release")
            else answer_ok(file_payload(yaml.safe_dump(caller_document("edullm-p1"))))
        ),
    )

    assert tool.main([]) == tool.EXIT_OK
    assert all("ref=main" not in path for path in asked), asked


def test_the_caller_is_found_by_what_it_calls_rather_than_by_its_filename(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``edullm-p1``'s caller is ``publish-research-image.yml``, and it is the only one.

    Mutation: fetch ``.github/workflows/edullm-platform-build.yml`` by name. Five of the six
    registrations would agree, and the one repository this module was written for would be
    reported as having no caller workflow at all -- a finding that reads as true, sends
    somebody to write a file that is already there, and hides the real cause.
    """
    single(monkeypatch, caller_name="publish-research-image.yml")

    assert tool.main([]) == tool.EXIT_OK


# ---------------------------------------------------------------------------------------
# The defect this tool was written for
# ---------------------------------------------------------------------------------------


def test_a_registration_that_has_never_published_is_a_finding_that_names_the_variable(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """THE ONE THAT WOULD HAVE CAUGHT ``edullm-p1``, and the message is half of the value.

    Every static check passes here, because on the morning this happened every static check
    did pass: the caller file was correct and the repository variable it named was unset. The
    variable is unreadable from this repository -- Actions variables need collaborator access
    and this repository is asserted to hold no credential at all -- so the finding cannot
    name the value. It can and must name the variable, the symptom the build prints, and the
    command that settles it, or a reader lands where the original error left them.
    """
    single(monkeypatch, successes=0)

    assert tool.main([]) == tool.EXIT_CANNOT_PUBLISH
    captured = capsys.readouterr()
    assert "no_build_has_ever_succeeded" in captured.err
    assert tool.PUBLISHER_ROLE_VARIABLE in captured.err
    assert "Could not load credentials from any providers" in captured.err
    assert "gh variable list --repo edu-llm/edullm-p1" in captured.err


def test_a_history_that_could_not_be_read_is_not_a_history_of_no_builds(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """THE BUG THE FIRST DRAFT OF THIS TOOL SHIPPED WITH, kept fixed.

    The run-history endpoint takes a workflow's file name or its numeric id, and answers a
    *path* with a 404. The first draft passed the path, every repository answered 404, the
    404 was counted as zero, and all six registrations were reported as never having built --
    on a morning when five of them had published that week. A check that fires on everything
    is one nobody reads, and it would have buried the one true finding it was written for.
    """
    single(monkeypatch, runs=not_found)

    assert tool.main([]) == tool.EXIT_UNUSABLE
    captured = capsys.readouterr()
    assert "build_history_not_read" in captured.err
    assert "no_build_has_ever_succeeded" not in captured.err


def test_the_run_history_is_asked_for_by_file_name_rather_than_by_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other side of the test above, asserted on the request rather than on the answer."""
    asked = single(monkeypatch)

    assert tool.main([]) == tool.EXIT_OK
    history = [path for path in asked if "/actions/workflows/" in path]
    expected = (
        f"repos/edu-llm/edullm-p1/actions/workflows/{CONVENTIONAL_CALLER}"
        "/runs?status=success&per_page=1"
    )
    assert history == [expected]


# ---------------------------------------------------------------------------------------
# The caller contract, which a file can be held to
# ---------------------------------------------------------------------------------------


def test_a_registration_with_no_caller_workflow_cannot_publish(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A repository holding an ECR repository and a widened role and nothing that uses them."""
    monkeypatch.setattr(tool, "load_yaml", lambda *_, **__: one_repository())
    install(
        monkeypatch,
        lambda path: (
            answer_ok({"full_name": "edu-llm/edullm-p1"})
            if path.startswith("repositories/")
            else answer_ok(listing_payload("tests.yml"))
            if path.endswith(f"{tool.WORKFLOWS_DIRECTORY}?ref=main")
            else answer_ok(file_payload("name: Tests\non: [push]\njobs: {}\n"))
        ),
    )

    assert tool.main([]) == tool.EXIT_CANNOT_PUBLISH
    captured = capsys.readouterr()
    assert "no_build_caller_workflow" in captured.err
    # A repository with no caller has no run history to ask about, and asking would have
    # answered 404 and produced a second, contradictory finding.
    assert "no_build_has_ever_succeeded" not in captured.err


def test_a_directory_that_is_not_there_at_all_is_a_missing_caller_rather_than_a_crash(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A repository with no ``.github/workflows`` answers 404, which is an answer."""
    monkeypatch.setattr(tool, "load_yaml", lambda *_, **__: one_repository())
    install(
        monkeypatch,
        lambda path: (
            answer_ok({"full_name": "edu-llm/edullm-p1"})
            if path.startswith("repositories/")
            else not_found()
        ),
    )

    assert tool.main([]) == tool.EXIT_CANNOT_PUBLISH
    assert "no_build_caller_workflow" in capsys.readouterr().err


@pytest.mark.parametrize("ref", ["v1.2.3", "d" * 40, ""])
def test_a_caller_pinned_to_anything_but_main_is_refused(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    ref: str,
) -> None:
    """The trust policy matches ``job_workflow_ref`` against ``@refs/heads/main`` exactly.

    Pinning a ``uses:`` to a SHA is ordinarily the safer choice and here it mints a claim IAM
    will never accept, which arrives as an AssumeRole denial reading like a broken role ARN.
    """
    single(monkeypatch, document=caller_document("edullm-p1", ref=ref))

    assert tool.main([]) == tool.EXIT_CANNOT_PUBLISH
    assert "build_caller_is_not_pinned_to_main" in capsys.readouterr().err


def test_a_caller_that_cannot_mint_an_oidc_token_is_refused(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A called workflow can only downgrade the permissions it is handed."""
    single(
        monkeypatch,
        document=caller_document("edullm-p1", permissions={"contents": "read"}),
    )

    assert tool.main([]) == tool.EXIT_CANNOT_PUBLISH
    assert "build_caller_cannot_mint_a_token" in capsys.readouterr().err


def test_a_caller_that_reads_the_role_from_somewhere_else_is_refused(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The value is unreadable from here. The name in the call site is not.

    This is the one static check that would not have caught ``edullm-p1``, and it is here
    because it catches the neighbouring mistake: a caller wired to a variable nobody sets on
    purpose, which fails identically and for a reason that is in the file.
    """
    single(
        monkeypatch,
        document=caller_document(
            "edullm-p1", **{"with": {"repository": "edullm-p1", "publisher_role_arn": "${{ secrets.ROLE }}"}}
        ),
    )

    assert tool.main([]) == tool.EXIT_CANNOT_PUBLISH
    assert "build_caller_names_no_publisher_role_variable" in capsys.readouterr().err


def test_a_caller_declaring_another_registrations_key_is_refused(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Registration is case-sensitive, and a near miss publishes into the wrong repository."""
    single(monkeypatch, document=caller_document("EduLLM-P1"))

    assert tool.main([]) == tool.EXIT_CANNOT_PUBLISH
    assert "build_caller_names_another_registration" in capsys.readouterr().err


def test_every_way_one_caller_is_wrong_is_reported_at_once(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Mutation: return the first finding.

    A caller written from the wrong template is usually wrong in more than one way, and
    learning one per build is one dispatch and one runner per line of a file.
    """
    single(
        monkeypatch,
        document=caller_document(
            "wrong-key",
            ref="v1",
            permissions={"contents": "read"},
            **{"with": {"repository": "wrong-key", "publisher_role_arn": ""}},
        ),
    )

    assert tool.main([]) == tool.EXIT_CANNOT_PUBLISH
    reported = capsys.readouterr().err
    for reason in (
        "build_caller_is_not_pinned_to_main",
        "build_caller_cannot_mint_a_token",
        "build_caller_names_no_publisher_role_variable",
        "build_caller_names_another_registration",
    ):
        assert reason in reported, reason


# ---------------------------------------------------------------------------------------
# A check that could not look is not a check that found nothing
# ---------------------------------------------------------------------------------------


def test_being_unable_to_look_exits_unusable_rather_than_cannot_publish(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Exit 1 sends somebody to a repository setting. Exit 2 sends them to ``gh auth login``.

    A logged-out laptop reporting six registrations that cannot publish is six repositories
    somebody goes and reads on a morning when all six were fine.
    """
    install(monkeypatch, lambda _: cannot_look())

    assert tool.main(["--registry", str(REGISTRY_PATH)]) == tool.EXIT_UNUSABLE
    captured = capsys.readouterr()
    assert "repository_not_read" in captured.err
    assert "no_build_caller_workflow" not in captured.err
    assert "no_build_has_ever_succeeded" not in captured.err


def test_a_definite_finding_outranks_an_unanswered_question(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Somebody with one registration that cannot publish has to repair it regardless."""
    inner = everything_publishes()
    registry = committed_registry()
    first, second = registry.repositories[0], registry.repositories[1]

    def responder(path: str) -> subprocess.CompletedProcess[str]:
        if path == f"repositories/{first.github_repository_id}":
            return cannot_look()
        if path.startswith(f"repos/edu-llm/{second.repository}/") and "/actions/workflows/" in path:
            return answer_ok({"total_count": 0})
        return inner(path)

    install(monkeypatch, responder)

    assert tool.main(["--registry", str(REGISTRY_PATH)]) == tool.EXIT_CANNOT_PUBLISH


def test_a_registration_whose_repository_is_gone_is_reported_rather_than_skipped(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(tool, "load_yaml", lambda *_, **__: one_repository())
    install(monkeypatch, lambda path: not_found() if path.startswith("repositories/") else answer_ok(None))

    assert tool.main([]) == tool.EXIT_CANNOT_PUBLISH
    assert "registered_repository_does_not_exist" in capsys.readouterr().err


def test_an_unreadable_registry_is_unusable_rather_than_a_clean_pass(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A registry that will not parse has no registrations, which is not none broken."""
    install(monkeypatch, everything_publishes())
    broken = tmp_path / "repositories.yaml"
    broken.write_text("repositories: [\n", encoding="utf-8")

    assert tool.main(["--registry", str(broken)]) == tool.EXIT_UNUSABLE


def test_a_workflow_file_github_itself_cannot_parse_is_not_a_caller(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """GitHub has already decided: a file it will not parse runs nothing.

    Mutation: let the YAML error escape. A check that dies on somebody's broken unrelated
    workflow reports nothing about any of the six registrations.
    """
    monkeypatch.setattr(tool, "load_yaml", lambda *_, **__: one_repository())
    install(
        monkeypatch,
        lambda path: (
            answer_ok({"full_name": "edu-llm/edullm-p1"})
            if path.startswith("repositories/")
            else answer_ok(listing_payload("broken.yml"))
            if path.endswith(f"{tool.WORKFLOWS_DIRECTORY}?ref=main")
            else answer_ok(file_payload("jobs: [\n"))
        ),
    )

    assert tool.main([]) == tool.EXIT_CANNOT_PUBLISH
    assert "no_build_caller_workflow" in capsys.readouterr().err
