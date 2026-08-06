"""The document a machine reads, and the two things it must never be.

WHY THIS EXISTS AT ALL. Every skill under .cursor/skills/ matches on a refusal code, and
before --json the only way to get one was to match the word after "refused" in a wrapped
paragraph. docs-frank/reference/designing-the-cli.md settles the shape: one document on
stdout whatever the outcome, the key names tools/compile_submission.py already writes, a
format_version, and a flag rather than a terminal check.

THE TWO THINGS IT MUST NEVER BE ARE BOTH ASSERTED RATHER THAN INTENDED. It must never carry
the placeholder image digest, because a caller reading image_digest out of a check would be
reading sha256 followed by sixty-four zeroes and could compare it to a real one. And it must
never make check reach a network, because that is the property the whole verb is built on.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from edullm_platform.cli.machine import FORMAT_VERSION
from edullm_platform.cli.main import EXIT_OK, EXIT_REFUSED
from edullm_platform.cli.preflight import DEFERRED_TO_SUBMIT, UNRESOLVED_IMAGE_DIGEST
from edullm_platform.run_history import RUNS_FOR_A_FIGURE, load_run_history
from tests.cli_support import FakeRunner, git_answers, invoke, ok, write_spec

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def checkout(tmp_path: Path, **spec: object) -> tuple[Path, FakeRunner]:
    write_spec(tmp_path, **spec)  # type: ignore[arg-type]
    return tmp_path, FakeRunner(git_answers(tmp_path))


def only_document(out: str) -> dict[str, Any]:
    """The whole of stdout as one JSON document, which is the contract being asserted."""
    parsed = json.loads(out)
    assert isinstance(parsed, dict)
    return parsed


def test_check_json_is_one_document_on_stdout_and_nothing_else(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: keep printing the human blocks alongside the document.

    A caller pipes stdout straight into a parser. One stray line above the brace and every
    skill in this repository stops working, and it stops working with a JSONDecodeError
    rather than with anything a reader could act on.
    """
    root, runner = checkout(tmp_path, compute="gpu-1xa10g")

    code, out, err = invoke(
        ["check", "--json", "--dataset", "regmix-10b-v1", "--experiment", "an-experiment"],
        runner=runner,
        cwd=root,
        monkeypatch=monkeypatch,
    )

    assert code == EXIT_OK, out + err
    document = only_document(out)
    assert document["format_version"] == FORMAT_VERSION
    assert document["verb"] == "check"
    assert document["refused"] is False
    assert document["refusals"] == []


