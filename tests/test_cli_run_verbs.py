"""``status``, ``logs`` and ``cancel``: three verbs over one workflow, and one over none.

``cancel-run.yml`` is named "Look at a run, or stop it" and already does all three things --
looking is the default, stopping is the opt-in, and the log tail is printed on every
dispatch including one that goes on to stop the run. So these verbs are three doors into
one workflow, and what is worth testing is that the safe door stays safe: neither
``status`` nor ``logs`` may ever send ``stop=true``.

**BUT THE DISPATCH IS THE SECOND THING EACH OF THEM TRIES, NOT THE FIRST.**
``submit-run.yml`` finishes at admission, so everything up to that point is on GitHub and
costs an API call, and only what happens afterwards is inside AWS and costs a runner. The
cases below are that line, tested from both sides: a run parked at a gate, refused while
compiling, or not yet compiled is answered outright, and an admitted one falls through.

The one case worth more care than the others is an admission job that *failed*, because a
failure says nothing about whether it got as far as starting the run -- ``submit-run.yml``
writes where a run went only after admission answers. Reading that as "not admitted" would
make ``cancel`` refuse to stop a job that is running, so it reads as uncertain and
dispatches, and there is a test below that will fail if anybody optimizes that away.

**AND THE THIRD WAY TO BE UNCERTAIN IS NOT LIKE THE OTHER TWO, WHICH IS THE OTHER LINE THIS
FILE NOW DRAWS.** Those two are uncertainties about a run that was found. A run id no recent
dispatch carries is not found at all, and reading that as "ask AWS" is how a verb that looks
read-only came to dispatch a workflow, spend a runner and sit for the poll ceiling over an
id pasted from an old transcript. ``status`` and ``logs`` refuse it and name ``--ask-aws``;
``cancel`` still asks, because refusing to stop a job that turns out to be running is worse
than any runner. Three cases below hold each half of that.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final

import pytest
import yaml

from edullm_platform.cli.actions import (
    ADMISSION_JOB,
    ADMITTED,
    CANCEL_WORKFLOW,
    LOG_HEADINGS,
    PRINTED_RUN_ID,
    SUBMIT_WORKFLOW,
    report_ceiling_seconds,
)
from edullm_platform.cli.main import EXIT_OK, EXIT_REFUSED, build_parser_and_verbs
from edullm_platform.lifecycle_projection import BATCH_JOB_STATUSES
from tests.cli_support import PROJECT_ROOT, FakeRunner, failed, invoke, ok

RUN_ID = "run_019fd2a1-4e07-7a3c-9d55-1b2f8c0e6a41"
SUBMIT_RUN_ID = 19407766
CANCEL_RUN = {
    "id": 22001,
    "status": "completed",
    "conclusion": "success",
    "created_at": "2099-01-01T00:00:00Z",
    "html_url": "https://github.com/edu-llm/platform/actions/runs/22001",
}

#: What ``gh run view --log`` hands back: the job name, the step name, an instant, and then
#: the line the workflow printed. The report ``cancel-run.yml`` tees into its step summary
#: arrives wrapped in those three columns, which is what the reader here has to strip.
REPORT_LOG = "\n".join(
    f"cancel\tSay what the run is doing\t2099-01-01T00:00:0{index}.0000000Z {line}"
    for index, line in enumerate(
        [
            f"## {RUN_ID}",
            "| | |",
            "| --- | --- |",
            "| Status | `RUNNABLE` |",
            "| Why | no compute environment capacity yet |",
            "### The last lines this run printed",
            "```",
            "step 200   loss  5.9042",
            "```",
        ]
    )
)

#: The same report for a job that has finished, which is the state the heading used to
#: contradict. ``run_019fd676-62f0`` looked exactly like this on 2026-08-06 under a heading
#: that called it ``SUBMITTED``.
SUCCEEDED_REPORT_LOG = REPORT_LOG.replace("`RUNNABLE`", "`SUCCEEDED`").replace(
    "no compute environment capacity yet", "Essential container in task exited"
)

#: What the same job log holds when the dispatch asked for the other end of the stream: a
#: different heading, and the lines a run writes on the way up. Both container lines carry
#: the tabs OLMo-core's logger puts in them, so this fixture is also the end-to-end half of
#: the truncation ``tests/test_cli_actions.py`` covers at the parser.
HEAD_REPORT_LOG = "\n".join(
    f"cancel\tShow fifty lines\t2099-01-01T00:00:0{index}.0000000Z {line}"
    for index, line in enumerate(
        [
            f"### {LOG_HEADINGS[True]}",
            "3 line(s), oldest first. This is the start of the stream.",
            "```",
            (
                "2026-08-07 20:29:09.986\tip-10-20-55-74:0\tolmo_core.train.trainer:935\t"
                "INFO\tLoading checkpoint from 's3://bucket/step20'..."
            ),
            (
                "2026-08-07 20:29:52.030\tip-10-20-55-74:0\tolmo_core.train.trainer:412\t"
                "INFO\tWill resume training from step 20, epoch 1"
            ),
            "```",
        ]
    )
)


def github(*, log: str = REPORT_LOG) -> FakeRunner:
    """A GitHub where the fast path finds nothing, so every verb falls through as before.

    ``gh run download`` fails, so no candidate dispatch yields a manifest carrying the run
    id and the search comes back empty. That is the honest worst case -- a run older than
    the search window, or one whose artifacts have expired -- and the behaviour it has to
    produce is exactly the behaviour that existed before there was a fast path.
    """
    return FakeRunner(
        {
            ("gh", "workflow", "run"): ok(""),
            ("gh", "api"): lambda argv: ok(
                json.dumps(CANCEL_RUN)
                if argv[-1].endswith(str(CANCEL_RUN["id"]))
                else json.dumps({"workflow_runs": [CANCEL_RUN]})
            ),
            ("gh", "run", "download"): failed("no artifact matches"),
            ("gh", "run", "view"): ok(log),
        }
    )


def submission(
    *,
    status: str = "completed",
    conclusion: str | None = "success",
    admission: str | None = "success",
    admission_present: bool = True,
    pending: tuple[dict[str, Any], ...] = (),
    approvals: tuple[dict[str, Any], ...] = (),
    log: str = REPORT_LOG,
) -> FakeRunner:
    """A GitHub carrying one dispatch of ``submit-run.yml`` in a state a test chooses.

    Both workflows answer, because a verb that falls through drives the second one after
    reading the first: the fast path is a prefix of the slow one rather than a branch away
    from it.
    """
    submit_run = {
        "id": SUBMIT_RUN_ID,
        "status": status,
        "conclusion": conclusion,
        "created_at": "2099-01-01T00:00:00Z",
        "html_url": f"https://github.com/edu-llm/platform/actions/runs/{SUBMIT_RUN_ID}",
    }
    jobs = [{"name": "Compile the submission and classify it", "conclusion": "success"}]
    if admission_present:
        jobs.append({"name": ADMISSION_JOB, "conclusion": admission})

    def api(argv: tuple[str, ...]) -> Any:
        path = argv[-1]
        if path.endswith(f"/{SUBMIT_RUN_ID}/jobs"):
            return ok(json.dumps({"jobs": jobs}))
        if path.endswith("/pending_deployments"):
            return ok(json.dumps(list(pending)))
        if path.endswith("/approvals"):
            return ok(json.dumps(list(approvals)))
        if SUBMIT_WORKFLOW in path:
            return ok(json.dumps({"workflow_runs": [submit_run]}))
        if path.endswith(str(CANCEL_RUN["id"])):
            return ok(json.dumps(CANCEL_RUN))
        return ok(json.dumps({"workflow_runs": [CANCEL_RUN]}))

    def download(argv: tuple[str, ...]) -> Any:
        directory = Path(argv[argv.index("--dir") + 1])
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "compiled-submission.json").write_text(
            json.dumps(
                {
                    "run_id": RUN_ID,
                    "experiment": "an-experiment",
                    "team": "memory-split",
                    "manifest": {"fanout": {"size": 9, "index_parameter": "arm"}},
                }
            ),
            encoding="utf-8",
        )
        return ok("")

    return FakeRunner(
        {
            ("gh", "workflow", "run"): ok(""),
            ("gh", "api"): api,  # type: ignore[dict-item]
            ("gh", "run", "download"): download,  # type: ignore[dict-item]
            ("gh", "run", "view"): ok(log),
        }
    )


def parked_at(gate: str, *, reviewers: tuple[str, ...], yours: bool) -> tuple[dict[str, Any], ...]:
    return (
        {
            "environment": {"name": gate},
            "wait_timer": 0,
            "current_user_can_approve": yours,
            "reviewers": [
                {"type": "User", "reviewer": {"login": login}} for login in reviewers
            ],
        },
    )


def dispatched_fields(runner: FakeRunner) -> dict[str, str]:
    argv = runner.ran("gh", "workflow", "run")[0]
    fields: dict[str, str] = {}
    for index, word in enumerate(argv):
        if word == "-f":
            name, _, value = argv[index + 1].partition("=")
            fields[name] = value
    return fields


def test_status_on_one_run_asks_the_workflow_to_look_and_never_to_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: send ``stop`` unset, or true.

    ``cancel-run.yml`` defaults ``stop`` to false, so an unset field is safe today and is
    safe because of a default in a file this binary does not own. Sending it explicitly is
    what makes "looking never stops anything" a property of the caller as well.

    On an admitted run rather than an unfindable one, which is the only kind of run
    ``status`` dispatches for now. This case used to lean on a GitHub where nothing could be
    found, so a test about ``stop`` was also, silently, the test that held the behaviour
    :func:`test_a_run_the_window_does_not_reach_is_refused_rather_than_asked_after` removes.
    """
    runner = submission()

    code, out, _ = invoke(["status", RUN_ID], runner=runner, cwd=tmp_path, monkeypatch=monkeypatch)

    assert code == EXIT_OK
    fields = dispatched_fields(runner)
    assert fields["stop"] == "false"
    assert fields["run_id"] == RUN_ID
    assert runner.ran("gh", "workflow", "run")[0][3] == CANCEL_WORKFLOW
    assert "| Status | `RUNNABLE` |" in out


