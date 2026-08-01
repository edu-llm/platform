"""The report that asks the question the lineage record cannot.

``ResultManifest`` has no field for "a checkpoint was expected and none was found", and an
empty ``checkpoints`` tuple already means "this run was never expected to checkpoint". So a
run that took OLMo-core's ``/tmp`` default trains for hours, writes nothing anybody can
reach, exits zero, and is recorded as an unqualified success. This tool is what notices.

**The three ways it could lie are what these tests are about.** It could call an outage an
empty prefix, which turns a credentials problem into a false accusation. It could skip a run
whose workload profile has since been renamed, which is a run quietly left out of a report
about runs being quietly left out. And it could report every run with no checkpoints,
including the ones that were never supposed to have any, which is the report becoming noise
nobody reads.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "tools"))

from find_runs_that_saved_nothing import (
    ReportInputError,
    RunCheckpointState,
    checkpoint_states,
    render,
)

from edullm_platform.config import load_yaml
from edullm_platform.contracts.admission import IntentRecord
from edullm_platform.contracts.workload import WorkloadCatalog


@pytest.fixture(scope="module")
def catalog() -> WorkloadCatalog:
    return load_yaml(PROJECT_ROOT / "config" / "workload-catalog.yaml", WorkloadCatalog)


def intent(workload_profile: str) -> IntentRecord:
    source = (
        PROJECT_ROOT
        / "fixtures"
        / "evidence"
        / "phase-2"
        / "lineage"
        / "records"
        / "intent"
        / "run_019fa446-8a4e-7094-9e29-d44fffbd2491.json"
    )
    loaded: Any = json.loads(source.read_text(encoding="utf-8"))
    if isinstance(loaded, str):
        loaded = json.loads(loaded)
    loaded["manifest"]["workload_profile"] = workload_profile
    loaded["manifest"]["compute_profile"] = "gpu-1xa10g"
    return IntentRecord.model_validate(loaded)


def test_a_run_whose_profile_promises_nothing_is_not_reported_as_having_saved_nothing(
    catalog: WorkloadCatalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mutation: report every run with an empty checkpoint prefix.

    ``olmo-core-check-cpu`` carries no checkpoint contract, so writing no checkpoint is the
    correct outcome for it and not a defect. A report that listed those alongside the real
    failures would be mostly correct entries, which is how a report stops being read.
    """
    monkeypatch.setattr(
        "find_runs_that_saved_nothing._objects_under", lambda *a, **k: 0
    )

    states = checkpoint_states([intent("olmo-core-check-cpu")], catalog)

    assert states == []


def test_a_run_that_promised_a_checkpoint_and_wrote_none_is_reported(
    catalog: WorkloadCatalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The case the tool exists for, and the one nothing else on the platform reports."""
    monkeypatch.setattr(
        "find_runs_that_saved_nothing._objects_under", lambda *a, **k: 0
    )

    states = checkpoint_states([intent("olmo-core-train-1gpu")], catalog)

    assert len(states) == 1
    assert states[0].saved_nothing
    assert states[0].prefix.endswith("/checkpoints/")

    report = render(states)
    assert "Wrote nothing" in report
    assert "EDULLM_CHECKPOINT_DIR" in report, (
        "the report names the failure and not the fix, which is the half a reader needs"
    )


def test_an_outage_is_not_reported_as_an_empty_prefix(
    catalog: WorkloadCatalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE ONE THAT MATTERS. Mutation: treat any non-zero exit from the CLI as zero objects.

    ``aws s3 ls`` exits 1 with no output for a prefix that holds nothing, and exits 1 with a
    message when it could not ask. Collapsing the two turns an expired credential into an
    accusation that somebody's twelve-hour run saved nothing -- which is the exact false
    alarm this tool exists to prevent, pointed the wrong way.

    Not hypothetical: the credentials on the machine this was written on expired while it
    was being written, and this is the branch that fired.
    """

    def refused(*_: object, **__: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="Refresh token rejected"
        )

    monkeypatch.setattr("find_runs_that_saved_nothing.subprocess.run", refused)

    with pytest.raises(ReportInputError, match="could not list"):
        checkpoint_states([intent("olmo-core-train-1gpu")], catalog)


def test_an_empty_prefix_is_zero_objects_rather_than_an_error(
    catalog: WorkloadCatalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other side of the same branch: a genuinely empty prefix must read as empty.

    ``aws s3 ls`` says nothing at all for a prefix that holds nothing, and exits 1 doing it.
    A tool that treated the exit code alone as failure would refuse to report the very
    condition it was written to find.
    """

    def empty(*_: object, **__: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="")

    monkeypatch.setattr("find_runs_that_saved_nothing.subprocess.run", empty)

    states = checkpoint_states([intent("olmo-core-train-1gpu")], catalog)

    assert states[0].objects == 0


def test_a_run_naming_a_renamed_profile_is_said_out_loud_rather_than_dropped(
    catalog: WorkloadCatalog,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Mutation: skip an unknown profile silently, which is one `continue` away.

    Every workload was renamed out of ``-smoke``, and the stored records deliberately keep
    the old names -- a lineage record states what a run was submitted as and carries a
    digest over its own bytes. So a report reading historical records meets profiles the
    catalog no longer has, and dropping them without a word would be a run quietly left out
    of a report about runs being quietly left out.
    """
    monkeypatch.setattr(
        "find_runs_that_saved_nothing._objects_under", lambda *a, **k: 0
    )

    states = checkpoint_states([intent("olmo-core-train-smoke")], catalog)

    assert states == []
    assert "olmo-core-train-smoke" in capsys.readouterr().err


def test_the_report_says_nothing_is_wrong_rather_than_printing_an_empty_table() -> None:
    """A report with no rows should read as an answer, not as a broken tool."""
    assert "nothing here to be wrong" in render([])


def test_a_run_that_saved_something_is_reported_separately_from_one_that_did_not() -> None:
    """Both halves, because "no failures" and "no runs" are different answers.

    A reader who sees only the failures cannot tell whether the absence of a section means
    everything is fine or nothing was checked.
    """
    states = [
        RunCheckpointState("run_a", "memory-split", "olmo-core-train-1gpu", "s3://x/a/", 0),
        RunCheckpointState("run_b", "memory-split", "olmo-core-train-1gpu", "s3://x/b/", 12),
    ]

    report = render(states)

    assert "Wrote nothing" in report
    assert "Wrote something" in report
    assert "run_a" in report
    assert "run_b" in report