def test_check_json_reaches_no_network(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Mutation: fill submitter in from `gh api user` when the config file has nobody.

    check answers in a fraction of a second and asks nothing, which is what makes it the
    verb somebody runs half a dozen times while editing a spec and the verb that works on a
    login node with no egress. A serializer is not a reason to give that up.
    """
    root, runner = checkout(tmp_path, compute="gpu-1xa10g")

    code, out, err = invoke(
        ["check", "--json", "--dataset", "regmix-10b-v1", "--experiment", "an-experiment"],
        runner=runner,
        cwd=root,
        monkeypatch=monkeypatch,
    )

    assert code == EXIT_OK, out + err
    assert runner.ran("gh") == []


def test_a_refused_check_still_emits_one_document_and_still_exits_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: emit the document only on the clear path, or exit 0 because it printed.

    Refused is the common case and the interesting one. The exit code is the published
    interface and the document is the detail, and a caller needs both to agree: exit 1 with
    a document naming which codes fired.
    """
    root, runner = checkout(tmp_path, compute="gpu-1xa10g")

    code, out, err = invoke(
        [
            "check",
            "--json",
            "--dataset",
            "a-corpus-nothing-registers",
            "--experiment",
            "an-experiment",
        ],
        runner=runner,
        cwd=root,
        monkeypatch=monkeypatch,
    )

    assert code == EXIT_REFUSED, out + err
    document = only_document(out)
    assert document["refused"] is True
    assert "unregistered_dataset" in [refusal["code"] for refusal in document["refusals"]]
    assert all({"code", "detail"} == set(refusal) for refusal in document["refusals"])


def test_the_placeholder_digest_is_nowhere_in_the_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: dump the manifest as pydantic serializes it and stop there.

    cli/preflight.py builds a real RunManifest with UNRESOLVED_IMAGE_DIGEST standing in for
    the one field a laptop cannot fill, and its docstring promises the value is never
    printed. A human renderer kept that promise by printing "resolved at submit, from the
    commit above". A serializer that dumped the model would break it silently, and what it
    would publish is a well-formed digest naming nothing, which a caller could compare
    against a real one and get a false answer from.
    """
    root, runner = checkout(tmp_path, compute="gpu-1xa10g")

    code, out, err = invoke(
        ["check", "--json", "--dataset", "regmix-10b-v1", "--experiment", "an-experiment"],
        runner=runner,
        cwd=root,
        monkeypatch=monkeypatch,
    )

    assert code == EXIT_OK, out + err
    assert UNRESOLVED_IMAGE_DIGEST not in out
    document = only_document(out)
    assert document["manifest"] is not None
    assert document["manifest"]["image_digest"] is None
    assert document["manifest_sha256"] is None


def test_the_document_carries_every_check_a_laptop_could_not_make(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: leave `deferred` out, so a clean check reads as a clean bill of health.

    docs-frank/working/adarsh-rajesh-first-run.md is a transcript of what it costs when a
    submitter believes a clean preflight means a submission will go through. An agent
    believes it harder and faster than a person does.

    Held against ``DEFERRED_TO_SUBMIT`` rather than against a list written here, because
    which questions are deferred moves and this document has to move with it. What decides
    that list is ``tests/test_check_refuses_what_compile_refuses.py``, which fails when a
    compile-time refusal is neither asked locally nor named there -- so a list spelled here
    would be the copy that stayed behind.
    """
    root, runner = checkout(tmp_path, compute="gpu-1xa10g")

    code, out, err = invoke(
        ["check", "--json", "--dataset", "regmix-10b-v1", "--experiment", "an-experiment"],
        runner=runner,
        cwd=root,
        monkeypatch=monkeypatch,
    )

    assert code == EXIT_OK, out + err
    document = only_document(out)
    deferred = [(entry["code"], entry["detail"]) for entry in document["deferred"]]

    assert deferred == [(code_said, detail) for code_said, detail in DEFERRED_TO_SUBMIT]
    assert "no_published_image" in {code_said for code_said, _ in deferred}, (
        "the check the transcript is about is not among the deferred ones, so this document "
        "tells a submitter nothing about whether their commit was built"
    )


def test_the_history_block_says_when_it_was_measured_and_over_how_many_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: drop `measured_at`, or publish the sentence without the counts.

    The digest is a committed file. An install from an old tag reads the reading that tag
    carried and has no way to know it is old, so a caller deciding whether to believe a
    median needs the date as a timestamp rather than scraped back out of prose -- which is
    the same split this document already makes for money and for refusal codes.

    The counts are asserted beside it because a median with no denominator is the thing
    run_history.py exists to refuse. Read against the committed digest rather than a
    fixture, so a digest rebuilt with a different shape fails here rather than shipping.
    """
    root, runner = checkout(tmp_path, compute="gpu-1xa10g")

    code, out, err = invoke(
        ["check", "--json", "--dataset", "regmix-10b-v1", "--experiment", "an-experiment"],
        runner=runner,
        cwd=root,
        monkeypatch=monkeypatch,
    )

    assert code == EXIT_OK, out + err
    history = only_document(out)["history"]
    packaged = load_run_history(PROJECT_ROOT / "config")
    assert packaged is not None, "this repository carries a digest and this case reads it"

    assert history["measured_at"] == packaged.built_at.isoformat()
    assert datetime.fromisoformat(history["measured_at"]).tzinfo is not None
    # The same date the sentence carries, so a caller printing `said` and a caller
    # branching on the timestamp cannot tell a reader two different things.
    assert packaged.built_at.date().isoformat() in history["said"]
    assert isinstance(history["succeeded"], int)
    assert isinstance(history["failed"], int)
    assert history["succeeded"] >= RUNS_FOR_A_FIGURE
    assert str(history["succeeded"]) in history["said"]


def test_money_is_a_string_and_not_a_float(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: `float(cost.maximum_compute_cost_usd)`.

    presentation.py's own header records the defect this repeats: a CLI that rounded
    differently from the approver page would have a submitter and a lead reading two prices
    for one run. Binary floating point is a second arithmetic, and the whole path carries
    money as base-ten text for exactly that reason. Asserted as a type rather than as a
    value, because the value is the catalog's and this test is not about the catalog.
    """
    root, runner = checkout(tmp_path, compute="gpu-1xa10g")

    code, out, err = invoke(
        ["check", "--json", "--dataset", "regmix-10b-v1", "--experiment", "an-experiment"],
        runner=runner,
        cwd=root,
        monkeypatch=monkeypatch,
    )

    assert code == EXIT_OK, out + err
    cost = only_document(out)["cost"]
    assert isinstance(cost["maximum_compute_cost_usd"], str)
    assert isinstance(cost["hourly_rate_usd"], str)
    assert isinstance(cost["nodes"], int)


def test_the_document_is_byte_identical_piped_or_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: sort keys only sometimes, or indent by terminal width.

    Nothing in this binary calls isatty and nothing here starts. Two runs of one command
    against one tree produce one string, which is what makes a pasted transcript the
    transcript somebody else sees and what makes a golden test possible at all.
    """
    root, runner = checkout(tmp_path, compute="gpu-1xa10g")
    argv = ["check", "--json", "--dataset", "regmix-10b-v1", "--experiment", "an-experiment"]

    first = invoke(argv, runner=runner, cwd=root, monkeypatch=monkeypatch)
    second = invoke(argv, runner=runner, cwd=root, monkeypatch=monkeypatch)

    assert first[1] == second[1]
    assert "\x1b" not in first[1]


def test_check_without_the_flag_still_prints_the_paragraphs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: make JSON the primary form, or switch on whether stdout is a terminal.

    designing-the-cli.md rules both out in the same paragraph, and the reason is that
    `edullm check > note.txt` and `edullm check` would then disagree about what was checked.
    This is the assertion that keeps the flag a flag.
    """
    root, runner = checkout(tmp_path, compute="gpu-1xa10g")

    code, out, err = invoke(
        ["check", "--dataset", "regmix-10b-v1", "--experiment", "an-experiment"],
        runner=runner,
        cwd=root,
        monkeypatch=monkeypatch,
    )

    assert code == EXIT_OK, out + err
    assert out.startswith("checked against ")
    assert "no refusals. edullm submit will dispatch this." in out


STATUS_RUNS = """{"workflow_runs": [
  {"id": 41, "status": "completed", "conclusion": "success",
   "created_at": "2026-08-05T10:00:00Z",
   "html_url": "https://github.com/edu-llm/platform/actions/runs/41"}
]}"""

COMPILED = """{
  "run_id": "run_019fcf3c-9878-7c1a-8f00-1c2d3e4f5a6b",
  "submitter": "caiiris",
  "approval_class": "routine",
  "approving_environment": "run-approval-lead",
  "manifest_sha256": "1f0e9d8c7b6a5948372615043f2e1d0c9b8a7960514233221100ffeeddccbbaa",
  "experiment": "an-experiment",
  "team": "memory-split",
  "manifest": {"fanout": null}
}"""

ADMITTED_JOBS = """{"jobs": [
  {"name": "Submit the approved manifest to admission", "conclusion": "success"}
]}"""


def status_runner(tmp_path: Path) -> FakeRunner:
    """A single admitted submission, answered entirely from GitHub.

    Admitted is the case worth building the fixture around, because it is the one where
    read_run_facts stops and reports that AWS would have to be asked. Everything the
    document has to carry is populated on that branch.

    ONE CALLABLE ON ("gh", "api") RATHER THAN A TUPLE PER ENDPOINT, WHICH IS THE PATTERN
    tests/test_cli_run_verbs.py ALREADY USES AND IS NOT A STYLE CHOICE. FakeRunner matches a
    prefix of the argv exactly, and `workflow_runs` appends a query string to the path, so a
    key spelling the path without `?per_page=...&event=...&actor=...` matches nothing and the
    fake raises UnexpectedCommandError. Dispatching on `argv[-1]` with `in` is what survives
    a query string.
    """

    def api(argv: tuple[str, ...]) -> Any:
        path = argv[-1]
        if "/workflows/submit-run.yml/runs" in path:
            return ok(STATUS_RUNS)
        if path.endswith("/41/jobs"):
            return ok(ADMITTED_JOBS)
        if path.endswith("/41/approvals"):
            return ok("[]")
        return ok("{}")

    def download(argv: tuple[str, ...]) -> Any:
        destination = Path(argv[argv.index("--dir") + 1])
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "compiled-submission.json").write_text(COMPILED, encoding="utf-8")
        return ok("")

    return FakeRunner(
        {
            ("gh", "api"): api,
            ("gh", "run", "download"): download,
        }
    )  # type: ignore[arg-type]