def test_logs_prints_the_tail_and_not_the_description_beside_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: print the whole log.

    One workflow writes four reports into one job, and a verb that printed all of them would
    make ``logs`` and ``status`` the same command. The tail is fifty lines by the workflow's
    own choice, and burying it under a table is how it stops being read.
    """
    runner = submission()

    code, out, _ = invoke(["logs", RUN_ID], runner=runner, cwd=tmp_path, monkeypatch=monkeypatch)

    assert code == EXIT_OK
    assert "step 200   loss  5.9042" in out
    assert "| Status |" not in out
    assert dispatched_fields(runner)["stop"] == "false"
    # The tail is the default, so nothing is sent about which end to read. See the comment
    # on ``from_start`` in ``_drive_the_run_report``.
    assert "from_start" not in dispatched_fields(runner)


def test_logs_can_ask_for_the_head_of_a_stream_and_reads_the_section_it_asked_for(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """THE END OF A LOG A RESEARCHER COULD NOT REACH. Mutation: accept the flag and go on
    dispatching for the tail, or send it and go on reading the tail's heading.

    Everything a run prints before its first batch is above the fifty lines the tail holds,
    on a training log measured in hundreds of megabytes -- which is why ``resumed_from_step``
    had to be added to OLMo-core's end-of-run summary in another repository, to move one line
    into a window this verb could see.

    Both halves are asserted together because either alone is a worse answer than the flag
    not existing. Sending the field and reading the tail's heading prints "the workflow named
    no section this verb reads" over an answer that is sitting in the step summary; reading
    the head's heading without sending the field finds nothing at all.
    """
    runner = submission(log=HEAD_REPORT_LOG)

    code, out, _ = invoke(
        ["logs", RUN_ID, "--from-start"], runner=runner, cwd=tmp_path, monkeypatch=monkeypatch
    )

    assert code == EXIT_OK
    assert dispatched_fields(runner)["from_start"] == "true"
    assert LOG_HEADINGS[True] in out
    assert "Loading checkpoint from" in out
    assert "Will resume training from step 20" in out


def test_cancel_sends_stop_and_carries_the_reason_that_gets_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: drop the reason, or make it optional here.

    The workflow refuses a stop with no reason and says why: a termination with no recorded
    reason is the thing ``lifecycle_projection`` cannot tell from a failure. Requiring it in
    argparse means the refusal costs no runner.
    """
    runner = github()

    code, _, _ = invoke(
        ["cancel", RUN_ID, "--reason", "wrong corpus"],
        runner=runner,
        cwd=tmp_path,
        monkeypatch=monkeypatch,
    )

    assert code == EXIT_OK
    fields = dispatched_fields(runner)
    assert fields["stop"] == "true"
    assert fields["reason"] == "wrong corpus"


