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
    ACKNOWLEDGEMENTS_PATH,
    CheckpointAcknowledgements,
    CommandLineObjectStore,
    ReportInputError,
    RunCheckpointState,
    _load_acknowledgements,
    _load_outcomes,
    checkpoint_states,
    main,
    render,
)

from edullm_platform.checkpoints import CheckpointState
from edullm_platform.config import load_yaml
from edullm_platform.contracts.admission import IntentRecord
from edullm_platform.contracts.lifecycle import AttemptTerminalState
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

    ``olmo-core-check`` carries no checkpoint contract, so writing no checkpoint is the
    correct outcome for it and not a defect. A report that listed those alongside the real
    failures would be mostly correct entries, which is how a report stops being read.
    """
    states = checkpoint_states(
        [intent("olmo-core-check")], catalog, store=FakeObjectStore()
    )

    assert states == []


def test_a_run_that_promised_a_checkpoint_and_wrote_none_is_reported(
    catalog: WorkloadCatalog,
) -> None:
    """The case the tool was written for, and the one nothing else on the platform reports."""
    states = checkpoint_states(
        [intent("olmo-core-train")], catalog, store=FakeObjectStore()
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
        [intent("olmo-core-train")], catalog, store=store_holding((0, STUB))
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
        [intent("olmo-core-train")], catalog, store=store_holding((0, FULL_STEP))
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
        [intent("olmo-core-train")],
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
        [intent("olmo-core-train")],
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

    states = checkpoint_states([intent("olmo-core-train")], catalog)

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
        checkpoint_states([intent("olmo-core-train")], catalog)


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


def described(
    run_id: str,
    state: CheckpointState,
    objects: int,
    *,
    outcome: AttemptTerminalState | None = None,
    outcome_known: bool = False,
    acknowledged: str | None = None,
) -> RunCheckpointState:
    return RunCheckpointState(
        run_id=run_id,
        team=TEAM,
        workload_profile="olmo-core-train",
        prefix=CHECKPOINTS,
        objects=objects,
        state=state,
        detail="what the reader found",
        outcome=outcome,
        outcome_known=outcome_known,
        acknowledged=acknowledged,
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
    loaded["manifest"]["workload_profile"] = "olmo-core-train"
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


# ----------------------------------------------------------------------------------------
# Which runs the report is entitled to hold a prefix against
# ----------------------------------------------------------------------------------------


def write_result(root: Path, run_id: str, outcome: str) -> None:
    """One result record, in the shape the lifecycle recorder writes."""
    results = root / "result"
    results.mkdir(exist_ok=True)
    (results / f"{run_id}.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "run_id": run_id,
                "attempt_id": "att_019fa910-13ef-7af8-ad90-81b03811c034",
                "outcome": outcome,
                "output_prefixes": [output_prefix(team=TEAM, run_id=run_id)],
                "checkpoints": [],
                "wandb_run": None,
                "retention_class": "standard",
                "completed_at": "2026-08-01T10:37:04.700000Z",
            }
        ),
        encoding="utf-8",
    )


def test_a_run_the_platform_recorded_as_failed_is_listed_and_not_held_against_anything() -> None:
    """THE ONE THAT MATTERS. Mutation: judge every contracted run, whatever it ended as.

    That is what this did, and it is why the nightly was red on fourteen runs that had
    nothing to do with checkpointing. They died at ``wandb.init()`` on a credential that was
    wrong until it was rotated, inside the checkpointer's own teardown, and on an evaluator
    fetching a file that is not served over HTTP. All three are recorded as failures with an
    exit code, so repeating them here said nothing new and buried the one run that mattered.

    Listed, not dropped. A run that leaves the report entirely is a run nobody looks at, and
    this tool exists because of a run nobody looked at.
    """
    states = [
        described(
            "run_failed",
            CheckpointState.UNCOMMITTED,
            1,
            outcome=AttemptTerminalState.FAILED,
            outcome_known=True,
        ),
    ]

    assert not states[0].judged

    report = render(states)

    assert "## Not asked about" in report
    assert "run_failed" in report, "a run that vanishes from the report is one nobody reads"
    assert "## Wrote something that will not load" not in report


def test_a_run_recorded_as_a_success_is_still_read_out_of_the_bucket() -> None:
    """Mutation: believe the result record, since it says the run succeeded.

    The outcome decides whether to ask, and the prefix decides the answer. Collapsing the two
    would delete the entire point: a run that trains for twelve hours into ``/tmp``, exits
    zero and is recorded as an unqualified success is the failure nothing else reports.
    """
    states = [
        described(
            "run_ok",
            CheckpointState.ABSENT,
            0,
            outcome=AttemptTerminalState.SUCCEEDED,
            outcome_known=True,
        ),
    ]

    assert states[0].judged

    report = render(states)

    assert "## Wrote nothing" in report
    assert "## Not asked about" not in report


def test_a_run_with_no_result_record_has_not_finished_and_is_not_judged() -> None:
    """A run still going has not written its first checkpoint yet, which is not a failure.

    This was the other half of what the report could not tell apart, and it would have called
    the live twelve-hour run a silent failure for its first two hundred steps.
    """
    states = [described("run_going", CheckpointState.ABSENT, 0, outcome=None, outcome_known=True)]

    assert not states[0].judged
    assert "no result record" in render(states)


def test_with_no_result_records_at_all_every_run_is_judged_as_before() -> None:
    """Mutation: treat "no result tree" as "no run succeeded", which passes every night.

    The nightly reader role cannot read ``result/`` until the stack is applied from a laptop,
    so this is the state the check runs in today. A report that answered a missing permission
    by knowing less and going green would be the silent failure it exists to find, turned on
    itself. ``outcome_known`` is what keeps the two apart.
    """
    states = [described("run_unknown", CheckpointState.UNCOMMITTED, 1)]

    assert not states[0].outcome_known
    assert states[0].judged
    assert "## Wrote something that will not load" in render(states)


def test_the_absence_of_a_result_tree_is_not_the_same_as_an_empty_one(tmp_path: Path) -> None:
    """``None`` and ``{}`` mean opposite things and the loader keeps them apart.

    An empty mapping is "these runs finished and none of them succeeded". ``None`` is "nobody
    looked". A loader returning ``{}`` for both would let a missing sync scope the report down
    to nothing and report success.
    """
    assert _load_outcomes(tmp_path) is None

    (tmp_path / "result").mkdir()

    assert _load_outcomes(tmp_path) == {}

    write_result(tmp_path, RUN_ID, "succeeded")

    assert _load_outcomes(tmp_path) == {RUN_ID: AttemptTerminalState.SUCCEEDED}


def test_a_failed_run_does_not_fail_the_nightly_but_a_succeeded_one_does(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The exit code is the whole of the signal, so the scope has to reach it.

    Same stub prefix in both halves. What changes is what the platform recorded, and that is
    what decides whether this is a finding or a run that already reported its own failure.
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
    loaded["manifest"]["workload_profile"] = "olmo-core-train"
    loaded["manifest"]["compute_profile"] = "gpu-1xa10g"
    (records / f"{RUN_ID}.json").write_text(json.dumps(loaded), encoding="utf-8")

    monkeypatch.setattr(
        "find_runs_that_saved_nothing.CommandLineObjectStore",
        lambda **_: store_holding((0, STUB)),
    )

    write_result(tmp_path, RUN_ID, "failed")

    assert main(["--lineage-root", str(tmp_path)]) == 0

    write_result(tmp_path, RUN_ID, "succeeded")

    assert main(["--lineage-root", str(tmp_path)]) == 1


# ----------------------------------------------------------------------------------------
# Runs somebody has read, and the ones nobody has
# ----------------------------------------------------------------------------------------


ADJUDICATED = (
    "A --dry-run submitted while proving the submission path, which resolves the config and "
    "trains nothing, so there was never anything to save."
)


def acknowledging(root: Path, *entries: tuple[str, str]) -> Path:
    """An acknowledgement file in the shape the repository's own is in."""
    path = root / "checkpoint-acknowledgements.yaml"
    if not entries:
        path.write_text("schema_version: 1\nacknowledgements: []\n", encoding="utf-8")
        return path
    body = ["schema_version: 1", "acknowledgements:"]
    for run_id, reason in entries:
        body += [
            f"  - run_id: {run_id}",
            f"    reason: {json.dumps(reason)}",
            "    recorded_by: philote-dev",
            '    recorded_at: "2026-08-01T19:05:00.000000Z"',
        ]
    path.write_text("\n".join(body) + "\n", encoding="utf-8")
    return path


