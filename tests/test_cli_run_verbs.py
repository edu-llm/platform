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
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from edullm_platform.cli.actions import ADMISSION_JOB, CANCEL_WORKFLOW, SUBMIT_WORKFLOW
from edullm_platform.cli.main import EXIT_OK, EXIT_REFUSED
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
    """
    runner = github()

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
    runner = github()

    code, out, _ = invoke(["logs", RUN_ID], runner=runner, cwd=tmp_path, monkeypatch=monkeypatch)

    assert code == EXIT_OK
    assert "step 200   loss  5.9042" in out
    assert "| Status |" not in out
    assert dispatched_fields(runner)["stop"] == "false"


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
    assert "run_019fd2a1" in out
    assert "PENDING_APPROVAL" in out
    assert "an-experiment 9 cells" in out


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
    assert "waits for a runner: tens of seconds, not a moment." in err
    assert runner.ran("gh", "workflow", "run") != []
    assert "| Status | `RUNNABLE` |" in out


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

    code, out, _ = invoke(
        ["cancel", RUN_ID, "--reason", "wrong corpus"],
        runner=runner,
        cwd=tmp_path,
        monkeypatch=monkeypatch,
    )

    assert code == EXIT_REFUSED
    assert runner.ran("gh", "workflow", "run") == []
    assert "refused  nothing_admitted_to_stop" in out
    assert f"gh run cancel {SUBMIT_RUN_ID}" in out
    assert "an approval would still start it" in out


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


def test_a_run_the_search_window_does_not_reach_behaves_exactly_as_it_did_before(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fast path's floor: it can add speed and may never remove an answer.

    Mutation: refuse a run this could not find. GitHub keeps workflow runs and artifacts for
    a bounded window, so an old run simply is not joinable to a dispatch -- and the run it
    names may still be running in AWS. The only safe reading of "not found" is "ask AWS",
    which is what every call did before this existed.
    """
    runner = github()

    code, out, _ = invoke(["status", RUN_ID], runner=runner, cwd=tmp_path, monkeypatch=monkeypatch)

    assert code == EXIT_OK
    assert runner.ran("gh", "workflow", "run") != []
    assert "carries this run id" in out
    assert "| Status | `RUNNABLE` |" in out


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