def test_cancel_without_a_reason_is_refused_by_the_parser_rather_than_by_a_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: default the reason to something.

    A default reason is a recorded reason nobody chose, which is worse than none: the run's
    history then says it was cancelled deliberately, in words the person who cancelled it
    never wrote.
    """
    runner = github()

    with pytest.raises(SystemExit) as raised:
        invoke(["cancel", RUN_ID], runner=runner, cwd=tmp_path, monkeypatch=monkeypatch)

    assert raised.value.code == 2
    assert runner.calls == []


def test_a_malformed_run_id_costs_no_runner_at_all(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same check ``cancel-run.yml`` makes first, made one layer earlier.

    Mutation: dispatch and let the workflow refuse. It would, in its first step and before
    touching AWS -- and it would cost a runner, a queue slot and a minute to say what a
    regular expression says here for nothing.
    """
    runner = github()

    code, _, err = invoke(
        ["status", "019fd2a1"], runner=runner, cwd=tmp_path, monkeypatch=monkeypatch
    )

    assert code == EXIT_REFUSED
    assert "refused  run_id_not_well_formed" in err
    assert runner.calls == []


def test_status_with_no_run_id_reads_your_own_submissions_without_dispatching_anything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The question people ask most: has a lead tapped yet.

    Mutation: answer it by dispatching a workflow. GitHub already knows -- a submission
    parked at an environment with reviewers has status ``waiting`` -- so paying a runner for
    it would make the most-typed command the slowest one.
    """
    waiting = {
        "id": 19407766,
        "status": "waiting",
        "conclusion": None,
        "created_at": "2099-01-01T00:00:00Z",
        "html_url": "https://github.com/edu-llm/platform/actions/runs/19407766",
    }

    def download(argv: tuple[str, ...]) -> object:
        directory = Path(argv[argv.index("--dir") + 1])
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "compiled-submission.json").write_text(
            json.dumps(
                {
                    "run_id": RUN_ID,
                    "experiment": "an-experiment",
                    "manifest": {"fanout": {"size": 9, "index_parameter": "arm"}},
                }
            ),
            encoding="utf-8",
        )
        return ok("")

    runner = FakeRunner(
        {
            ("gh", "api"): ok(json.dumps({"workflow_runs": [waiting]})),
            ("gh", "run", "download"): download,  # type: ignore[dict-item]
        }
    )

    code, out, _ = invoke(["status"], runner=runner, cwd=tmp_path, monkeypatch=monkeypatch)

    assert code == EXIT_OK
    assert runner.ran("gh", "workflow", "run") == []
    assert "run_019fd2a1-4e07" in out
    assert "PENDING_APPROVAL" in out
    assert "an-experiment 9 cells" in out


# ---------------------------------------------------------------------------------------
# an abbreviated run id: the form the listing prints and the form people paste back
# ---------------------------------------------------------------------------------------


def near(*runs: tuple[str, str, str], admission: str = "skipped") -> FakeRunner:
    """A GitHub carrying several dispatches, each with the run id and experiment given."""
    listed = [
        {
            "id": 1000 + index,
            "status": "completed",
            "conclusion": "success",
            "created_at": created,
            "html_url": f"https://github.com/edu-llm/platform/actions/runs/{1000 + index}",
        }
        for index, (_, _, created) in enumerate(runs)
    ]
    by_workflow_run = {1000 + index: run_id for index, (run_id, _, _) in enumerate(runs)}
    experiments = {1000 + index: name for index, (_, name, _) in enumerate(runs)}

    def api(argv: tuple[str, ...]) -> Any:
        path = argv[-1]
        if path.endswith("/jobs"):
            return ok(json.dumps({"jobs": [{"name": ADMISSION_JOB, "conclusion": admission}]}))
        if path.endswith("/approvals"):
            return ok(json.dumps([]))
        if SUBMIT_WORKFLOW in path:
            return ok(json.dumps({"workflow_runs": listed}))
        if path.endswith(str(CANCEL_RUN["id"])):
            return ok(json.dumps(CANCEL_RUN))
        return ok(json.dumps({"workflow_runs": [CANCEL_RUN]}))

    def download(argv: tuple[str, ...]) -> Any:
        workflow_run = int(argv[argv.index("download") + 1])
        directory = Path(argv[argv.index("--dir") + 1])
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "compiled-submission.json").write_text(
            json.dumps(
                {
                    "run_id": by_workflow_run[workflow_run],
                    "experiment": experiments[workflow_run],
                    "manifest": {"fanout": {"size": 1, "index_parameter": "arm"}},
                }
            ),
            encoding="utf-8",
        )
        return ok("")

    return FakeRunner(
        {
            ("gh", "api"): api,  # type: ignore[dict-item]
            ("gh", "run", "download"): download,  # type: ignore[dict-item]
            ("gh", "run", "view"): ok(REPORT_LOG),
            ("gh", "workflow", "run"): ok(""),
        }
    )


def test_the_id_the_listing_prints_is_an_id_the_verbs_take(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The circle this closes was the first thing a first user hit.

    ``status`` printed ``run_019fd2a1``, which is what anybody would copy, and every verb
    refused it as malformed -- and the refusal's one remedy was "edullm status with no
    argument lists yours", which is where the unusable string came from. Mutation: take
    only ids given in full, and the listing becomes decoration.
    """
    runner = near((RUN_ID, "an-experiment", "2099-01-01T00:00:00Z"))
    printed = RUN_ID[: len("run_") + PRINTED_RUN_ID]

    code, out, err = invoke(
        ["status", printed], runner=runner, cwd=tmp_path, monkeypatch=monkeypatch
    )

    assert code == EXIT_OK, out + err
    assert "run_id_not_well_formed" not in err
    assert printed in out
    assert "an-experiment" in out


