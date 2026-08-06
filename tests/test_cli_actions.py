"""The seams the CLI sits on, and the readings it makes of somebody else's output.

Three kinds of thing are here. The seams: a name this package restates rather than imports,
held to the module that owns it. The readings: GitHub's status pair turned into the four
words a submitter needs, and a job log turned back into the report a workflow wrote. And
the verbs that are settled and unbuilt, which have to answer with a plan rather than with
"invalid choice".
"""

from __future__ import annotations

import io
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import pytest

from edullm_platform.cli.actions import (
    ADMITTED,
    CANCEL_WORKFLOW,
    COMPLETION_ATTEMPTS,
    COMPLETION_INTERVAL,
    DECLINED,
    NEW_RUN_ATTEMPTS,
    NEW_RUN_INTERVAL,
    PLATFORM_REPOSITORY,
    PRINTED_RUN_ID,
    SUBMIT_WORKFLOW,
    PlatformActions,
    elapsed_said,
    read_report_sections,
    report_ceiling_seconds,
    submission_state,
)
from edullm_platform.cli.configuration import (
    CONFIG_DIRECTORY_VARIABLE,
    ConfigurationUnreadableError,
    find_config_directory,
)
from edullm_platform.cli.main import EXIT_UNUSABLE, NOT_BUILT_YET, _SignOfLife
from edullm_platform.cli.workspace import SubprocessRunner, read_git_facts
from edullm_platform.lifecycle_projection import BATCH_JOB_STATUSES
from edullm_platform.operational_inventory import EXPECTED_GITHUB_ORG, EXPECTED_GITHUB_REPOSITORY
from tests.cli_support import PROJECT_ROOT, FakeRunner, invoke, ok


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
        ({"status": "completed", "conclusion": "success"}, ADMITTED),
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


#: Everything GitHub can put in a workflow run's ``status``, and everything it can put in a
#: ``conclusion``, so the case below drives every pair rather than the six anybody thought of.
GITHUB_RUN_STATUSES: Final = (
    "queued",
    "in_progress",
    "completed",
    "requested",
    "waiting",
    "pending",
)
GITHUB_RUN_CONCLUSIONS: Final = (
    None,
    "success",
    "failure",
    "neutral",
    "cancelled",
    "skipped",
    "timed_out",
    "action_required",
    "stale",
    "startup_failure",
)


def test_no_word_this_prints_for_a_submission_is_a_word_batch_prints_for_a_job() -> None:
    """Mutation: restore ``SUBMITTED`` as the answer for a workflow run that succeeded.

    **THE TWO VOCABULARIES MET IN ONE OUTPUT AND SHARED A WORD THAT MEANT DIFFERENT THINGS
    IN EACH.** This function describes a submission workflow. Batch describes a job. They
    are printed within a few lines of each other by ``edullm status <run-id>`` -- the
    heading comes from here and the report table below it comes from ``describe-jobs`` --
    and ``SUBMITTED`` was in both sets. In this one it meant the workflow had finished and
    the job was placed; in Batch's it means the job is held with its dependencies not yet
    evaluated, which is very nearly the opposite. Measured on 2026-08-06,
    ``run_019fd676-62f0`` printed ``SUBMITTED`` in the heading and ``SUCCEEDED`` in the
    table eight lines below it.

    Held as an emptiness rather than as an equality against a list, because the property is
    that the two vocabularies stay disjoint and not that either has a particular size. Batch
    owns seven words and this owns however many it owns; adding one on either side that the
    other already has is the failure, whichever side adds it.

    ``BATCH_JOB_STATUSES`` is imported from ``lifecycle_projection`` rather than spelled
    here, because that module is where the seven are ruled and it refuses an eighth.
    """
    printed = {
        submission_state({"status": status, "conclusion": conclusion})
        for status in GITHUB_RUN_STATUSES
        for conclusion in GITHUB_RUN_CONCLUSIONS
    } | {ADMITTED, DECLINED}

    assert printed & set(BATCH_JOB_STATUSES) == set(), (
        "a word this prints about a submission is also a word Batch prints about a job, so "
        "one output carries it twice meaning two things"
    )


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


#: Run ids minted seconds apart by the real platform, kept because the length the listing
#: prints was chosen from them. Two dispatches of a sweep and a resubmission after a fix --
#: the ordinary shapes, not a contrived worst case.
MINTED_CLOSE_TOGETHER: Final = (
    "run_019fcf14-6197-70b6-867c-29b766298103",  # 23:19:58
    "run_019fcf14-0f7c-70f0-bbb3-7f5d6b45482a",  # 23:19:28
    "run_019fce83-9a51-70dc-ad7c-0e4699ef9b93",  # 20:41:59
    "run_019fce83-4597-703a-b1cb-f52bd59788e4",  # 20:41:26
    "run_019fce2f-9b5b-70ea-8a77-6703e1b76605",  # 19:10:00
    "run_019fce2f-895a-7041-a4ad-86604d20905a",  # 19:09:58
)


