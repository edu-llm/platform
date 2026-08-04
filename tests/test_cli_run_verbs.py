"""``status``, ``logs`` and ``cancel``: three verbs over one workflow, and one over none.

``cancel-run.yml`` is named "Look at a run, or stop it" and already does all three things --
looking is the default, stopping is the opt-in, and the log tail is printed on every
dispatch including one that goes on to stop the run. So these verbs are three doors into
one workflow, and what is worth testing is that the safe door stays safe: neither
``status`` nor ``logs`` may ever send ``stop=true``.

``status`` with no run id is the exception and takes no dispatch at all. Whether a lead has
tapped yet is the question people ask most often, and GitHub answers it on its own.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from edullm_platform.cli.actions import CANCEL_WORKFLOW
from edullm_platform.cli.main import EXIT_OK, EXIT_REFUSED
from tests.cli_support import FakeRunner, invoke, ok

RUN_ID = "run_019fd2a1-4e07-7a3c-9d55-1b2f8c0e6a41"
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
    return FakeRunner(
        {
            ("gh", "workflow", "run"): ok(""),
            ("gh", "api"): lambda argv: ok(
                json.dumps(CANCEL_RUN)
                if argv[-1].endswith(str(CANCEL_RUN["id"]))
                else json.dumps({"workflow_runs": [CANCEL_RUN]})
            ),
            ("gh", "run", "view"): ok(log),
        }
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