def test_the_shorter_id_older_transcripts_carry_still_resolves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Eight characters is what the listing printed yesterday, so it is what Slack holds.

    Mutation: accept only the length the listing prints today. Every id pasted into a
    thread before this change is eight characters long, and the person retrying one of them
    is exactly the person this whole path is for.
    """
    runner = near((RUN_ID, "an-experiment", "2099-01-01T00:00:00Z"))

    code, out, err = invoke(
        ["status", "run_019fd2a1"], runner=runner, cwd=tmp_path, monkeypatch=monkeypatch
    )

    assert code == EXIT_OK, out + err
    assert "an-experiment" in out


def test_the_wait_an_abbreviation_costs_is_named_before_it_is_paid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """26 seconds against the real platform, and a whole id is found in one or two reads.

    Mutation: wait in silence. The difference is not the wait -- an abbreviation genuinely
    has to read the window out to know no second run answers to it -- it is that a terminal
    printing nothing for half a minute teaches people the tool hung. Every other wait this
    binary makes somebody sit through is announced first, including the runner one.

    On stderr, so a script reading the listing is not handed a line that is not a run.
    """
    runner = near((RUN_ID, "an-experiment", "2099-01-01T00:00:00Z"))

    _, out, err = invoke(
        ["status", "run_019fd2a1"], runner=runner, cwd=tmp_path, monkeypatch=monkeypatch
    )

    assert "resolving run_019fd2a1, which takes a few seconds." in err
    assert "resolving" not in out

    _, _, whole = invoke(["status", RUN_ID], runner=runner, cwd=tmp_path, monkeypatch=monkeypatch)

    assert "resolving" not in whole


def test_what_reaches_the_workflow_is_the_whole_id_and_never_the_abbreviation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The shorthand stops at this binary's edge.

    ``cancel-run.yml`` checks the shape of what it is handed in its first step and would
    refuse an abbreviation, correctly -- it has no listing to resolve one against and no
    business guessing. Mutation: pass through what was typed. It costs a runner to be told
    the id is malformed, which is the exact cost :func:`_malformed_run_id` exists to avoid.
    """
    runner = near((RUN_ID, "an-experiment", "2099-01-01T00:00:00Z"), admission="success")

    code, out, err = invoke(
        ["cancel", "run_019fd2a1", "--reason", "wrong corpus"],
        runner=runner,
        cwd=tmp_path,
        monkeypatch=monkeypatch,
    )

    assert code == EXIT_OK, out + err
    assert dispatched_fields(runner)["run_id"] == RUN_ID


def test_an_abbreviation_naming_two_runs_names_them_both_and_sends_nobody_back_to_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**The refusal that must never point at the command that produced its input.**

    UUIDv7 puts the clock at the front, so ids collide exactly when runs were submitted
    close together -- a retry, or the second arm of a sweep. Both of those are ordinary,
    and both make an eight-character abbreviation name two runs.

    Mutation: answer with "run edullm status to see your runs". The abbreviation came from
    that listing, so the remedy hands back the input that failed and the person loops. What
    this has to do instead is carry the answer: each match at a length that tells it from
    the other, its experiment, and the clock time -- not only how long ago, because runs
    whose ids collide are runs whose elapsed times round to the same words.
    """
    first = "run_019fcf14-6197-70b6-867c-29b766298103"
    second = "run_019fcf14-0f7c-70f0-bbb3-7f5d6b45482a"
    runner = near(
        (first, "strict-in-distribution-lr", "2099-01-01T23:19:58Z"),
        (second, "regime-arity-param-matched", "2099-01-01T23:19:28Z"),
    )

    code, out, err = invoke(
        ["cancel", "run_019fcf14", "--reason", "wrong-arm"],
        runner=runner,
        cwd=tmp_path,
        monkeypatch=monkeypatch,
    )
    said = " ".join(err.split())

    assert code == EXIT_REFUSED
    assert "refused  run_id_is_ambiguous" in err
    assert "run_019fcf14-6197 (strict-in-distribution-lr" in said
    assert "run_019fcf14-0f7c (regime-arity-param-matched" in said
    assert "23:19:58 UTC" in said and "23:19:28 UTC" in said
    assert "edullm status" not in said
    # Nothing was stopped, and no runner was spent finding out that nothing could be.
    assert runner.ran("gh", "workflow", "run") == []
    assert out == ""


def test_an_abbreviation_that_could_be_resolved_never_reaches_a_dispatch_ambiguous(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two runs sharing eight characters, one of them named at the printed length.

    Mutation: stop the search at the first match the way a whole id does. It would pick the
    newest of the two silently, which for ``cancel`` means stopping a run nobody asked to
    stop -- the one outcome in this file worth reading every dispatch to avoid.
    """
    first = "run_019fcf14-6197-70b6-867c-29b766298103"
    second = "run_019fcf14-0f7c-70f0-bbb3-7f5d6b45482a"
    runner = near(
        (first, "strict-in-distribution-lr", "2099-01-01T23:19:58Z"),
        (second, "regime-arity-param-matched", "2099-01-01T23:19:28Z"),
    )

    code, out, err = invoke(
        ["status", "run_019fcf14-0f7c"], runner=runner, cwd=tmp_path, monkeypatch=monkeypatch
    )

    assert code == EXIT_OK, out + err
    assert "regime-arity-param-matched" in out
    assert "strict-in-distribution-lr" not in out


