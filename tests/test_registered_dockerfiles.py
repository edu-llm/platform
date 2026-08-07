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

import ast
import base64
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


def directory_payload() -> list[dict[str, object]]:
    return [{"type": "file", "name": "setup.py", "path": "setup.py"}]


def workflow_listing(name: str = "publish.yml") -> list[dict[str, object]]:
    return [{"type": "file", "name": name, "path": f".github/workflows/{name}"}]


def workflow_blob(text: str) -> dict[str, object]:
    """A contents-API answer for a workflow file, encoded the way GitHub encodes one."""
    return {
        "type": "file",
        "encoding": "base64",
        "content": base64.b64encode(text.encode("utf-8")).decode("ascii"),
    }


def a_workflow_that_calls_the_build() -> dict[str, object]:
    return workflow_blob(
        "jobs:\n"
        "  publish:\n"
        f"    uses: {tool.PLATFORM_BUILD_WORKFLOW}@main\n"
        "    with:\n"
        "      repository: whatever\n"
    )


def questions_about(entry: object, organization: str = "edu-llm") -> set[str]:
    """Every API path the tool has to ask about one registration for a clean pass.

    Derived from the entry rather than written out, so the both-directions assertion below
    stays an assertion about the committed registry and not about a list in this module --
    which is the whole property that test exists to protect.
    """
    slug = f"{organization}/{entry.repository}"  # type: ignore[attr-defined]
    branch = entry.default_branch  # type: ignore[attr-defined]
    asked = {
        f"repositories/{entry.github_repository_id}",  # type: ignore[attr-defined]
        f"repos/{slug}/branches/{branch}",
        f"repos/{slug}/contents/{entry.dockerfile_path}?ref={branch}",  # type: ignore[attr-defined]
        f"repos/{slug}/contents/.github/workflows?ref={branch}",
        f"repos/{slug}/contents/.github/workflows/publish.yml?ref={branch}",
    }
    # `.` is the repository root, which the repository resolving already establishes, so it
    # is deliberately not a read. Every registration declares it today and the field exists
    # because one day one will not.
    if entry.build_context != ".":  # type: ignore[attr-defined]
        asked.add(f"repos/{slug}/contents/{entry.build_context}?ref={branch}")  # type: ignore[attr-defined]
    return asked


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
    """Answer every question the tool can ask about the committed registry with a pass.

    The order of the branches matters: a workflow file's path and a Dockerfile's path both
    read ``repos/<slug>/contents/...``, so the two workflow cases are matched before the
    generic one rather than after it.
    """
    by_id = {
        str(entry.github_repository_id): f"{organization}/{entry.repository}"
        for entry in committed_registry().repositories
    }
    contexts = {
        entry.build_context
        for entry in committed_registry().repositories
        if entry.build_context != "."
    }

    def responder(path: str) -> subprocess.CompletedProcess[str]:
        if path.startswith("repositories/"):
            return answer_ok(repository_payload(by_id[path.removeprefix("repositories/")]))
        if "/branches/" in path:
            return answer_ok({"name": path.rsplit("/", 1)[-1]})
        if path.endswith("contents/.github/workflows") or "contents/.github/workflows?" in path:
            return answer_ok(workflow_listing())
        if "contents/.github/workflows/" in path:
            return answer_ok(a_workflow_that_calls_the_build())
        if any(f"contents/{context}?" in path for context in contexts):
            return answer_ok(directory_payload())
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
    # Both directions over the whole question set rather than over the Dockerfile alone,
    # because the check grew four more claims and a registration can now be under-asked in
    # four more ways. Equality is what makes it two assertions in one: a registration no
    # question mentions, and a question about something no registration names.
    expected: set[str] = set()
    for entry in registry.repositories:
        expected |= questions_about(entry)
    assert set(asked) == expected


