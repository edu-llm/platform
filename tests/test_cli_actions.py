"""The seams the CLI sits on, and the readings it makes of somebody else's output.

Three kinds of thing are here. The seams: a name this package restates rather than imports,
held to the module that owns it. The readings: GitHub's status pair turned into the four
words a submitter needs, and a job log turned back into the report a workflow wrote. And
the verbs that are settled and unbuilt, which have to answer with a plan rather than with
"invalid choice".
"""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from edullm_platform.cli.actions import (
    CANCEL_WORKFLOW,
    PLATFORM_REPOSITORY,
    SUBMIT_WORKFLOW,
    elapsed_said,
    read_report_sections,
    submission_state,
)
from edullm_platform.cli.configuration import (
    CONFIG_DIRECTORY_VARIABLE,
    ConfigurationUnreadableError,
    find_config_directory,
)
from edullm_platform.cli.main import EXIT_UNUSABLE, NOT_BUILT_YET
from edullm_platform.cli.workspace import SubprocessRunner, read_git_facts
from edullm_platform.phase0_gate import EXPECTED_GITHUB_ORG, EXPECTED_GITHUB_REPOSITORY
from tests.cli_support import PROJECT_ROOT, FakeRunner, invoke


def test_the_repository_this_dispatches_into_is_the_one_phase0_expects() -> None:
    """The seam test the copy in ``actions.py`` says exists.

    Mutation: change either side. ``PLATFORM_REPOSITORY`` is restated rather than imported
    so that ``--help`` does not pull the evidence and criteria graph, and the price of a
    restatement is that it drifts -- this is what stops it drifting silently.
    """
    assert PLATFORM_REPOSITORY == f"{EXPECTED_GITHUB_ORG}/{EXPECTED_GITHUB_REPOSITORY}"


@pytest.mark.parametrize("workflow", [SUBMIT_WORKFLOW, CANCEL_WORKFLOW])
def test_both_workflows_this_drives_are_files_in_this_repository(workflow: str) -> None:
    """Mutation: rename either without renaming it here.

    ``gh workflow run`` answers "could not find any workflows named X" and exits non-zero,
    which reads as a permissions or an authentication problem rather than as a typo -- and
    the submission role's trust policy pins the submission one by path besides, so renaming
    it silently revokes every submission.
    """
    assert (PROJECT_ROOT / ".github" / "workflows" / workflow).is_file()


@pytest.mark.parametrize(
    ("run", "expected"),
    [
        ({"status": "waiting", "conclusion": None}, "PENDING_APPROVAL"),
        ({"status": "queued", "conclusion": None}, "DISPATCHED"),
        ({"status": "in_progress", "conclusion": None}, "COMPILING"),
        ({"status": "completed", "conclusion": "success"}, "SUBMITTED"),
        ({"status": "completed", "conclusion": "failure"}, "REFUSED"),
        ({"status": "completed", "conclusion": "cancelled"}, "CANCELLED"),
    ],
)
def test_githubs_status_pair_reads_as_the_thing_it_means_to_a_submitter(
    run: dict[str, object], expected: str
) -> None:
    """``waiting`` is the one that matters and the one GitHub names least helpfully.

    Mutation: report GitHub's own words. "waiting" and "in_progress" are facts about a
    workflow run; "a lead has not tapped yet" and "your submission is being compiled" are
    the facts about the submission, and only one pair of those tells a researcher whether
    to go and message somebody.
    """
    assert submission_state(run) == expected


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(0, "0s"), (38, "38s"), (59, "59s"), (60, "1m"), (240, "4m"), (4271, "1h11m")],
)
def test_a_wait_is_said_the_way_the_transcripts_say_it(seconds: int, expected: str) -> None:
    """Mutation: print minutes past an hour.

    ``188m`` is a number the reader has to divide, and what they are dividing it to find out
    is whether this has been sitting there long enough to go and ask somebody.
    """
    now = datetime(2026, 8, 24, 12, 0, tzinfo=UTC)
    since = datetime.fromtimestamp(now.timestamp() - seconds, UTC)

    assert elapsed_said(since, now=now) == expected