# ---------------------------------------------------------------------------------------
# the fast path: what GitHub can answer before AWS has to be asked
# ---------------------------------------------------------------------------------------


def test_a_run_waiting_on_a_lead_is_answered_without_a_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The most-repeated question, and the one the old code paid the most to answer.

    Mutation: dispatch anyway. A run parked at an environment has not reached AWS -- the
    admission job has not started -- so a workflow that describes Batch jobs has nothing to
    describe, and the tens of seconds buy an answer GitHub gave immediately. This is also
    the state people poll hardest, because it is the one that changes when somebody else
    acts.
    """
    runner = submission(
        status="waiting",
        conclusion=None,
        admission=None,
        pending=parked_at("run-approval-lead", reviewers=("grant-matherne",), yours=False),
    )

    code, out, _ = invoke(["status", RUN_ID], runner=runner, cwd=tmp_path, monkeypatch=monkeypatch)

    assert code == EXIT_OK
    assert runner.ran("gh", "workflow", "run") == []
    assert "PENDING_APPROVAL" in out
    assert "run-approval-lead" in out
    assert "grant-matherne" in out
    assert "nothing was dispatched to answer this." in out


def test_a_lead_is_told_the_run_is_waiting_on_them_specifically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: print the reviewer list and stop there.

    ``current_user_can_approve`` is the one field that turns "waiting for a lead" into
    "waiting for you", and it is the difference between a fact about somebody else and a
    thing to go and do. It is also why a lead has any reason to run this verb: their own
    submissions are not what they are checking on.
    """
    runner = submission(
        status="waiting",
        conclusion=None,
        admission=None,
        pending=parked_at(
            "run-approval-lead", reviewers=("grant-matherne", "hiya-vyas"), yours=True
        ),
    )

    code, out, _ = invoke(["status", RUN_ID], runner=runner, cwd=tmp_path, monkeypatch=monkeypatch)

    assert code == EXIT_OK
    assert "can release this" in out
    assert runner.ran("gh", "workflow", "run") == []


def test_a_submission_refused_before_admission_never_asks_aws_about_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: read the workflow run's conclusion instead of the admission job's.

    A compile that refused leaves the admission job skipped, so nothing was ever sent. The
    old code dispatched here and waited tens of seconds to be told about a run that does
    not exist in AWS, which is the single most wasteful thing this binary did.
    """
    runner = submission(conclusion="failure", admission="skipped")

    code, out, _ = invoke(["status", RUN_ID], runner=runner, cwd=tmp_path, monkeypatch=monkeypatch)

    assert code == EXIT_OK
    assert runner.ran("gh", "workflow", "run") == []
    assert "without running its admission job" in out
    assert "nothing was dispatched to answer this." in out


def test_an_admitted_run_falls_through_and_says_why_before_it_waits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The half the fast path cannot answer, handed over rather than hidden.

    Mutation: dispatch silently. The wait is tens of seconds and it is the only wait this
    binary imposes; a reader who knows it is coming and why is having a different experience
    from one watching a cursor. Everything GitHub did know is printed first, so the wait is
    for the part that genuinely needs AWS rather than for all of it.
    """
    runner = submission(
        approvals=(
            {"state": "approved", "user": {"login": "hiya-vyas"}, "comment_created_at": None},
        )
    )

    code, out, err = invoke(["status", RUN_ID], runner=runner, cwd=tmp_path, monkeypatch=monkeypatch)

    assert code == EXIT_OK
    assert "hiya-vyas" in out
    assert "reading that from AWS needs a runner" in out
    said = " ".join(err.split())
    assert "waits first for a runner and then for that workflow to finish" in said
    assert runner.ran("gh", "workflow", "run") != []
    assert "| Status | `RUNNABLE` |" in out


def test_the_wait_names_its_own_ceiling_rather_than_promising_tens_of_seconds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: go back to "tens of seconds, not a moment".

    That was true of the usual case and false of the one that matters. The two polls behind
    this dispatch allow 57 seconds to find the run and 594 more to watch it finish, so the
    worst case is close to eleven minutes of a terminal printing nothing, described by a
    sentence promising under a minute. A reader who has been told a ceiling waits; a reader
    who has been told the wrong one reaches for Ctrl-C at ninety seconds, which on ``cancel``
    is the moment the stop is in flight.

    The number is read off the poll parameters rather than compared against a literal here,
    for the reason the whole ``no_hardcoded_bounds`` rule exists: a second copy agrees on
    the day it is typed. Widen either poll and both the sentence and this move together.
    """
    runner = submission()

    _, _, err = invoke(["logs", RUN_ID], runner=runner, cwd=tmp_path, monkeypatch=monkeypatch)
    minutes = -(-int(report_ceiling_seconds()) // 60)

    assert f"gives up after {minutes} minutes" in " ".join(err.split())


def test_an_admission_job_that_failed_is_uncertain_rather_than_not_admitted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The case where guessing would be worse than waiting.

    Mutation: treat a failed admission job as "never reached AWS". ``submit-run.yml`` writes
    where a run went only *after* the admission execution answers, so a job that failed may
    have started a Batch job that is running now. Believing otherwise makes ``cancel``
    refuse to stop it, and the researcher's only remaining option is to ask somebody with a
    credential. Dispatching is the cheap half of that mistake.
    """
    runner = submission(conclusion="failure", admission="failure")

    code, _, err = invoke(
        ["cancel", RUN_ID, "--reason", "wrong corpus"],
        runner=runner,
        cwd=tmp_path,
        monkeypatch=monkeypatch,
    )

    assert code == EXIT_OK
    assert runner.ran("gh", "workflow", "run") != []
    assert dispatched_fields(runner)["stop"] == "true"
    assert "does not say whether it got as far as starting the run" in err