def test_the_printed_run_id_is_long_enough_for_the_runs_that_actually_collide() -> None:
    """The length was measured against real ids rather than picked, and this is the measurement.

    A run id is a UUIDv7: its leading twelve hex digits are the millisecond it was minted.
    Eight of them are the top thirty-two bits of that, which advance once per 65,536 ms --
    so two submissions inside the same minute *must* share an eight-character prefix. Of
    the last 74 real submissions, ten did, in five pairs between 4.6 and 55.6 seconds
    apart; three of those pairs are below. A fan-out shares one id and a retry makes a
    second one seconds later, so this is the normal case and not the tail.

    Thirteen characters is the whole timestamp, and everything past it is the random half
    of the id. The smallest gap between two real submissions was 3.875 s, against the
    1 ms two runs would have to share to collide here.

    Mutation: shorten it back to eight, or lengthen it into the entropy. The first makes
    the listing print one name for two runs; the second prints characters no reader can do
    anything with.
    """
    printed = {run_id[: len("run_") + PRINTED_RUN_ID] for run_id in MINTED_CLOSE_TOGETHER}
    at_eight = {run_id[: len("run_") + 8] for run_id in MINTED_CLOSE_TOGETHER}

    assert len(printed) == len(MINTED_CLOSE_TOGETHER)
    assert len(at_eight) == len(MINTED_CLOSE_TOGETHER) // 2
    # The whole timestamp and none of the entropy: the character after it is the hyphen
    # before the version nibble.
    assert MINTED_CLOSE_TOGETHER[0][len("run_") + PRINTED_RUN_ID] == "-"


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


#: A job log shaped the way a real one is: the runner talks before the report and goes on
#: talking after it, and the section a verb wants is the last one anything writes.
#:
#: The fixture above ends at the report, which is why it passed while the shipped behaviour
#: was to keep everything to the end of the file. A log that stops where the answer stops is
#: not a log this code ever meets.
LOG_WITH_A_RUNNER_TALKING_AFTERWARDS = (
    "cancel\tSet up job\t2099-01-01T00:00:00.0000000Z Current runner version: '2.320.0'\n"
    "cancel\tSet up job\t2099-01-01T00:00:00.0000000Z ##[group]Operating System\n"
    "cancel\tSay what the run is doing\t2099-01-01T00:00:01.0000000Z ## run_0198\n"
    "cancel\tSay what the run is doing\t2099-01-01T00:00:02.0000000Z | Status | SUCCEEDED |\n"
    "cancel\tShow the last fifty\t2099-01-01T00:00:03.0000000Z ### The last lines this run printed\n"
    "cancel\tShow the last fifty\t2099-01-01T00:00:04.0000000Z 9 line(s), oldest first\n"
    "cancel\tShow the last fifty\t2099-01-01T00:00:05.0000000Z step 200 loss 5.9\n"
    "cancel\tPost Check out the platform\t2099-01-01T00:00:06.0000000Z Post job cleanup.\n"
    "cancel\tPost Check out the platform\t2099-01-01T00:00:07.0000000Z "
    "[command]/usr/bin/git config --global --add safe.directory /home/runner/work/platform\n"
    "cancel\tComplete job\t2099-01-01T00:00:08.0000000Z Cleaning up orphan processes\n"
)


def test_the_last_section_of_a_report_ends_where_the_step_that_wrote_it_ends() -> None:
    """Mutation: keep every line after the heading, which is what shipped.

    ``keeping`` was turned on at a matching heading and turned off only at the next one, so
    the section a verb asked for ran to the end of the file whenever it was the last one
    anything wrote -- which it is for both ``logs`` and, on a run that reached AWS,
    ``status``. What a researcher got was their answer followed by the runner's own
    housekeeping: ``Post job cleanup``, a ``git config --global --add safe.directory``, and
    ``Cleaning up orphan processes``. About seventy lines of it on a real run.

    The boundary is the step, which is already in every line ``gh run view --log`` returns
    and needed no new parsing. A report block is written by one step; the runner's
    housekeeping is written by the post steps and by ``Complete job``.

    Asserted as an equality rather than as the absence of three strings, because a denylist
    of the noise seen once is the fix that leaves the fourth line in.
    """
    tailed = read_report_sections(
        LOG_WITH_A_RUNNER_TALKING_AFTERWARDS, ("The last lines this run printed",)
    )

    assert tailed == (
        "### The last lines this run printed\n9 line(s), oldest first\nstep 200 loss 5.9"
    )