def test_status_json_answers_one_run_from_github_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: drop `needs_a_dispatch` and let a caller infer it from `admitted`.

    Whether the next question costs a runner is the single fact this verb exists to answer
    cheaply, and it is not the same fact as whether the run was admitted. A run parked at a
    gate is not admitted and needs no dispatch, and an admission job that failed at an
    unknown point is not admitted and does.
    """
    code, out, err = invoke(
        ["status", "--json", "run_019fcf3c-9878-7c1a-8f00-1c2d3e4f5a6b"],
        runner=status_runner(tmp_path),
        cwd=tmp_path,
        monkeypatch=monkeypatch,
    )

    assert code == EXIT_OK, out + err
    document = only_document(out)
    assert document["verb"] == "status"
    assert document["run_id"] == "run_019fcf3c-9878-7c1a-8f00-1c2d3e4f5a6b"
    assert document["admitted"] == "yes"
    assert document["needs_a_dispatch"] is True
    assert document["was_found"] is True
    assert document["submission"]["workflow_run_id"] == 41
    assert document["submission"]["short_run_id"] == "run_019fcf3c-9878"
    assert document["team"] == "memory-split"


def test_status_json_never_dispatches_a_workflow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: dispatch cancel-run.yml and put its markdown in a field.

    THE AWS HALF HAS NO STRUCTURE AND PUBLISHING IT WOULD BE INVENTING ONE. What comes back
    is markdown headings scraped out of a job log, which is precisely why
    designing-the-cli.md puts no --json on logs and cancel. So this publishes the half that
    is structured and names the half that is not, and a caller that wants the AWS answer runs
    the same verb without the flag. It also means --json costs no runner, which matters when
    the caller is a loop.
    """
    runner = status_runner(tmp_path)

    code, out, err = invoke(
        ["status", "--json", "run_019fcf3c-9878-7c1a-8f00-1c2d3e4f5a6b"],
        runner=runner,
        cwd=tmp_path,
        monkeypatch=monkeypatch,
    )

    assert code == EXIT_OK, out + err
    assert runner.ran("gh", "workflow", "run") == []
    assert only_document(out)["aws_report"] is None