def test_the_path_and_the_branch_asked_about_are_the_ones_the_registration_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mutation: ask about ``.edullm/Dockerfile`` on ``main`` whatever the entry says.

    Three of the eight fields decide what is looked for, and a tool that hardcoded any of
    them would have agreed with every registration written so far and lied about the first
    one that differs -- which is the same shape of defect as the convention that produced the
    broken entry. Asserted against an entry whose branch, Dockerfile path and build context
    are all deliberately not the defaults, and the build context is asserted here because
    every committed registration declares ``.`` and so exercises the branch that skips it.
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
                    "build_context": "src",
                }
            ]
        }
    )
    monkeypatch.setattr(tool, "load_yaml", lambda *_, **__: registry)

    def responder(path: str) -> subprocess.CompletedProcess[str]:
        if path.startswith("repositories/"):
            return answer_ok(repository_payload("edu-llm/OLMo-core"))
        if "/branches/" in path:
            return answer_ok({"name": "release"})
        if "contents/.github/workflows?" in path:
            return answer_ok(workflow_listing())
        if "contents/.github/workflows/" in path:
            return answer_ok(a_workflow_that_calls_the_build())
        if "contents/src?" in path:
            return answer_ok(directory_payload())
        return answer_ok(file_payload())

    asked = install(monkeypatch, responder)

    assert tool.main([]) == tool.EXIT_OK
    assert set(asked) == {
        "repositories/1306868157",
        "repos/edu-llm/OLMo-core/branches/release",
        "repos/edu-llm/OLMo-core/contents/images/gpu/Dockerfile?ref=release",
        "repos/edu-llm/OLMo-core/contents/src?ref=release",
        "repos/edu-llm/OLMo-core/contents/.github/workflows?ref=release",
        "repos/edu-llm/OLMo-core/contents/.github/workflows/publish.yml?ref=release",
    }


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

    ``open-instruct-scored-rewards`` was registered with ``.edullm/Dockerfile`` and no
    ``.edullm`` directory on ``main``. The registration validated, merged, created its ECR
    repository and reached the submission form, and nothing anywhere went red.

    That repository has had both since 2026-08-05, so the name below is the case this was
    written from rather than a live finding. It stays because the responder supplies the
    404 itself: what is under test is what the tool does with one, not what any repository
    currently holds.
    """
    inner = everything_is_there()
    absent = next(
        entry
        for entry in committed_registry().repositories
        if entry.repository == "open-instruct-scored-rewards"
    )

    # THE DOCKERFILE PATH ALONE RATHER THAN EVERY PATH ON THAT REPOSITORY, which it used to
    # be. The check asks about the branch before it asks about anything on the branch, so a
    # responder refusing all of them now demonstrates the branch finding instead -- a
    # different defect with a different repair, asserted on its own above.
    def responder(path: str) -> subprocess.CompletedProcess[str]:
        if path == (
            f"repos/edu-llm/{absent.repository}/contents/{absent.dockerfile_path}"
            f"?ref={absent.default_branch}"
        ):
            return not_found()
        return inner(path)

    install(monkeypatch, responder)

    assert tool.main(["--registry", str(REGISTRY_PATH)]) == tool.EXIT_MISSING
    captured = capsys.readouterr()
    assert "registered_dockerfile_is_absent" in captured.err
    assert "open-instruct-scored-rewards" in captured.err
    # The registrations that resolve are still reported as resolving, so one broken
    # registration does not read as a registry nobody can build any of. Counted nowhere
    # here, for the reason the module docstring gives: a number would go stale on the next
    # registration exactly as the sentence above it did.
    assert "OLMo-core is on main with .edullm/Dockerfile" in captured.out


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


def test_a_branch_that_is_gone_is_reported_as_the_branch_and_not_as_four_missing_files(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Mutation: ask for the contents and let the 404s speak.

    A registration whose branch has been renamed or deleted answers 404 for every path on
    it, so a check with no branch question reports a missing Dockerfile, a missing build
    context and an absent caller workflow -- three repairs, none of them the repair. The
    branch is asked first and short-circuits, so the finding names the one thing that is
    actually wrong.
    """
    inner = everything_is_there()
    entry = committed_registry().repositories[0]

    def responder(path: str) -> subprocess.CompletedProcess[str]:
        if path == f"repos/edu-llm/{entry.repository}/branches/{entry.default_branch}":
            return not_found()
        return inner(path)

    install(monkeypatch, responder)

    assert tool.main(["--registry", str(REGISTRY_PATH)]) == tool.EXIT_MISSING
    captured = capsys.readouterr()
    assert "registered_branch_is_absent" in captured.err
    assert entry.default_branch in captured.err
    assert "registered_dockerfile_is_absent" not in captured.err
    assert "no_workflow_calls_the_platform_build" not in captured.err