def test_cancelling_a_run_that_never_started_names_the_operation_that_would_stop_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: exit 0 because nothing is running.

    Nothing is running *yet*. A run parked at a gate is one approval away from starting, so
    reporting success would tell a script the run was seen to and leave it live. Stopping it
    is a GitHub operation rather than an AWS one, and naming that operation is the only
    useful thing this can say.
    """
    runner = submission(
        status="waiting",
        conclusion=None,
        admission=None,
        pending=parked_at("run-approval-lead", reviewers=("grant-matherne",), yours=False),
    )

    code, out, err = invoke(
        ["cancel", RUN_ID, "--reason", "wrong corpus"],
        runner=runner,
        cwd=tmp_path,
        monkeypatch=monkeypatch,
    )

    assert code == EXIT_REFUSED
    assert runner.ran("gh", "workflow", "run") == []
    assert "refused  nothing_admitted_to_stop" in err
    assert f"gh run cancel {SUBMIT_RUN_ID}" in err
    # Collapsed, because the refusal is wrapped to the terminal and where the sentence
    # breaks is a function of how long the run id in front of it is.
    assert "an approval would still start it" in " ".join(err.split())
    # On stderr with this verb's other two refusals, and not on stdout where it used to be.
    # A caller that has to read both streams to collect one verb's refusals cannot tell
    # from the exit code which one carried this one.
    assert out == ""


def test_logs_on_a_run_that_has_not_started_says_so_rather_than_printing_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: dispatch and print whatever comes back.

    What comes back is an empty tail, and an empty tail reads as a run that is producing no
    output -- which is a fault -- rather than as a run that has not begun. Same bytes, two
    meanings, and the wrong one sends somebody to debug their training script.
    """
    runner = submission(
        status="waiting",
        conclusion=None,
        admission=None,
        pending=parked_at("run-approval-lead", reviewers=("grant-matherne",), yours=False),
    )

    code, out, _ = invoke(["logs", RUN_ID], runner=runner, cwd=tmp_path, monkeypatch=monkeypatch)

    assert code == EXIT_OK
    assert runner.ran("gh", "workflow", "run") == []
    assert "has printed nothing yet" in out
    assert "parked at an approval gate" in out