def test_status_json_with_no_run_id_lists_the_recent_submissions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: return a bare array rather than a document with a `runs` key.

    `docker ps --format json` emits one object per line rather than an array and the
    maintainers closed the report because the shape had become load-bearing. A top-level
    array here has the same problem one step along: there is nowhere to put format_version,
    so the day a field changes meaning nothing can say so.
    """
    code, out, err = invoke(
        ["status", "--json"],
        runner=status_runner(tmp_path),
        cwd=tmp_path,
        monkeypatch=monkeypatch,
    )

    assert code == EXIT_OK, out + err
    document = only_document(out)
    assert document["format_version"] == FORMAT_VERSION
    assert [run["short_run_id"] for run in document["runs"]] == ["run_019fcf3c-9878"]


def test_a_malformed_run_id_is_a_document_on_stdout_and_still_exits_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: keep printing render_refusals to stderr under --json.

    "One document on stdout whatever the outcome" is the contract, and a refusal is an
    outcome. A caller that has to read stderr to find out why exit 1 happened is back to
    parsing prose, which is the whole thing this flag removes.
    """
    code, out, err = invoke(
        ["status", "--json", "not-a-run-id"],
        runner=FakeRunner({}),
        cwd=tmp_path,
        monkeypatch=monkeypatch,
    )

    assert code == EXIT_REFUSED, out + err
    document = only_document(out)
    assert [refusal["code"] for refusal in document["refusals"]] == ["run_id_not_well_formed"]


DECLINED_RUNS = """{"workflow_runs": [
  {"id": 41, "status": "completed", "conclusion": "failure",
   "created_at": "2026-08-05T10:00:00Z",
   "html_url": "https://github.com/edu-llm/platform/actions/runs/41"}
]}"""

#: What GitHub records against the gated admission job once a review is rejected. **BOTH ARE
#: DRIVEN BECAUSE THE SUITE ASSUMED ONE AND THE ACCOUNT PRODUCES THE OTHER.** This file
#: answered every declined run with ``skipped``, and workflow run 31094100261 -- a real
#: decline by ``philote-dev`` on 2026-08-06 -- carried ``failure`` with an empty ``steps``
#: list, because the gate stopped the job before it ran a line. So the one shape a researcher
#: could actually meet was the one no test could produce, and ``edullm status`` on that run
#: read ``REFUSED`` while the listing beside it read ``DECLINED``.
GATED_JOB_CONCLUSIONS = ("skipped", "failure")


def jobs_with(conclusion: str) -> str:
    return json.dumps(
        {"jobs": [{"name": "Submit the approved manifest to admission", "conclusion": conclusion}]}
    )

REJECTED = """[
  {"state": "rejected", "user": {"login": "alsy7009"},
   "comment": "the 24h bound is a typo, this shape takes an hour",
   "comment_created_at": "2026-08-05T10:04:00Z"}
]"""