def test_a_repository_no_workflow_of_which_calls_the_platform_build_is_a_finding(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """THE OTHER HALF OF THE ABSENCE THAT PRODUCED THIS TOOL, AND IT IS AN EXACT QUESTION.

    ``open-instruct-scored-rewards`` had no Dockerfile *and* no caller workflow, and the
    second is why the first went unnoticed: the Dockerfile is read by ``docker build`` inside
    the caller's own workflow, so with no caller nothing ever ran to discover it. A
    repository whose Dockerfile is perfect and whose workflows never mention the reusable
    build is in the same unbuildable-but-submittable state, arriving by the other door.

    The answer is exact rather than heuristic. The publisher role's trust policy matches
    ``job_workflow_ref`` against that path with ``StringEquals``, and a ``uses:`` reference
    cannot be interpolated, so a branch with no literal mention of it is a branch from which
    no credential for this account can be obtained.
    """
    inner = everything_is_there()
    entry = committed_registry().repositories[0]

    def responder(path: str) -> subprocess.CompletedProcess[str]:
        if f"repos/edu-llm/{entry.repository}/contents/.github/workflows/" in path:
            return answer_ok(workflow_blob("jobs:\n  test:\n    runs-on: ubuntu-latest\n"))
        return inner(path)

    install(monkeypatch, responder)

    assert tool.main(["--registry", str(REGISTRY_PATH)]) == tool.EXIT_MISSING
    captured = capsys.readouterr()
    assert "no_workflow_calls_the_platform_build" in captured.err
    assert tool.PLATFORM_BUILD_WORKFLOW in captured.err
    assert entry.repository in captured.err


def test_a_repository_with_no_workflows_directory_at_all_is_the_same_finding(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    inner = everything_is_there()
    entry = committed_registry().repositories[0]

    def responder(path: str) -> subprocess.CompletedProcess[str]:
        if "contents/.github/workflows?" in path and entry.repository in path:
            return not_found()
        return inner(path)

    install(monkeypatch, responder)

    assert tool.main(["--registry", str(REGISTRY_PATH)]) == tool.EXIT_MISSING
    assert "no_workflow_calls_the_platform_build" in capsys.readouterr().err


def test_a_workflow_body_that_could_not_be_read_is_unusable_rather_than_no_caller(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Mutation: treat an unreadable body as a file that does not call the build.

    The contents API answers a blob over a megabyte with no body at all, and a check that
    counted that as "does not call the platform build" would report a repository as unable to
    publish on the strength of a file it never read. That is the module's own rule about exit
    2 applied to the one read here that can come back empty rather than absent.
    """
    inner = everything_is_there()
    entry = committed_registry().repositories[0]

    def responder(path: str) -> subprocess.CompletedProcess[str]:
        if f"repos/edu-llm/{entry.repository}/contents/.github/workflows/" in path:
            return answer_ok({"type": "file", "encoding": "none", "content": ""})
        return inner(path)

    install(monkeypatch, responder)

    assert tool.main(["--registry", str(REGISTRY_PATH)]) == tool.EXIT_UNUSABLE
    captured = capsys.readouterr()
    assert "caller_workflow_not_read" in captured.err
    assert "no_workflow_calls_the_platform_build" not in captured.err


def test_a_build_context_that_is_not_a_directory_is_a_finding_of_its_own(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The eighth field, which nothing held against anything until now.

    ``build_context`` is handed to ``docker build`` by the caller's own workflow exactly as
    ``dockerfile_path`` is, so it fails in the same place and reads the same way. A file of
    that name is told apart from nothing of that name because the repairs differ.
    """
    registry = RepositoryRegistry.model_validate(
        {
            "repositories": [
                {
                    "repository": "OLMo-core",
                    "github_repository_id": 1306868157,
                    "default_branch": "main",
                    "ecr_repository": "sbsandbox-intern-edullm-olmo-core",
                    "base_image_repository": "docker.io/library/python",
                    "base_image_digest": "sha256:" + "a" * 64,
                    "dockerfile_path": ".edullm/Dockerfile",
                    "build_context": "context",
                }
            ]
        }
    )
    monkeypatch.setattr(tool, "load_yaml", lambda *_, **__: registry)

    def responder(path: str) -> subprocess.CompletedProcess[str]:
        if path.startswith("repositories/"):
            return answer_ok(repository_payload("edu-llm/OLMo-core"))
        if "/branches/" in path:
            return answer_ok({"name": "main"})
        if "contents/.github/workflows?" in path:
            return answer_ok(workflow_listing())
        if "contents/.github/workflows/" in path:
            return answer_ok(a_workflow_that_calls_the_build())
        return answer_ok(file_payload())

    install(monkeypatch, responder)

    assert tool.main([]) == tool.EXIT_MISSING
    assert "registered_build_context_is_not_a_directory" in capsys.readouterr().err


def test_every_independent_claim_is_answered_rather_than_the_first_failure_returned(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Mutation: return on the first finding.

    ``tools/register_repository.py`` refuses a registration on these and the person reading
    the refusal is waiting on a workflow dispatch, so one finding per dispatch is three
    dispatches to learn three things. The two claims above the file reads do short-circuit,
    for the reason the branch test gives; these three are independent and are all asked.
    """
    inner = everything_is_there()
    entry = committed_registry().repositories[0]
    slug = f"repos/edu-llm/{entry.repository}"

    def responder(path: str) -> subprocess.CompletedProcess[str]:
        if path == f"{slug}/contents/{entry.dockerfile_path}?ref={entry.default_branch}":
            return not_found()
        if f"{slug}/contents/.github/workflows/" in path:
            return answer_ok(workflow_blob("jobs: {}\n"))
        return inner(path)

    install(monkeypatch, responder)

    assert tool.main(["--registry", str(REGISTRY_PATH)]) == tool.EXIT_MISSING
    captured = capsys.readouterr()
    assert "registered_dockerfile_is_absent" in captured.err
    assert "no_workflow_calls_the_platform_build" in captured.err


def test_every_finding_this_can_report_falsifies_a_claim_it_declares() -> None:
    """Reads BOTH sides. Mutation: add a finding and leave CLAIMS alone.

    ``CLAIMS`` is printed into the pull request body ``tools/register_repository.py`` opens,
    so it is what a reviewer is told was checked. A reason the tool can emit that no claim
    names is a check nobody is told about, and a claim with no reason behind it is a sentence
    promising a check that does not exist. Both directions, over the module's own source, so
    neither can be satisfied by editing this test.
    """
    declared = {reason for _, reasons in tool.CLAIMS for reason in reasons}
    tree = ast.parse(Path(tool.__file__).read_text(encoding="utf-8"))
    emitted = {
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "Finding"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    }
    # The `*_not_read` reasons are the exit-2 door rather than a falsified claim: they say the
    # question was never put, which is why they carry EXIT_UNUSABLE and are excluded here
    # rather than listed as claims nobody would know how to repair.
    falsifying = {reason for reason in emitted if not reason.endswith("_not_read")}

    assert falsifying == declared
    assert emitted - falsifying, "the unusable door has to stay reachable"


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