def a_lineage_root_holding_one_succeeded_run(root: Path, run_id: str = RUN_ID) -> None:
    records = root / "intent"
    records.mkdir(exist_ok=True)
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
    loaded["manifest"]["workload_profile"] = "olmo-core-train"
    loaded["manifest"]["compute_profile"] = "gpu-1xa10g"
    loaded["run_id"] = run_id
    (records / f"{run_id}.json").write_text(json.dumps(loaded), encoding="utf-8")
    write_result(root, run_id, "succeeded")


def test_an_acknowledged_run_is_still_read_and_still_printed(
    catalog: WorkloadCatalog,
) -> None:
    """Mutation: filter acknowledged runs out before the bucket is read.

    That is the version of this list that goes blind, and it is one line shorter. A run that
    leaves the report is a run nobody looks at, and this tool exists because of a run nobody
    looked at -- so an acknowledgement changes the exit code and nothing else. The prefix is
    still read out of the account, so the report goes on saying what is there.
    """
    states = checkpoint_states(
        [intent("olmo-core-train")],
        catalog,
        store=FakeObjectStore(),
        acknowledgements={RUN_ID: ADJUDICATED},
    )

    assert len(states) == 1
    assert states[0].saved_nothing, "the finding is still a finding; it is only not news"
    assert states[0].acknowledged == ADJUDICATED
    assert states[0].judged, (
        "an acknowledgement must not change whether the question applies, only whether the "
        "answer is held against the build"
    )
    assert not states[0].held_against_the_build