def declined_runner(tmp_path: Path, approvals: str, gated: str = "skipped") -> FakeRunner:
    """A submission whose admission job never ran, with the approvals endpoint deciding why.

    THE TWO CASES DIFFER IN ONE ENDPOINT AND IN NOTHING ELSE, WHICH IS THE FINDING. GitHub
    gives a rejected deployment review the same run conclusion it gives a compile refusal,
    so the runs list is byte-identical here between a decline and a crash. Only
    ``/approvals`` can tell them apart.

    ``gated`` is what GitHub records against the admission job, and it is a parameter rather
    than a constant because it varies and the variation is what hid the defect. See
    :data:`GATED_JOB_CONCLUSIONS`.
    """

    def api(argv: tuple[str, ...]) -> Any:
        path = argv[-1]
        if "/workflows/submit-run.yml/runs" in path:
            return ok(DECLINED_RUNS)
        if path.endswith("/41/jobs"):
            return ok(jobs_with(gated))
        if path.endswith("/41/approvals"):
            return ok(approvals)
        return ok("{}")

    def download(argv: tuple[str, ...]) -> Any:
        destination = Path(argv[argv.index("--dir") + 1])
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "compiled-submission.json").write_text(COMPILED, encoding="utf-8")
        return ok("")

    return FakeRunner(
        {("gh", "api"): api, ("gh", "run", "download"): download}
    )  # type: ignore[arg-type]


@pytest.mark.parametrize("gated", GATED_JOB_CONCLUSIONS)
def test_status_json_tells_a_declined_run_from_a_failed_one(
    gated: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: leave both reading REFUSED.

    A lead saying no and a runner dying installing uv produced the same word, the same row and
    the same document, and a researcher reading it went looking for a bug in a submission a
    person had simply declined. Those send somebody to different places, and the second one
    has a name attached to it.

    **AND BOTH MARKINGS OF THE GATED JOB ARE DRIVEN, WHICH IS THE SECOND MUTATION THIS CASE
    NOW HOLDS.** Look for the decline only where that job is absent or ``skipped`` and the
    ``failure`` parameter goes red -- which is the shape this account produces and the one
    that shipped, so the document said ``REFUSED`` about a run a person had declined by name.
    """
    code, out, err = invoke(
        ["status", "--json", "run_019fcf3c-9878-7c1a-8f00-1c2d3e4f5a6b"],
        runner=declined_runner(tmp_path, REJECTED, gated=gated),
        cwd=tmp_path,
        monkeypatch=monkeypatch,
    )

    assert code == EXIT_OK, out + err
    document = only_document(out)
    assert document["submission"]["state"] == "DECLINED"
    assert document["declined"] == {
        "by": "alsy7009",
        "reason": "the 24h bound is a typo, this shape takes an hour",
        "at": "2026-08-05T10:04:00+00:00",
    }
    assert document["admitted"] == "no"
    assert document["needs_a_dispatch"] is False


def test_a_run_nobody_declined_still_reads_as_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other half. A compile refusal is not a decline and must not gain a name.

    ``declined`` is present and null rather than absent, so a caller branching on it gets an
    answer rather than a ``KeyError`` on every run that went the ordinary way.
    """
    code, out, err = invoke(
        ["status", "--json", "run_019fcf3c-9878-7c1a-8f00-1c2d3e4f5a6b"],
        runner=declined_runner(tmp_path, "[]"),
        cwd=tmp_path,
        monkeypatch=monkeypatch,
    )

    assert code == EXIT_OK, out + err
    document = only_document(out)
    assert document["declined"] is None
    assert document["submission"]["state"] == "REFUSED"
    assert "finished without running its admission job" in document["because"]


def test_a_decline_with_no_reason_says_none_was_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GitHub's comment box is optional and every one of the 34 real approvals left it empty.

    So the common case is a decline with no sentence, and "no reason given" is what tells a
    submitter to go and ask. A blank reads as a tool that did not look.
    """
    code, out, err = invoke(
        ["status", "run_019fcf3c-9878-7c1a-8f00-1c2d3e4f5a6b"],
        runner=declined_runner(
            tmp_path, '[{"state": "rejected", "user": {"login": "alsy7009"}, "comment": ""}]'
        ),
        cwd=tmp_path,
        monkeypatch=monkeypatch,
    )

    assert code == EXIT_OK, out + err
    assert "DECLINED" in out
    assert "declined by" in out
    assert "reason" in out and "none given" in out
    # Unwrapped, because the paragraph below the rows is wrapped to the terminal width and
    # asserting the sentence as one line would be asserting the wrap point.
    assert "It did not fail and nothing about it is broken." in " ".join(out.split())
