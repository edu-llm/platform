"""The report that asks the question the lineage record cannot.

``ResultManifest`` has no field for "a checkpoint was expected and none was found", and an
empty ``checkpoints`` tuple already means "this run was never expected to checkpoint". So a
run that took OLMo-core's ``/tmp`` default trains for hours, writes nothing anybody can
reach, exits zero, and is recorded as an unqualified success. This tool is what notices.

**The middle state is what these tests are mostly about.** The report counted objects until
it was pointed at the account, and a count cannot tell a checkpoint from a fragment of one:
eight runs carrying a checkpoint contract had written exactly one object, ``step0`` holding
``train/rank0.pt`` and nothing else, and all eight were filed as having saved something. Rank
0's trainer state with no weights and no optimizer beside it is not a checkpoint, a resume
from it starts at step zero, and one object read as healthy. Both real shapes are here, keys
copied from ``s3://sbsandbox-intern-edullm-outputs`` rather than imagined.

**The other three ways it could lie are still covered.** It could call an outage an empty
prefix, which turns a credentials problem into a false accusation. It could skip a run whose
workload profile has since been renamed, which is a run quietly left out of a report about
runs being quietly left out. And it could report every run with no checkpoints, including the
ones that were never supposed to have any, which is the report becoming noise nobody reads.
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
    CommandLineObjectStore,
    ReportInputError,
    RunCheckpointState,
    checkpoint_states,
    main,
    render,
)

from edullm_platform.checkpoints import CheckpointState
from edullm_platform.config import load_yaml
from edullm_platform.contracts.admission import IntentRecord
from edullm_platform.contracts.results import output_prefix
from edullm_platform.contracts.workload import WorkloadCatalog
from tests.fake_object_store import FakeObjectStore

RUN_ID = "run_019fa446-8a4e-7094-9e29-d44fffbd2491"
TEAM = "memory-split"
CHECKPOINTS = output_prefix(team=TEAM, run_id=RUN_ID) + "checkpoints/"
KEY = CHECKPOINTS.split("sbsandbox-intern-edullm-outputs/", 1)[1]

#: What eight runs carrying a checkpoint contract actually wrote, whole. Rank 0's trainer
#: state at step zero, 15317 bytes of it, and nothing else in the account.
STUB = ("train/rank0.pt",)

#: What a run that checkpointed writes for each step, keys read off the one prefix in the
#: account that a loader accepts. The byte counts are not reproduced -- three gigabytes per
#: step in a unit test buys nothing, and it is the names that ``dir_is_checkpoint`` reads.
FULL_STEP = (
    ".metadata.json",
    "config.json",
    "data_paths.txt",
    "model_and_optim/.metadata",
    *(f"model_and_optim/__0_{shard}.distcp" for shard in range(8)),
    "train/rank0.pt",
)


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
        / f"{RUN_ID}.json"
    )
    loaded: Any = json.loads(source.read_text(encoding="utf-8"))
    if isinstance(loaded, str):
        loaded = json.loads(loaded)
    loaded["manifest"]["workload_profile"] = workload_profile
    loaded["manifest"]["compute_profile"] = "gpu-1xa10g"
    return IntentRecord.model_validate(loaded)


def store_holding(*steps: tuple[int, tuple[str, ...]]) -> FakeObjectStore:
    """A checkpoint prefix as OLMo-core leaves one, with no ``_SUCCESS`` because it writes none."""
    store = FakeObjectStore()
    for step, names in steps:
        for name in names:
            store.put(f"{KEY}step{step}/{name}", b"tensor bytes", algorithm="CRC32C")
    return store


def test_a_run_whose_profile_promises_nothing_is_not_reported_as_having_saved_nothing(
    catalog: WorkloadCatalog,
) -> None:
    """Mutation: report every run with an empty checkpoint prefix.

    ``olmo-core-check-cpu`` carries no checkpoint contract, so writing no checkpoint is the
    correct outcome for it and not a defect. A report that listed those alongside the real
    failures would be mostly correct entries, which is how a report stops being read.
    """
    states = checkpoint_states(
        [intent("olmo-core-check-cpu")], catalog, store=FakeObjectStore()
    )

    assert states == []


def test_a_run_that_promised_a_checkpoint_and_wrote_none_is_reported(
    catalog: WorkloadCatalog,
) -> None:
    """The case the tool was written for, and the one nothing else on the platform reports."""
    states = checkpoint_states(
        [intent("olmo-core-train-1gpu")], catalog, store=FakeObjectStore()
    )

    assert len(states) == 1
    assert states[0].state is CheckpointState.ABSENT
    assert states[0].saved_nothing
    assert states[0].prefix.endswith("/checkpoints/")

    report = render(states)
    assert "Wrote nothing" in report
    assert "EDULLM_CHECKPOINT_DIR" in report, (
        "the report names the failure and not the fix, which is the half a reader needs"
    )


def test_a_step_directory_holding_only_rank0_is_not_reported_as_having_saved(
    catalog: WorkloadCatalog,
) -> None:
    """THE ONE THAT MATTERS. Mutation: decide by counting objects under the prefix.

    That is what this did, and eight runs in the account are why it stopped. Each wrote
    ``checkpoints/step0/train/rank0.pt`` and nothing else: rank 0's trainer state, no model
    weights, no optimizer state, 15317 bytes against the three gigabytes a step of this model
    takes. A count of one is indistinguishable from a checkpoint, so all eight were filed as
    having saved something and nobody looked again. A resume from any of them starts at step
    zero, which is the twelve hours the report exists to stop somebody losing.
    """
    states = checkpoint_states(
        [intent("olmo-core-train-1gpu")], catalog, store=store_holding((0, STUB))
    )

    assert len(states) == 1
    assert states[0].objects == 1, "the object is there, which is exactly why counting failed"
    assert states[0].state is CheckpointState.UNCOMMITTED
    assert states[0].wrote_something_unloadable
    assert not states[0].saved_nothing, (
        "it wrote, and calling that nothing would send a reader looking for a save-folder "
        "mistake that did not happen"
    )
    assert not states[0].is_loadable


def test_the_shape_a_run_that_checkpointed_writes_is_reported_as_loadable(
    catalog: WorkloadCatalog,
) -> None:
    """The other side of the same branch, so the check cannot pass by refusing everything.

    A verifier that called every prefix unloadable would catch all eight fragments and be
    useless, and the report would be red for ever. This is the layout of the one prefix in the
    account a loader accepts: a ``model_and_optim`` directory of eight shards beside its
    ``.metadata``, the trainer state, and the config the run was started from.
    """
    states = checkpoint_states(
        [intent("olmo-core-train-1gpu")], catalog, store=store_holding((0, FULL_STEP))
    )

    assert len(states) == 1
    assert states[0].state is CheckpointState.COMMITTED
    assert states[0].is_loadable
    assert states[0].objects == 13
    assert "model, optimizer and trainer state" in states[0].detail


def test_a_torn_newest_step_is_not_a_finding_when_an_earlier_one_still_loads(
    catalog: WorkloadCatalog,
) -> None:
    """Mutation: call the prefix unloadable because its highest step directory is torn.

    An attempt reclaimed part way through writing step2 resumes from step1 and keeps going,
    because ``find_checkpoints`` skips a directory failing ``dir_is_checkpoint`` and
    ``latest_checkpoint`` takes the highest of what survives. Reporting that run as having
    nothing to load would have the report and the trainer disagreeing about the one question
    the report is asked, and the trainer is the one that is right.

    The tool does not decide this and must not appear to. It carries what the reader found,
    including the warning about the torn directory, which is the half an operator needs to
    know a write did not finish even though the run is fine.
    """
    states = checkpoint_states(
        [intent("olmo-core-train-1gpu")],
        catalog,
        store=store_holding((0, FULL_STEP), (1, FULL_STEP), (2, STUB)),
    )

    assert states[0].is_loadable
    assert "step1" in states[0].detail
    assert "step2 is newer but unfinished" in states[0].detail


def test_a_prefix_whose_every_step_is_torn_is_a_finding(catalog: WorkloadCatalog) -> None:
    """The other side of the fallback, and the shape eight runs in the account are in.

    Falling back to an earlier complete directory is right, and it must not become "there is
    probably something further down". A prefix where no step directory loads has nothing to
    resume from however many of them there are.
    """
    states = checkpoint_states(
        [intent("olmo-core-train-1gpu")],
        catalog,
        store=store_holding((0, STUB), (1, STUB)),
    )

    assert states[0].wrote_something_unloadable
    assert "step1" in states[0].detail
    assert "no earlier step directory here is one either" in states[0].detail


def test_a_missing_success_marker_is_an_absence_rather_than_an_outage(
    catalog: WorkloadCatalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The CLI's own words for a key that is not there, which the store has to recognise.

    ``checkpoints`` reads an error code off the response rather than off a botocore class,
    which is what lets a reader that is not holding boto3 exist at all. The CLI hands back a
    line of text instead, so the store rebuilds the shape -- and if it rebuilt it wrongly
    every prefix in the account would read as unreachable rather than as empty.
    """

    def answer(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        if "get-object" in command:
            return subprocess.CompletedProcess(
                args=command,
                returncode=254,
                stdout="",
                stderr=(
                    "\naws: [ERROR]: An error occurred (NoSuchKey) when calling the "
                    "GetObject operation: The specified key does not exist.\n"
                ),
            )
        return subprocess.CompletedProcess(
            args=command, returncode=0, stdout='{"Prefix": "teams/"}', stderr=""
        )

    monkeypatch.setattr("find_runs_that_saved_nothing.subprocess.run", answer)

    states = checkpoint_states([intent("olmo-core-train-1gpu")], catalog)

    assert states[0].state is CheckpointState.ABSENT


def test_an_outage_is_not_reported_as_an_empty_prefix(
    catalog: WorkloadCatalog,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mutation: treat any failed CLI call as nothing being there.

    Collapsing the two turns an expired credential into an accusation that somebody's
    twelve-hour run saved nothing, which is this tool's own false alarm pointed the wrong way.

    Not hypothetical: the credentials on the machine this was first written on expired while
    it was being written, and this is the branch that fired.
    """

    def refused(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=command,
            returncode=255,
            stdout="",
            stderr="\naws: [ERROR]: The config profile (sbsandbox) could not be found\n",
        )

    monkeypatch.setattr("find_runs_that_saved_nothing.subprocess.run", refused)

    with pytest.raises(ReportInputError, match="could not"):
        checkpoint_states([intent("olmo-core-train-1gpu")], catalog)


def test_a_code_that_is_not_about_a_missing_object_stops_the_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mutation: treat every code the CLI prints as an absence.

    S3 names a lot of failures the same way it names a missing key, and only three of those
    codes mean the object is not there. ``ExpiredToken`` arrives in the identical shape and
    means the report cannot see the bucket at all, so reading it as an empty prefix would
    accuse every run at once on the morning a credential lapses.
    """

    def expired(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=command,
            returncode=254,
            stdout="",
            stderr=(
                "\naws: [ERROR]: An error occurred (ExpiredToken) when calling the "
                "ListObjectsV2 operation: The provided token has expired.\n"
            ),
        )

    monkeypatch.setattr("find_runs_that_saved_nothing.subprocess.run", expired)

    with pytest.raises(ReportInputError, match="ExpiredToken"):
        CommandLineObjectStore().list_objects_v2(Bucket="b", Prefix="teams/")


def test_the_report_cannot_write_to_the_bucket_it_is_auditing() -> None:
    """Mutation: implement put_object so the Protocol is satisfied the easy way.

    The store has to name every call the Protocol names, and the tempting way to do that is
    a working one. A report that can write to the prefix it is judging is a report that can
    manufacture the evidence it reports, which is worth one line to make impossible.
    """
    with pytest.raises(ReportInputError, match="never write"):
        CommandLineObjectStore().put_object(Bucket="b", Key="k", Body=b"")


def test_a_run_naming_a_renamed_profile_is_said_out_loud_rather_than_dropped(
    catalog: WorkloadCatalog,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Mutation: skip an unknown profile silently, which is one `continue` away.

    Every workload was renamed out of ``-smoke``, and the stored records deliberately keep
    the old names -- a lineage record states what a run was submitted as and carries a
    digest over its own bytes. So a report reading historical records meets profiles the
    catalog no longer has, and dropping them without a word would be a run quietly left out
    of a report about runs being quietly left out.
    """
    states = checkpoint_states(
        [intent("olmo-core-train-smoke")], catalog, store=FakeObjectStore()
    )

    assert states == []
    assert "olmo-core-train-smoke" in capsys.readouterr().err


def test_the_report_says_nothing_is_wrong_rather_than_printing_an_empty_table() -> None:
    """A report with no rows should read as an answer, not as a broken tool."""
    assert "nothing here to be wrong" in render([])


def described(run_id: str, state: CheckpointState, objects: int) -> RunCheckpointState:
    return RunCheckpointState(
        run_id=run_id,
        team=TEAM,
        workload_profile="olmo-core-train-1gpu",
        prefix=CHECKPOINTS,
        objects=objects,
        state=state,
        detail="what the reader found",
    )


def test_the_three_states_are_three_sections_rather_than_two() -> None:
    """Mutation: fold the fragments back in beside the checkpoints that load.

    "Wrote something" was the heading that hid this, and a reader scanning it had no way to
    tell the one resumable run from the eight that are not. The headline counts all three
    because the absence of a section otherwise reads as "none of that happened" when it can
    equally mean nothing was checked.
    """
    states = [
        described("run_a", CheckpointState.ABSENT, 0),
        described("run_b", CheckpointState.UNCOMMITTED, 1),
        described("run_c", CheckpointState.COMMITTED, 13),
    ]

    report = render(states)

    assert "## Wrote nothing" in report
    assert "## Wrote something that will not load" in report
    assert "## Wrote a checkpoint that will load" in report
    assert "1 wrote nothing, 1 wrote something no loader will accept, and 1 can be" in report
    for run_id in ("run_a", "run_b", "run_c"):
        assert run_id in report
    # The fragment is described where a reader is looking for failures, not filed with the
    # run that succeeded.
    assert report.index("run_b") < report.index("run_c")


def test_a_fragment_exits_non_zero_so_the_nightly_can_gate_on_it(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Mutation: keep exiting non-zero only for a prefix that is empty.

    The exit code is the whole of the signal the nightly job reads. A run that wrote a
    fragment is no more resumable than one that wrote nothing, so an exit of zero here would
    put the check in the workflow and leave it unable to fail for the case it was added for.
    """
    records = tmp_path / "intent"
    records.mkdir()
    source = (
        PROJECT_ROOT
        / "fixtures"
        / "evidence"
        / "phase-2"
        / "lineage"
        / "records"
        / "intent"
        / f"{RUN_ID}.json"
    )
    loaded = json.loads(source.read_text(encoding="utf-8"))
    if isinstance(loaded, str):
        loaded = json.loads(loaded)
    loaded["manifest"]["workload_profile"] = "olmo-core-train-1gpu"
    loaded["manifest"]["compute_profile"] = "gpu-1xa10g"
    (records / f"{RUN_ID}.json").write_text(json.dumps(loaded), encoding="utf-8")

    monkeypatch.setattr(
        "find_runs_that_saved_nothing.CommandLineObjectStore",
        lambda **_: store_holding((0, STUB)),
    )

    assert main(["--lineage-root", str(tmp_path)]) == 1
    assert "Wrote something that will not load" in capsys.readouterr().out

    monkeypatch.setattr(
        "find_runs_that_saved_nothing.CommandLineObjectStore",
        lambda **_: store_holding((0, FULL_STEP)),
    )

    assert main(["--lineage-root", str(tmp_path)]) == 0