@pytest.mark.parametrize("verb", ["status", "logs"])
def test_a_run_the_window_does_not_reach_is_refused_rather_than_asked_after(
    verb: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**The only path in this binary that spent money on a guess, and it looked read-only.**

    This used to dispatch, and the argument for that was the fast path's floor: it may add
    speed and may never remove an answer, an old run is genuinely not joinable to a dispatch,
    and the run may still be burning a GPU. Every clause of that is true and it is an
    argument about ``cancel``, which is why ``cancel`` still does it and there is a case
    below holding it there.

    It is the wrong argument for a question. Pasting a run id out of a transcript older than
    the artifact window into ``status`` is an ordinary thing to do, and what it bought was a
    dispatched ``cancel-run.yml``, a runner, and up to the poll ceiling of a silent terminal
    before answering about a run that ended last month. A malformed id was refused for
    nothing, so the whole cost fell on ids that look right.

    Mutation: dispatch anyway, on either verb. The expensive path stops being the default
    and becomes a flag, which is the only shape that lets the cheap wrong answer be cheap.
    """
    runner = github()

    code, out, err = invoke([verb, RUN_ID], runner=runner, cwd=tmp_path, monkeypatch=monkeypatch)

    assert code == EXIT_REFUSED
    assert runner.ran("gh", "workflow", "run") == []
    assert "refused  run_id_not_found" in err
    assert out == ""


def test_the_refusal_says_a_run_it_cannot_find_may_still_be_a_real_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: say the run does not exist.

    It might not, and it might have finished a month ago. Those are different facts and
    nothing local can separate them, so the refusal that reports one of them as the other is
    worse than the dispatch it replaced -- somebody told their run is gone stops looking for
    it. What this has to carry instead is the window it searched, that a real run can sit
    outside it, and the flag that buys the certain answer.
    """
    runner = github()

    _, _, err = invoke(["status", RUN_ID], runner=runner, cwd=tmp_path, monkeypatch=monkeypatch)
    said = " ".join(err.split())
    minutes = -(-int(report_ceiling_seconds()) // 60)

    assert "carries this run id" in said
    assert "may well be a real run" in said
    assert "Nothing was dispatched" in said
    assert f"--ask-aws to ask anyway, which dispatches {CANCEL_WORKFLOW}" in said
    assert f"gives up after {minutes} minutes" in said


@pytest.mark.parametrize("verb", ["status", "logs"])
def test_ask_aws_buys_the_answer_the_refusal_named(
    verb: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The remedy has to be a remedy, which means it reaches the same answer as before.

    Mutation: refuse and offer nothing. AWS is the only thing that still knows what an old
    run did, so a refusal with no way past it would take an answer away rather than move its
    price onto whoever asked for it. What the flag restores is exactly the old path, dispatch
    and all, and ``stop`` stays false on both verbs.
    """
    runner = github()

    code, out, _ = invoke(
        [verb, RUN_ID, "--ask-aws"], runner=runner, cwd=tmp_path, monkeypatch=monkeypatch
    )

    assert code == EXIT_OK, out
    assert runner.ran("gh", "workflow", "run") != []
    assert dispatched_fields(runner)["stop"] == "false"
    assert dispatched_fields(runner)["run_id"] == RUN_ID


def test_cancel_still_asks_aws_about_a_run_it_cannot_find_and_takes_no_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**The half of the old behaviour that was right, kept where it was right.**

    Mutation: refuse an unfindable id on ``cancel`` too, for symmetry. Symmetry is not the
    property here. ``status`` and ``logs`` are questions and the worst a refusal costs is a
    second command; ``cancel`` is an instruction, and an id the window does not reach may
    name a job running now on eight A100s. Refusing to stop it leaves the researcher asking
    somebody with an AWS credential, which is the arrangement this whole tool exists to
    avoid. So ``cancel`` pays the runner every time and is offered no flag to opt out of it.
    """
    runner = github()

    code, _, err = invoke(
        ["cancel", RUN_ID, "--reason", "wrong corpus"],
        runner=runner,
        cwd=tmp_path,
        monkeypatch=monkeypatch,
    )

    assert code == EXIT_OK
    assert runner.ran("gh", "workflow", "run") != []
    assert dispatched_fields(runner)["stop"] == "true"
    # Said before the wait rather than after it, so the reader knows why this one is slow.
    assert "carries this run id" in err
    assert "--ask-aws" not in build_parser_and_verbs()[1]["cancel"].format_usage()


def test_an_uncertain_run_this_did_find_is_still_asked_after_without_a_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The three ways to be uncertain are not one way, and only one of them is a refusal.

    Mutation: refuse every ``UNSURE``. An admission job that ended at an unknown point is an
    uncertainty about a run this found -- there is a workflow run, a compiled manifest and a
    page to link -- and ``submit-run.yml`` writes where a run went only after admission
    answers, so it may have started something. Asking AWS is the honest next question there
    and the researcher should not have to know a flag to get it. Finding nothing at all is
    the only case where the dispatch was a guess.
    """
    runner = submission(conclusion="failure", admission="failure")

    code, out, err = invoke(["status", RUN_ID], runner=runner, cwd=tmp_path, monkeypatch=monkeypatch)

    assert code == EXIT_OK, out + err
    assert runner.ran("gh", "workflow", "run") != []
    assert "run_id_not_found" not in err
    assert "does not say whether it got as far as starting the run" in " ".join(out.split())


# ---------------------------------------------------------------------------------------
# one answer, whether or not you name the run
# ---------------------------------------------------------------------------------------

#: What the approvals endpoint carries after a lead declines. Copied in shape from the real
#: rejection on workflow run 31094100261, which is the one that showed the two views
#: disagreeing: ``philote-dev`` rejected ``run_019fd6a8-96e1`` at ``run-approval-lead`` on
#: 2026-08-06 with a sentence in the box.
REJECTED_AT_THE_GATE: tuple[dict[str, Any], ...] = (
    {
        "state": "rejected",
        "user": {"login": "philote-dev"},
        "comment": "the envelope proof already produced the message, so nothing needs to run",
        "comment_created_at": "2099-01-01T00:04:00Z",
    },
)

#: Every state a run can be in that GitHub alone can settle, as the fixture arguments that
#: produce it. The four states past admission -- queued for a machine, running, finished, and
#: placed nowhere -- are deliberately absent: they live in Batch, no reader on this side can
#: tell them apart, and ``ADMITTED`` is the one honest word for all four.
STATES_GITHUB_CAN_SETTLE: Final = {
    "PENDING_APPROVAL": {"status": "waiting", "conclusion": None, "admission": None},
    "COMPILING": {"status": "in_progress", "conclusion": None, "admission": None},
    "ADMITTED": {"conclusion": "success", "admission": "success"},
    "DECLINED": {
        "conclusion": "failure",
        "admission": "failure",
        "approvals": REJECTED_AT_THE_GATE,
    },
    "REFUSED": {"conclusion": "failure", "admission": "skipped"},
    "CANCELLED": {"conclusion": "cancelled", "admission": "skipped"},
}


def state_in_the_listing(output: str) -> str:
    """The second column of the first row, which is the word a listing reader reads."""
    return output.splitlines()[0].split()[1]


def state_in_the_heading(output: str) -> str:
    """The same word in the single-run view, which is the first line it prints."""
    return output.splitlines()[0].split()[1]


@pytest.mark.parametrize("expected", sorted(STATES_GITHUB_CAN_SETTLE))
def test_one_run_reads_the_same_whether_or_not_you_name_it(
    expected: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: change the state either view derives, in either direction.

    **THEY DISAGREED, AND THE DISAGREEMENT WAS MEASURED RATHER THAN IMAGINED.** On
    2026-08-06 ``edullm status`` listed ``run_019fd6a8-96e1`` as ``DECLINED`` and
    ``edullm status run_019fd6a8-96e1`` called the same run ``REFUSED``. One of those sends
    a researcher to ask the lead who said no and the other sends them to read a workflow log
    for a defect that is not there, and the tool printed both within a minute of each other.

    Two readers of one fact is the arrangement that produced it: the listing gated its
    decline lookup on the submission's own state and the single-run view gated it on the
    admission job's conclusion, and the account marks that job ``failure`` where the suite's
    fixtures marked it ``skipped``. :func:`declined_at_the_gate` is now the one place either
    asks.

    Held over every state GitHub can settle rather than over the one that broke, because
    the property is that these two views cannot diverge and not that this row was patched.
    """
    runner = submission(**STATES_GITHUB_CAN_SETTLE[expected])  # type: ignore[arg-type]
    _, listing, _ = invoke(["status"], runner=runner, cwd=tmp_path, monkeypatch=monkeypatch)
    _, named, error = invoke(
        ["status", RUN_ID],
        runner=submission(**STATES_GITHUB_CAN_SETTLE[expected]),  # type: ignore[arg-type]
        cwd=tmp_path,
        monkeypatch=monkeypatch,
    )

    assert state_in_the_listing(listing) == expected, listing
    assert state_in_the_heading(named) == expected, named + error


def test_the_listing_never_says_a_run_is_queued_when_it_cannot_know_that(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: return ``SUBMITTED`` from ``submission_state`` for a succeeded workflow.

    **THE LAST FALSE WORD, AND IT WAS FALSE ABOUT EIGHT ROWS OUT OF TEN.** Read against
    Batch on 2026-08-06, the eight runs this listing called ``SUBMITTED`` were five
    succeeded, one failed, and two Batch held no record of at all. Not one of them was
    queued, which is the single thing the word invites a reader to conclude, and the
    conclusion it invites is to wait.

    The assertion is on the word rather than on the sentence beside it, because the word is
    what a reader takes away from a table and it is what somebody pastes into a thread.
    """
    runner = submission()

    code, out, err = invoke(["status"], runner=runner, cwd=tmp_path, monkeypatch=monkeypatch)

    assert code == EXIT_OK, out + err
    assert state_in_the_listing(out) == ADMITTED
    assert "SUBMITTED" not in out
    assert runner.ran("gh", "workflow", "run") == []


def test_the_listing_says_where_githubs_knowledge_of_a_run_stops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: print the table and nothing under it.

    Renaming the word stops the listing claiming an outcome. It does not tell a reader where
    to get one, and a reader who has just learned that this view cannot answer their question
    is exactly the reader who needs the verb that can, with its price attached. Both halves
    are asserted: that the boundary is named, and that the command past it is named with what
    it costs -- an unnamed cost is how somebody Ctrl-Cs a dispatch at ninety seconds.
    """
    code, out, err = invoke(
        ["status"], runner=submission(), cwd=tmp_path, monkeypatch=monkeypatch
    )
    said = " ".join(out.split())

    assert code == EXIT_OK, out + err
    assert f"{ADMITTED} is where GitHub stops knowing" in said
    assert "finished an hour ago reads exactly like one still queued" in said
    assert "edullm status <run-id> asks AWS" in said
    assert "spends a runner" in said


def test_a_listing_that_has_reached_no_boundary_carries_no_paragraph_about_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: print the boundary sentence under every listing.

    A listing of submissions still compiling or parked at a gate is wholly about things that
    are still happening on GitHub, so every word of it is true of the run as well and there
    is no boundary to warn about. A paragraph that appears whatever the table says is a
    paragraph a reader learns to skip in a week, and the week after that they skip the one
    that mattered.
    """
    runner = submission(status="waiting", conclusion=None, admission=None)

    code, out, err = invoke(["status"], runner=runner, cwd=tmp_path, monkeypatch=monkeypatch)

    assert code == EXIT_OK, out + err
    assert state_in_the_listing(out) == "PENDING_APPROVAL"
    assert "GitHub stops knowing" not in out
    assert out.strip().splitlines()[-1].startswith("run_")


def test_the_heading_of_one_run_does_not_contradict_the_batch_status_beneath_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: restore ``SUBMITTED`` as the word for a succeeded submission workflow.

    **THE TWO HALVES OF ONE SCREEN SAID DIFFERENT THINGS ABOUT ONE RUN.** Measured against
    the platform on 2026-08-06, ``edullm status run_019fd676-62f0`` opened with
    ``run_019fd676-62f0  SUBMITTED  1h02m`` and then printed ``| Status | `SUCCEEDED` |``
    eight lines further down, in the same output, from the same invocation.

    The assertion is that the heading's word is not one of Batch's, rather than that it is
    any particular word. A heading using Batch's vocabulary reads as a Batch status whatever
    it was meant to mean, and the table below is where Batch's actual answer goes.
    """
    runner = submission(log=SUCCEEDED_REPORT_LOG)

    code, out, err = invoke(["status", RUN_ID], runner=runner, cwd=tmp_path, monkeypatch=monkeypatch)

    assert code == EXIT_OK, out + err
    assert state_in_the_heading(out) not in BATCH_JOB_STATUSES, out.splitlines()[0]
    assert "| Status | `SUCCEEDED` |" in out


@pytest.mark.parametrize("admission", ["failure", "skipped"])
def test_a_run_a_lead_declined_spends_no_runner_however_the_gated_job_is_marked(
    admission: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: ask about a decline only where the admission job is absent or ``skipped``.

    That was the gate, and this account does not produce that shape. A rejected deployment
    review leaves the gated job with an empty ``steps`` list and a conclusion of ``failure``
    -- read off workflow run 31094100261 -- so a declined run fell past every decline branch
    into the honest-uncertainty tail and dispatched ``cancel-run.yml`` to describe a Batch
    job the gate had already prevented. That cost two minutes and six seconds on 2026-08-06
    and came back saying the report named no section this verb reads, because there was no
    job to report on.

    Both markings are driven, because which one GitHub uses is GitHub's business and the
    gate is upstream of admission either way. ``skipped`` is what the suite assumed until
    now and it has to keep working.
    """
    runner = submission(
        conclusion="failure", admission=admission, approvals=REJECTED_AT_THE_GATE
    )

    code, out, err = invoke(["status", RUN_ID], runner=runner, cwd=tmp_path, monkeypatch=monkeypatch)

    assert code == EXIT_OK, out + err
    assert runner.ran("gh", "workflow", "run") == [], "a runner was spent on a run nobody ran"
    assert state_in_the_heading(out) == "DECLINED"
    assert "philote-dev" in out
    assert "It did not fail and nothing about it is broken" in " ".join(out.split())
    assert "nothing was dispatched to answer this." in out


def test_a_run_that_failed_on_its_own_is_still_not_reported_as_declined(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other half, and the one the widened gate could have broken.

    Mutation: read any badly-ended submission as a decline without asking the approvals
    endpoint. The decline branch now runs before the admission job is looked at, so a
    submission that genuinely broke reaches it first -- and a compile refusal reported as
    "a lead said no" would send somebody to ask a person who never saw it. Only
    ``/approvals`` separates the two, and an empty answer has to mean nobody did.
    """
    runner = submission(conclusion="failure", admission="failure", approvals=())

    code, out, err = invoke(["status", RUN_ID], runner=runner, cwd=tmp_path, monkeypatch=monkeypatch)

    assert code == EXIT_OK, out + err
    assert state_in_the_heading(out) == "REFUSED"
    assert "declined" not in out.lower()
    # Still uncertain, so it still asks. A failed admission job says nothing about whether
    # it got as far as starting the run, and that has to stay true.
    assert runner.ran("gh", "workflow", "run") != []


def test_the_admission_job_is_named_the_way_the_workflow_names_it() -> None:
    """A seam test, because the REST API exposes no job key and this has to match by name.

    Mutation: rename the job in ``submit-run.yml``. The jobs endpoint would stop carrying
    the name this looks for, every run would read as "no admission job", and the binary
    would confidently report admitted runs as never sent -- and ``cancel`` would refuse to
    stop them. Cheap to pin, expensive to discover.
    """
    workflow = yaml.safe_load(
        (PROJECT_ROOT / ".github" / "workflows" / SUBMIT_WORKFLOW).read_text(encoding="utf-8")
    )
    declared = {job.get("name") for job in workflow["jobs"].values() if isinstance(job, dict)}

    assert ADMISSION_JOB in declared
