"""That the Dockerfile check asks about the registry, and answers about what it read.

``config/repositories.yaml`` gained a fifth entry whose ``dockerfile_path`` names a file
that is not in the repository it names, and the platform had no way to notice. The tool
under test is the way it notices. These are the ways *it* can be wrong.

The first is the one that matters and is the reason this module exists. A check whose
subject is a list written inside it goes stale on the next registration in exactly the way
the registration process did, and the sixth repository would be as unchecked as the fifth
was. So the first test reads the committed registry and holds the questions the tool
actually asks against it, in both directions -- a registration nothing asks about, and a
question about a repository nothing registered -- and neither is allowed to be silent.

The second is telling a missing file apart from a failure to look. A 404 is the finding;
no session, no network and a rate limit are not, and a check that reported them as a
missing Dockerfile would send somebody to open a pull request against a repository whose
file is sitting right there. The two exits are asserted separately, and each reason is
asserted absent from the other's output.

The third is a repository that was renamed. The registration carries both a name and an id
and only the id is immutable, so a rename leaves every name-derived path stale while the
publisher role goes on working. That is a different repair from a missing file and it says
so.

Nothing here reaches GitHub. ``subprocess.run`` is replaced, and the derivation test answers
from the committed registry itself, so the questions are checked against the real file
rather than against a fixture shaped to pass.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "tools"))

import verify_registered_dockerfiles as tool

from edullm_platform.config import load_yaml
from edullm_platform.contracts.repository_registry import (
    RepositoryRegistry,
)

REGISTRY_PATH = PROJECT_ROOT / "config" / "repositories.yaml"


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


def repository_payload(slug: str) -> dict[str, object]:
    return {"full_name": slug, "id": 1}


def file_payload() -> dict[str, object]:
    return {"type": "file", "name": "Dockerfile"}


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


def everything_is_there(organization: str = "edu-llm") -> Callable[[str], subprocess.CompletedProcess[str]]:
    """Answer every question the tool can ask about the committed registry with a pass."""
    by_id = {
        str(entry.github_repository_id): f"{organization}/{entry.repository}"
        for entry in committed_registry().repositories
    }

    def responder(path: str) -> subprocess.CompletedProcess[str]:
        if path.startswith("repositories/"):
            return answer_ok(repository_payload(by_id[path.removeprefix("repositories/")]))
        return answer_ok(file_payload())

    return responder


# ---------------------------------------------------------------------------------------
# The subject is the registry
# ---------------------------------------------------------------------------------------


def test_the_check_asks_about_every_registration_and_only_about_registrations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE ONE THAT KEEPS THIS TRUE FOR THE SIXTH REPOSITORY. Mutation: write the list down.

    A check with its subject spelled out inside it would pass this module forever and stop
    describing the registry the day somebody registers something. So the expectation is
    built from the committed file rather than from a literal, and it is asserted in both
    directions: a registration the tool never asks about, and a question about something no
    registration names. The first is the hole that let ``open-instruct-scored-rewards``
    through; the second is what stops this being satisfied by a tool that asks about
    everything in the organization.
    """
    asked = install(monkeypatch, everything_is_there())

    assert tool.main(["--registry", str(REGISTRY_PATH)]) == tool.EXIT_OK

    registry = committed_registry()
    assert {
        path.removeprefix("repositories/")
        for path in asked
        if path.startswith("repositories/")
    } == {str(entry.github_repository_id) for entry in registry.repositories}
    assert {path for path in asked if path.startswith("repos/")} == {
        f"repos/edu-llm/{entry.repository}/contents/{entry.dockerfile_path}"
        f"?ref={entry.default_branch}"
        for entry in registry.repositories
    }