def test_a_section_the_runner_interrupted_is_not_cut_short_by_its_own_grouping() -> None:
    """Mutation: end the section at the first line whose step differs, rather than tracking it.

    ``## run_0198`` is followed by a step that writes no heading at all, and the section
    after it is a different report. Ending on any change of step would be the same rule read
    backwards and would drop the table's last row on a log where the runner interleaves a
    line. What ends a section is the step it started in ending, and the next heading.
    """
    described = read_report_sections(LOG_WITH_A_RUNNER_TALKING_AFTERWARDS, ("run_0198",))

    assert described == "## run_0198\n| Status | SUCCEEDED |"
    assert "Post job cleanup" not in described
    assert "The last lines" not in described


@pytest.mark.parametrize("verb", sorted(NOT_BUILT_YET))
def test_a_settled_verb_that_is_unbuilt_says_so_rather_than_being_absent(
    verb: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: leave them out of the parser.

    ``decisions.md`` settled a list of verbs. Somebody typing one that is not built yet
    should learn that it is a plan, not that they have made a typo -- and the answer has to
    name what does exist, because that list is short and the person asking is usually on
    their first day. It exits 2 rather than 1: nothing was judged.
    """
    runner = FakeRunner({})

    code, _, err = invoke([verb], runner=runner, cwd=tmp_path, monkeypatch=monkeypatch)

    assert code == EXIT_UNUSABLE
    assert f"{verb} is not built yet" in err
    assert "check, submit, status, logs, cancel" in err
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


# ---------------------------------------------------------------------------------------
# the two waits, and what a reader sees while they run
# ---------------------------------------------------------------------------------------


def test_the_ceiling_is_the_two_polls_added_up_rather_than_a_number_in_a_sentence() -> None:
    """Mutation: write the total into the sentence that quotes it.

    ``logs`` announced tens of seconds and could take eleven minutes, which is the shape of
    every stale number this repository has been bitten by. Widening either poll has to move
    the sentence, and the only way that happens by itself is if the sentence never held the
    number. Both loops sleep between attempts and not before the first, so a bound of ``n``
    attempts is ``n - 1`` sleeps, and getting that off by one would understate the wait.
    """
    expected = (NEW_RUN_ATTEMPTS - 1) * NEW_RUN_INTERVAL
    expected += (COMPLETION_ATTEMPTS - 1) * COMPLETION_INTERVAL

    assert report_ceiling_seconds() == expected
    assert report_ceiling_seconds() > 600


def test_a_poll_hands_the_caller_one_clock_across_both_of_its_loops() -> None:
    """Mutation: restart the clock in the second loop.

    A dispatch waits for a runner and then waits for the workflow, and they are two loops.
    A reader who has waited six minutes and is told "1 minute so far" learns that the
    number means nothing, which is worse than printing no number: the line exists to say
    the wait is progressing rather than hung.
    """
    seen: list[float] = []
    runs = iter(
        [
            {"status": "in_progress"},
            {"status": "in_progress"},
            {"status": "completed", "conclusion": "success"},
        ]
    )
    actions = PlatformActions(
        FakeRunner({("gh", "api"): lambda _: ok(json.dumps(next(runs)))}),
        sleep=lambda _: None,
    )

    conclusion = actions.wait_for_completion(
        22001, interval=6.0, waiting=seen.append, elapsed_already=57.0
    )

    assert conclusion == "success"
    assert seen == [63.0, 69.0]


def test_the_sign_of_life_is_a_whole_line_a_minute_and_never_a_spinner() -> None:
    """**The one thing added to the wait, held to the property the whole tool rests on.**

    Mutation: a spinner, or a dot per poll. A spinner is a carriage return and a cursor
    move, so a run piped into a file stops being the run a terminal showed and a pasted
    transcript stops being what the next person reads. A dot per poll is a hundred and
    twenty dots. This is one ordinary line at a whole minute, on stderr with the sentence
    that announced the wait, so the bytes are the same either way and a script reading the
    log tail off stdout still gets the log tail.
    """
    err = io.StringIO()
    saying = _SignOfLife(err)

    for elapsed in (6.0, 30.0, 59.0, 60.0, 66.0, 119.0, 121.0):
        saying(elapsed)

    lines = err.getvalue().splitlines()

    assert len(lines) == 2
    assert all(line.startswith("still waiting,") for line in lines)
    assert "\x1b" not in err.getvalue() and "\r" not in err.getvalue()
    assert saying.elapsed == 121.0