def test_the_report_a_workflow_teed_into_its_summary_is_recovered_from_the_job_log() -> None:
    """The whole reason ``status`` and ``logs`` can work at all.

    A step summary is exposed by no REST endpoint -- ``submit-run.yml`` says so and uploads
    an artifact to work around it -- and ``cancel-run.yml`` writes every block of its report
    through ``tee``, so the same bytes are in the log. Mutation: stop stripping the three
    columns ``gh run view --log`` prefixes, and the markdown comes back unreadable.
    """
    log = (
        "cancel\tSet up job\t2099-01-01T00:00:00.0000000Z Current runner version\n"
        "cancel\tSay what the run is doing\t2099-01-01T00:00:01.0000000Z ## run_0198\n"
        "cancel\tSay what the run is doing\t2099-01-01T00:00:02.0000000Z | Status | X |\n"
        "cancel\tShow the last fifty\t2099-01-01T00:00:03.0000000Z ### The last lines\n"
        "cancel\tShow the last fifty\t2099-01-01T00:00:04.0000000Z step 200 loss 5.9\n"
    )

    described = read_report_sections(log, ("run_0198",))
    tailed = read_report_sections(log, ("The last lines",))

    assert described == "## run_0198\n| Status | X |"
    assert tailed == "### The last lines\nstep 200 loss 5.9"
    assert "Current runner version" not in described


@pytest.mark.parametrize("verb", sorted(NOT_BUILT_YET))
def test_a_settled_verb_that_is_unbuilt_says_so_rather_than_being_absent(
    verb: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: leave them out of the parser.

    ``decisions.md`` settled thirteen verbs. Somebody typing one of the seven that are not
    built yet should learn that it is a plan, not that they have made a typo -- and the
    answer has to name what does exist, because that list is short and the person asking is
    usually on their first day. It exits 2 rather than 1: nothing was judged.
    """
    runner = FakeRunner({})

    code, _, err = invoke([verb], runner=runner, cwd=tmp_path, monkeypatch=monkeypatch)

    assert code == EXIT_UNUSABLE
    assert f"{verb} is not built yet" in err
    assert "check, new, submit, status, logs, cancel" in err
    assert runner.calls == []


def test_the_configuration_is_found_by_walking_up_from_a_platform_checkout(
    tmp_path: Path,
) -> None:
    """The path an editable install and the suite take.

    Mutation: rely on the packaged copy alone. ``force-include`` applies at wheel build time
    and not to an editable install, so a CLI that only looked there would be unusable in the
    one checkout where it is being developed.
    """
    inside = PROJECT_ROOT / "src" / "edullm_platform"

    assert find_config_directory(environ={}, start=inside) == PROJECT_ROOT / "config"


def test_a_directory_that_is_not_a_configuration_is_named_rather_than_walked_past(
    tmp_path: Path,
) -> None:
    """Mutation: fall through to the next candidate.

    An override that is silently ignored is worse than one that fails: a researcher checking
    a submission against a branch of the platform would be told it is fine by the
    configuration on their disk, and the branch is the thing they were asking about.
    """
    with pytest.raises(ConfigurationUnreadableError) as raised:
        find_config_directory(environ={CONFIG_DIRECTORY_VARIABLE: str(tmp_path)})

    assert "policy.yaml" in str(raised.value)


@pytest.mark.slow
def test_the_git_reading_works_against_a_real_repository(tmp_path: Path) -> None:
    """The one test here that runs git, because every other one supplies its answers.

    Mutation: read the repository name from the directory rather than from the remote. A
    clone can be named anything -- ``OLMo-core`` cloned as ``olmo`` is ordinary -- and
    ``config/repositories.yaml`` is keyed on the GitHub name, so the refusal a wrong reading
    produces is ``unregistered_repository`` about a repository that is registered.
    """
    checkout = tmp_path / "not-the-github-name"
    checkout.mkdir()
    for argv in (
        ("git", "init", "-q", "."),
        ("git", "remote", "add", "origin", "git@github.com:edu-llm/OLMo-core.git"),
    ):
        subprocess.run(argv, cwd=checkout, check=True, capture_output=True)
    (checkout / "a.txt").write_text("a\n", encoding="utf-8")
    subprocess.run(("git", "add", "-A"), cwd=checkout, check=True, capture_output=True)
    subprocess.run(
        ("git", "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qm", "one"),
        cwd=checkout,
        check=True,
        capture_output=True,
    )
    (checkout / "b.txt").write_text("b\n", encoding="utf-8")

    facts = read_git_facts(SubprocessRunner(), cwd=checkout)

    assert facts.repository == "OLMo-core"
    assert facts.commit_sha is not None and len(facts.commit_sha) == 40
    assert facts.dirty_paths == ("b.txt",)
    # Nothing has been pushed anywhere, which is what a fresh local repository looks like
    # and is the state the refusal about an unbuilt commit is derived from.
    assert facts.commit_on_a_remote is False