def test_the_path_and_the_branch_asked_about_are_the_ones_the_registration_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mutation: ask about ``.edullm/Dockerfile`` on ``main`` whatever the entry says.

    Two of the eight fields decide what is looked for, and a tool that hardcoded either
    would have agreed with every registration written so far and lied about the first one
    that differs -- which is the same shape of defect as the convention that produced the
    broken entry. Asserted against an entry whose fields are deliberately not the defaults.
    """
    registry = RepositoryRegistry.model_validate(
        {
            "repositories": [
                {
                    "repository": "OLMo-core",
                    "github_repository_id": 1306868157,
                    "default_branch": "release",
                    "ecr_repository": "sbsandbox-intern-edullm-olmo-core",
                    "base_image_repository": "docker.io/library/python",
                    "base_image_digest": "sha256:" + "a" * 64,
                    "dockerfile_path": "images/gpu/Dockerfile",
                    "build_context": ".",
                }
            ]
        }
    )
    monkeypatch.setattr(tool, "load_yaml", lambda *_, **__: registry)
    asked = install(
        monkeypatch,
        lambda path: answer_ok(
            repository_payload("edu-llm/OLMo-core")
            if path.startswith("repositories/")
            else file_payload()
        ),
    )

    assert tool.main([]) == tool.EXIT_OK
    assert asked == [
        "repositories/1306868157",
        "repos/edu-llm/OLMo-core/contents/images/gpu/Dockerfile?ref=release",
    ]


# ---------------------------------------------------------------------------------------
# What it answers
# ---------------------------------------------------------------------------------------


def test_every_registration_resolving_is_a_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    install(monkeypatch, everything_is_there())

    assert tool.main(["--registry", str(REGISTRY_PATH)]) == tool.EXIT_OK


def test_an_absent_dockerfile_is_reported_against_the_registration_that_names_it(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """THE DEFECT THIS TOOL WAS WRITTEN FOR, as the tool sees it.

    ``open-instruct-scored-rewards`` is registered with ``.edullm/Dockerfile`` and has no
    ``.edullm`` directory on ``main``. The registration validated, merged, created its ECR
    repository and reached the submission form, and nothing anywhere went red.
    """
    inner = everything_is_there()

    def responder(path: str) -> subprocess.CompletedProcess[str]:
        if path.startswith("repos/edu-llm/open-instruct-scored-rewards/"):
            return not_found()
        return inner(path)

    install(monkeypatch, responder)

    assert tool.main(["--registry", str(REGISTRY_PATH)]) == tool.EXIT_MISSING
    captured = capsys.readouterr()
    assert "registered_dockerfile_is_absent" in captured.err
    assert "open-instruct-scored-rewards" in captured.err
    # The four that resolve are still reported as resolving, so one broken registration
    # does not read as a registry nobody can build any of.
    assert "OLMo-core has .edullm/Dockerfile on main." in captured.out


def test_a_path_that_is_a_directory_is_not_mistaken_for_a_file(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Mutation: treat any 200 as a hit.

    The contents API answers a directory with a JSON array rather than an object, so a
    ``dockerfile_path`` naming ``.edullm`` instead of ``.edullm/Dockerfile`` gets all the
    way to a 200 and would pass a check that only asked whether the call succeeded. What
    ``docker build`` needs is a file.
    """
    inner = everything_is_there()

    def responder(path: str) -> subprocess.CompletedProcess[str]:
        if path.startswith("repos/edu-llm/OLMo-core/"):
            return answer_ok([{"type": "file", "name": "Dockerfile"}])
        return inner(path)

    install(monkeypatch, responder)

    assert tool.main(["--registry", str(REGISTRY_PATH)]) == tool.EXIT_MISSING
    assert "registered_dockerfile_is_not_a_file" in capsys.readouterr().err


def test_a_renamed_repository_is_a_different_finding_from_a_missing_file(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Mutation: resolve by name, so a rename reads as a Dockerfile somebody deleted.

    The id is immutable and the name is not. A renamed repository still assumes the
    publisher role, because the trust policy matches the id -- so nothing is broken there,
    and the repair is to change one field rather than to add a file to a repository.
    """
    inner = everything_is_there()

    def responder(path: str) -> subprocess.CompletedProcess[str]:
        if path == "repositories/1306868157":
            return answer_ok(repository_payload("edu-llm/OLMo-core-archived"))
        return inner(path)

    install(monkeypatch, responder)

    assert tool.main(["--registry", str(REGISTRY_PATH)]) == tool.EXIT_MISSING
    captured = capsys.readouterr()
    assert "registered_repository_was_renamed" in captured.err
    assert "OLMo-core-archived" in captured.err
    assert "registered_dockerfile_is_absent" not in captured.err


def test_a_repository_that_no_longer_exists_is_told_apart_from_a_rename(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    inner = everything_is_there()

    def responder(path: str) -> subprocess.CompletedProcess[str]:
        return not_found() if path == "repositories/1306868157" else inner(path)

    install(monkeypatch, responder)

    assert tool.main(["--registry", str(REGISTRY_PATH)]) == tool.EXIT_MISSING
    captured = capsys.readouterr()
    assert "registered_repository_does_not_exist" in captured.err
    assert "registered_repository_was_renamed" not in captured.err


# ---------------------------------------------------------------------------------------
# A check that could not look is not a check that found nothing
# ---------------------------------------------------------------------------------------


def test_being_unable_to_look_exits_unusable_rather_than_missing(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The two non-zero exits send a reader to different places, so they are not the same.

    Exit 1 sends somebody to a repository to add a file. Exit 2 sends them to ``gh auth
    login``. A logged-out laptop reporting five missing Dockerfiles is five pull requests
    nobody needed, so neither reason is allowed to appear in the other's output.
    """
    install(monkeypatch, lambda _: cannot_look())

    assert tool.main(["--registry", str(REGISTRY_PATH)]) == tool.EXIT_UNUSABLE
    captured = capsys.readouterr()
    assert "repository_not_read" in captured.err
    assert "registered_dockerfile_is_absent" not in captured.err
    assert "registered_repository_does_not_exist" not in captured.err


def test_a_definite_finding_outranks_an_unanswered_question(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mutation: return the first finding's code, or let an unusable answer mask a missing file.

    Somebody with one broken registration has to repair it whatever happened to the others,
    and a run that found both should send them there rather than to their credentials.
    """
    inner = everything_is_there()
    registry = committed_registry()
    first, second = registry.repositories[0], registry.repositories[1]

    def responder(path: str) -> subprocess.CompletedProcess[str]:
        if path == f"repositories/{first.github_repository_id}":
            return cannot_look()
        if path.startswith(f"repos/edu-llm/{second.repository}/"):
            return not_found()
        return inner(path)

    install(monkeypatch, responder)

    assert tool.main(["--registry", str(REGISTRY_PATH)]) == tool.EXIT_MISSING


def test_an_unreadable_registry_is_unusable_rather_than_a_clean_pass(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A registry that will not parse has no registrations, which is not the same as none broken."""
    install(monkeypatch, everything_is_there())
    broken = tmp_path / "repositories.yaml"
    broken.write_text("repositories: [\n", encoding="utf-8")

    assert tool.main(["--registry", str(broken)]) == tool.EXIT_UNUSABLE