def test_an_acknowledged_run_carries_its_reason_into_the_report() -> None:
    """The justification is printed beside the run, because a list nobody reads is a cutoff.

    An entry whose reason lives only in a config file is one a reader of the report cannot
    check, and the only thing separating this mechanism from "ignore runs before August" is
    that every entry states what was looked at.
    """
    states = [
        described(
            RUN_ID,
            CheckpointState.ABSENT,
            0,
            outcome=AttemptTerminalState.SUCCEEDED,
            outcome_known=True,
            acknowledged=ADJUDICATED,
        )
    ]

    report = render(states)

    assert "## Read and adjudicated" in report
    assert ADJUDICATED in report
    assert "## Wrote nothing" not in report, (
        "an adjudicated run listed among the findings puts the job's one open question back "
        "underneath a row somebody has already answered"
    )
    assert "1 finished successfully and have been read and adjudicated" in report


def test_a_run_nobody_has_acknowledged_still_turns_the_job_red(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """THE ONE THAT MATTERS. Mutation: acknowledge by date, or by profile, or by team.

    Every wider unit covers runs nobody has looked at, including ones submitted after it was
    written, and this is the assertion that fails when one is substituted. The list here
    names a real run and the offender is a different one, which is the shape the morning
    after a new offender appears.
    """
    a_lineage_root_holding_one_succeeded_run(tmp_path)
    acknowledged = acknowledging(
        tmp_path, ("run_019fbce3-ce4b-7067-b8c7-c2cf25e6b667", ADJUDICATED)
    )

    monkeypatch.setattr(
        "find_runs_that_saved_nothing.CommandLineObjectStore",
        lambda **_: store_holding((0, STUB)),
    )

    assert (
        main(["--lineage-root", str(tmp_path), "--acknowledgements", str(acknowledged)]) == 1
    )


def test_the_acknowledged_run_is_the_only_one_the_exit_code_forgives(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The other side of the same branch, so the list cannot pass by forgiving nothing.

    Same prefix and same recorded outcome in both halves of the test above and this one.
    What changes is whether the run's id is written down with a reason beside it.
    """
    a_lineage_root_holding_one_succeeded_run(tmp_path)

    monkeypatch.setattr(
        "find_runs_that_saved_nothing.CommandLineObjectStore",
        lambda **_: store_holding((0, STUB)),
    )

    assert main(["--lineage-root", str(tmp_path), "--acknowledgements", str(acknowledging(tmp_path))]) == 1

    acknowledged = acknowledging(tmp_path, (RUN_ID, ADJUDICATED))

    assert (
        main(["--lineage-root", str(tmp_path), "--acknowledgements", str(acknowledged)]) == 0
    )
    assert "## Read and adjudicated" in capsys.readouterr().out


def test_an_acknowledgement_covering_nothing_is_reported_and_is_not_a_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An entry earns its place by covering a finding, and stops earning it silently.

    A list that only grows stops describing anything, and the entries left in it are the ones
    a later reader trusts least. Reported rather than failed, because a stale entry conceals
    no run -- failing on it would turn tidying into an outage of the job.
    """
    a_lineage_root_holding_one_succeeded_run(tmp_path)
    acknowledged = acknowledging(tmp_path, (RUN_ID, ADJUDICATED))

    monkeypatch.setattr(
        "find_runs_that_saved_nothing.CommandLineObjectStore",
        lambda **_: store_holding((0, FULL_STEP)),
    )

    assert (
        main(["--lineage-root", str(tmp_path), "--acknowledgements", str(acknowledged)]) == 0
    )

    report = capsys.readouterr().out
    assert "## Acknowledgements that cover nothing" in report
    assert RUN_ID in report


def test_a_list_that_does_not_parse_stops_the_report_rather_than_acknowledging_nobody(
    tmp_path: Path,
) -> None:
    """Mutation: swallow the error and carry on with an empty list.

    Both directions of that are wrong and one is worse. A misspelled key that reads as no
    acknowledgements turns a typo into a red job whose cause is a YAML error nobody is shown,
    and the reader spends the morning on a run that was adjudicated weeks ago.
    """
    broken = tmp_path / "checkpoint-acknowledgements.yaml"
    broken.write_text("schema_version: 1\nacknowledgments: []\n", encoding="utf-8")

    with pytest.raises(ReportInputError, match="acknowledgement list"):
        _load_acknowledgements(broken)


def test_no_list_at_all_acknowledges_nothing(tmp_path: Path) -> None:
    """The state a fresh checkout is legitimately in, and the state this repository wants."""
    assert _load_acknowledgements(tmp_path / "absent.yaml").reasons() == {}


def test_a_reason_too_short_to_be_one_is_refused() -> None:
    """"Known issue" is not an adjudication, and the floor is what keeps this a record.

    Carried from ``ImageScanException`` deliberately: the two files answer the same kind of
    question, and the value of either is that a later reader can tell whether the thing was
    understood or waved through.
    """
    with pytest.raises(ValueError, match="at least 40 characters"):
        CheckpointAcknowledgements.model_validate(
            {
                "schema_version": 1,
                "acknowledgements": [
                    {
                        "run_id": RUN_ID,
                        "reason": "known issue",
                        "recorded_by": "philote-dev",
                        "recorded_at": "2026-08-01T19:05:00.000000Z",
                    }
                ],
            }
        )


def test_a_run_cannot_be_acknowledged_twice() -> None:
    """Two entries for one run are two different reasons, and only one of them is read.

    The one that loses is invisible, which makes the file say something nobody agreed to.
    """
    entry = {
        "run_id": RUN_ID,
        "reason": ADJUDICATED,
        "recorded_by": "philote-dev",
        "recorded_at": "2026-08-01T19:05:00.000000Z",
    }

    with pytest.raises(ValueError, match="more than one acknowledgement"):
        CheckpointAcknowledgements.model_validate(
            {"schema_version": 1, "acknowledgements": [entry, dict(entry)]}
        )


def test_the_repositorys_own_list_names_runs_and_states_why() -> None:
    """The file the nightly actually reads, held to what makes it defensible.

    Read here rather than trusted, because the entries are the whole of the argument that
    this is an adjudication and not a date cutoff written one line at a time.
    """
    acknowledgements = _load_acknowledgements(ACKNOWLEDGEMENTS_PATH)

    assert acknowledgements.acknowledgements, (
        "the list is empty, so either the historical run was resolved another way or the "
        "path moved and the nightly is reading nothing"
    )
    for entry in acknowledgements.acknowledgements:
        assert "EDULLM_CHECKPOINT_DIR" in entry.reason or "--dry-run" in entry.reason, (
            f"the reason recorded for {entry.run_id} does not say what the run did with the "
            "checkpoint directory, which is the question the entry is answering"
        )
