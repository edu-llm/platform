"""What one Batch state change says about a run, before anybody words it.

Every case here reads a committed fixture rather than a dictionary built in the test. The
fixtures are real events with the account id masked, so a field this reader depends on
cannot quietly stop being in the envelope AWS actually sends.

The two readers that are not the envelope are filled with fakes, so nothing here needs a
credential and nothing here reaches the account. What the fakes stand in for is checked
against the real thing in the plan's own findings rather than at test time, because a test
that needed the bucket would be a test nobody runs.
"""

from __future__ import annotations

import io
import json
from decimal import Decimal
from pathlib import Path

import pytest

from edullm_platform.contracts.workload import compute_maximum_compute_cost_usd
from edullm_platform.notifications.facts import Catalogs, RunEndedFacts, read_run_ended

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EVENTS = PROJECT_ROOT / "fixtures" / "events"


@pytest.fixture(scope="module")
def catalogs() -> Catalogs:
    return Catalogs.load(PROJECT_ROOT / "config")


def envelope(name: str) -> dict[str, object]:
    return json.loads((EVENTS / f"{name}.sanitized.json").read_text(encoding="utf-8"))


def test_a_succeeded_run_yields_the_person_the_experiment_and_the_money(
    catalogs: Catalogs,
) -> None:
    facts = read_run_ended(envelope("batch-succeeded"), catalogs=catalogs)

    assert facts is not None
    assert facts.run_id == "run_019fd3cc-79a0-70f5-aa29-6db4a2061a61"
    assert facts.outcome == "succeeded"
    assert facts.person == "Aryan Verma"
    assert facts.team == "scratch"
    assert facts.experiment == "plan-b-phase0-100m-superbpe-eval"
    assert facts.compute_profile == "gpu-1xa10g"
    assert facts.queue_name == "sbsandbox-intern-edullm-gpu"
    assert facts.seconds_spent == 63
    assert facts.exit_code == 0
    assert facts.cells_total is None


def test_the_spent_figure_is_the_attempt_window_against_the_catalog_rate(
    catalogs: Catalogs,
) -> None:
    """The join the relay has to do before it posts, and the reason it cannot forward.

    gpu-1xa10g is $1.0060 an hour in config/workload-catalog.yaml and the attempt ran
    63 seconds, so the run cost under two cents. Forwarding the event as it arrives carries
    neither figure.
    """
    facts = read_run_ended(envelope("batch-succeeded"), catalogs=catalogs)

    assert facts is not None
    assert facts.hourly_rate_usd == Decimal("1.0060")
    assert facts.spent_usd == Decimal("0.02")


def test_the_authorised_figure_is_the_bound_the_approval_bought(catalogs: Catalogs) -> None:
    """Two hours at one attempt on one cell, which is what the job definition asked for."""
    facts = read_run_ended(envelope("batch-succeeded"), catalogs=catalogs)

    assert facts is not None
    assert facts.authorised_usd == Decimal("2.01")


def test_a_failed_run_carries_its_exit_code_and_its_window(catalogs: Catalogs) -> None:
    facts = read_run_ended(envelope("batch-failed"), catalogs=catalogs)

    assert facts is not None
    assert facts.outcome == "failed"
    assert facts.exit_code == 1
    assert facts.seconds_spent == 2520
    assert facts.spent_usd == Decimal("0.70")


def test_a_run_that_has_not_ended_owes_nobody_a_message(catalogs: Catalogs) -> None:
    """Mutation: return facts for every state.

    Three of the six states a run passes through are not endings, and a message on each of
    them is three messages per run in a channel that gets nine a day.
    """
    assert read_run_ended(envelope("batch-running"), catalogs=catalogs) is None


def test_an_event_from_somewhere_else_owes_nobody_a_message(catalogs: Catalogs) -> None:
    """Mutation: trust the envelope.

    The rule is one deploy away from being widened and the queue is reachable by anything
    with permission to write to it, so the source is checked here as well as in the pattern.
    """
    foreign = envelope("batch-succeeded") | {"source": "aws.ec2"}

    assert read_run_ended(foreign, catalogs=catalogs) is None


def test_a_job_whose_name_is_not_a_run_id_owes_nobody_a_message(catalogs: Catalogs) -> None:
    document = envelope("batch-succeeded")
    document["detail"]["jobName"] = "somebody-elses-job"  # type: ignore[index]

    assert read_run_ended(document, catalogs=catalogs) is None


def test_a_run_the_envelope_names_nobody_for_has_no_person_without_a_reader(
    catalogs: Catalogs,
) -> None:
    """Five of the thirty-five have no wandb_username, so their runs carry no name in the
    envelope, and with no reader there is nowhere else to look.

    Said rather than guessed. Naming the team as though it were a person, or falling back
    to the run id, would put a wrong name on a message about somebody's money. The next
    test is the one that closes the gap for those five.
    """
    document = envelope("batch-succeeded")
    document["detail"]["container"]["environment"] = [  # type: ignore[index]
        entry
        for entry in document["detail"]["container"]["environment"]  # type: ignore[index]
        if entry["name"] != "WANDB_USERNAME"
    ]
    facts = read_run_ended(document, catalogs=catalogs)

    assert facts is not None
    assert facts.person is None


class FakeIntentReader:
    """One object, returned under whatever key is asked for. The whole of the S3 seam."""

    def __init__(self, body: bytes | None = None, raises: Exception | None = None) -> None:
        self.body = body
        self.raises = raises
        self.arguments: list[dict[str, object]] = []

    def get_object(self, **arguments: object) -> dict[str, object]:
        self.arguments.append(dict(arguments))
        if self.raises is not None:
            raise self.raises
        return {"Body": io.BytesIO(self.body or b"")}


def test_the_intent_record_names_one_of_the_five_the_envelope_cannot(
    catalogs: Catalogs,
) -> None:
    """THE WHOLE REASON THIS READER EXISTS. Mutation: drop the intent read.

    BritishAmericqn is one of the five roster members with no wandb_username, so every run
    they submit carries no person-shaped value in the envelope at all. The intent record
    carries their GitHub login, and the roster carries a display name against every login it
    holds, so the record answers for thirty-five where the envelope answers for thirty.
    """
    document = envelope("batch-succeeded")
    document["detail"]["container"]["environment"] = [  # type: ignore[index]
        entry
        for entry in document["detail"]["container"]["environment"]  # type: ignore[index]
        if entry["name"] != "WANDB_USERNAME"
    ]
    reader = FakeIntentReader(b'{"submitter": "BritishAmericqn"}')

    facts = read_run_ended(document, catalogs=catalogs, intent_reader=reader)

    assert facts is not None
    assert facts.person == "Benjamin Royston"
    assert reader.arguments == [
        {
            "Bucket": "sbsandbox-intern-edullm-lineage",
            "Key": "intent/run_019fd3cc-79a0-70f5-aa29-6db4a2061a61.json",
        }
    ]


def test_the_key_is_derived_from_the_job_name_so_nothing_is_listed(
    catalogs: Catalogs,
) -> None:
    """Mutation: search the prefix instead.

    The job name is the run id and the key is that id, so this costs one GetObject. A reader
    that listed would need s3:ListBucket on the lineage store, which is the grant that lets
    something enumerate every run this platform has ever admitted.
    """
    reader = FakeIntentReader(b'{"submitter": "aryanjverma"}')

    read_run_ended(envelope("batch-succeeded"), catalogs=catalogs, intent_reader=reader)

    assert [set(call) for call in reader.arguments] == [{"Bucket", "Key"}]


def test_the_record_wins_where_the_two_sources_disagree(catalogs: Catalogs) -> None:
    """The envelope says aryan-jaden-verma and the record says somebody else.

    They agree in practice. Where they cannot, the sealed record is the one admission wrote
    and the environment variable is an attribution the submission path set, so the record is
    what a message about somebody's money should carry.
    """
    reader = FakeIntentReader(b'{"submitter": "ericrcwu001"}')

    facts = read_run_ended(
        envelope("batch-succeeded"), catalogs=catalogs, intent_reader=reader
    )

    assert facts is not None
    assert facts.person == "Eric Wu"


@pytest.mark.parametrize(
    "reader",
    [
        FakeIntentReader(raises=RuntimeError("AccessDenied")),
        FakeIntentReader(raises=RuntimeError("NoSuchKey")),
        FakeIntentReader(b"not json"),
        FakeIntentReader(b'{"submitter": ""}'),
        FakeIntentReader(b"{}"),
    ],
)
def test_a_read_that_did_not_work_falls_back_and_never_raises(
    catalogs: Catalogs, reader: FakeIntentReader
) -> None:
    """MUTATION: LET THE READ RAISE. Every one of these dead-letters a message otherwise.

    A refusal, an absent record, a body that is not JSON, an empty submitter, a record with
    no submitter at all. None of them is a reason to lose a message about a run that
    happened, and every one of them leaves the envelope's own answer in place.
    """
    facts = read_run_ended(
        envelope("batch-succeeded"), catalogs=catalogs, intent_reader=reader
    )

    assert facts is not None
    assert facts.person == "Aryan Verma"


def test_a_queue_no_execution_target_names_leaves_the_money_unknown(
    catalogs: Catalogs,
) -> None:
    """The rule matches sixteen queues and config/execution-targets.yaml names fourteen.

    An unmatched queue is a fact rather than a crash. Raising here would dead-letter the
    delivery for a run that demonstrably happened, and a message saying the cost is unknown
    is worth more than no message.
    """
    document = envelope("batch-succeeded")
    document["detail"]["jobQueue"] = (  # type: ignore[index]
        "arn:aws:batch:us-east-1:<aws-account-id>:job-queue/somebody-elses-queue"
    )
    facts = read_run_ended(document, catalogs=catalogs)

    assert facts is not None
    assert facts.compute_profile is None
    assert facts.hourly_rate_usd is None
    assert facts.spent_usd is None
    assert facts.queue_name == "somebody-elses-queue"


def test_the_facts_are_frozen() -> None:
    """Mutation: make the dataclass mutable.

    A renderer that can edit its own inputs is a renderer whose output depends on the order
    the messages were built in.
    """
    assert RunEndedFacts.__dataclass_params__.frozen is True


def test_the_cancellation_marker_is_the_one_the_recorder_reads() -> None:
    """Mutation: change one of the two spellings.

    Both modules decide whether a FAILED job was really a cancellation, and they must decide
    it the same way. Restated rather than imported so each module reads on its own, and
    compared here so the copies cannot drift into two answers.
    """
    from edullm_platform.lifecycle_projection import CANCELLATION_REASON_MARKERS
    from edullm_platform.notifications.facts import CANCELLATION_MARKERS

    assert CANCELLATION_MARKERS == CANCELLATION_REASON_MARKERS


def test_the_lineage_bucket_is_the_one_the_recorder_writes_to() -> None:
    """Mutation: rename one of the two.

    The recorder puts the intent record's neighbours in this bucket and this reads one out
    of it. Two spellings with nothing between them is how a reader ends up asking a bucket
    that does not exist and reporting it as a run nobody submitted.
    """
    from edullm_platform.lifecycle_handler import (
        DEFAULT_LINEAGE_BUCKET as RECORDER_BUCKET,
    )
    from edullm_platform.lifecycle_handler import (
        LINEAGE_BUCKET_VARIABLE as RECORDER_VARIABLE,
    )
    from edullm_platform.notifications.facts import (
        DEFAULT_LINEAGE_BUCKET,
        LINEAGE_BUCKET_VARIABLE,
    )

    assert DEFAULT_LINEAGE_BUCKET == RECORDER_BUCKET
    assert LINEAGE_BUCKET_VARIABLE == RECORDER_VARIABLE


def test_the_field_read_out_of_the_intent_record_is_one_the_contract_declares() -> None:
    """THE READ IS UNTYPED AND THIS IS WHAT STANDS IN FOR THE TYPE. Mutation: misspell it.

    facts.py reads the record as JSON rather than as an IntentRecord, so that a whole
    RunManifest does not enter this function's zip and so that a manifest field the message
    never reads cannot fail validation and dead-letter it. What that gives up is the
    compiler noticing a renamed field, and this is what replaces it. The key is derived the
    same way admission answers it back to the state machine, so that is compared too.
    """
    from edullm_platform.contracts.admission import IntentRecord
    from edullm_platform.notifications.facts import SUBMITTER_FIELD, intent_key

    assert SUBMITTER_FIELD in IntentRecord.model_fields
    assert intent_key("run_019fd3cc-79a0-70f5-aa29-6db4a2061a61") == (
        "intent/run_019fd3cc-79a0-70f5-aa29-6db4a2061a61.json"
    )


def test_an_array_parent_carries_the_cell_counts(catalogs: Catalogs) -> None:
    facts = read_run_ended(envelope("batch-array-parent-failed"), catalogs=catalogs)

    assert facts is not None
    assert facts.cells_total == 20
    assert facts.cells_failed == 1
    assert facts.cells_succeeded == 19
    assert facts.compute_profile == "gpu-1xl40s"


def test_an_array_parent_prices_the_ceiling_across_every_cell(catalogs: Catalogs) -> None:
    """The parent event carries no attempts, so there is no window in it to price.

    What it does carry is the ceiling: the attempt timeout, the retry count and the array
    size. gpu-1xl40s is $1.861 an hour, ninety minutes, one attempt, twenty cells.
    """
    facts = read_run_ended(envelope("batch-array-parent-failed"), catalogs=catalogs)

    assert facts is not None
    assert facts.authorised_usd == Decimal("55.83")


def on_nodes(catalogs: Catalogs, profile: str, nodes: int) -> Catalogs:
    """The same catalogs with one profile widened to several machines.

    Built rather than committed, because ``config/workload-catalog.yaml`` is a reviewed file
    describing what this account actually offers and every one of its seventeen profiles is a
    single machine today. Editing it to make a test pass would be claiming a shape nobody
    provisioned. The field is there because multi-node is the shape this platform grows into,
    and the arithmetic has to be right before the first profile carries it rather than after.
    """
    catalog = catalogs.catalog
    widened = tuple(
        entry.model_copy(update={"nodes": nodes}) if entry.name == profile else entry
        for entry in catalog.compute_profiles
    )
    return Catalogs(
        inventory=catalogs.inventory,
        catalog=catalog.model_copy(update={"compute_profiles": widened}),
        targets=catalogs.targets,
    )


def test_the_money_counts_every_machine_the_profile_asks_for(catalogs: Catalogs) -> None:
    """MUTATION: DROP ``nodes`` FROM EITHER PRODUCT. Nothing in the catalog would go red.

    ``compute_maximum_compute_cost_usd`` is this platform's definition of what a run was
    approved to spend and it is ``rate x nodes x hours x attempts x cells``.
    ``run_costs.py`` prices a real window the same way, ``rate x nodes x duration``. This
    reader had neither factor, so it agreed with both only because all seventeen profiles in
    ``config/workload-catalog.yaml`` are one machine and anything times one is itself.

    That is a check that cannot fail, and the day somebody adds the two-node profile the
    field exists for it becomes a message understating a sweep by the node count, in the one
    place a lead reads a number at 2am to decide whether to approve a spend. Four nodes here
    rather than two, so a transposed factor cannot pass by coincidence.
    """
    widened = on_nodes(catalogs, "gpu-1xa10g", 4)
    one = read_run_ended(envelope("batch-succeeded"), catalogs=catalogs)
    four = read_run_ended(envelope("batch-succeeded"), catalogs=widened)

    assert one is not None and four is not None
    assert one.spent_usd == Decimal("0.02")
    assert one.authorised_usd == Decimal("2.01")
    assert four.spent_usd == Decimal("0.07")
    assert four.authorised_usd == Decimal("8.05")
    assert four.hourly_rate_usd == one.hourly_rate_usd, (
        "the rate is the per-machine figure the catalog records and stays it. The node count "
        "belongs in the product, not folded into the rate the message may one day print."
    )


def test_the_ceiling_is_the_platforms_own_arithmetic_rather_than_a_second_copy(
    catalogs: Catalogs,
) -> None:
    """MUTATION: reimplement the product here. Two definitions, one of them silently stale.

    The ceiling a message reports and the ceiling admission approved have to be the same
    number computed the same way, or a lead reconciling a message against a decision record
    finds a discrepancy that is nobody's run. So this holds the reader's answer against
    ``compute_maximum_compute_cost_usd``, the function the contract itself validates against,
    for a fan-out on several machines where every one of the five factors is more than one.
    """
    widened = on_nodes(catalogs, "gpu-1xl40s", 4)
    facts = read_run_ended(envelope("batch-array-parent-failed"), catalogs=widened)

    assert facts is not None
    assert facts.authorised_usd == compute_maximum_compute_cost_usd(
        Decimal("1.8610"),
        4,
        Decimal("1.5"),
        1,
        20,
    )
    assert facts.authorised_usd == Decimal("223.32")


def test_an_unread_fan_out_has_no_spend_rather_than_a_spend_of_zero(
    catalogs: Catalogs,
) -> None:
    """MUTATION: PRICE THE UNREAD PARENT AT ZERO. It is the cheapest-looking wrong answer.

    Twenty cells that each ran half an hour and a listing nobody made produce the same empty
    attempts array. `$0.00 spent` reads as a sweep that cost nothing, which is a claim about
    the run; None reads as a figure nobody has, which is a claim about the reader. Only the
    second is true.
    """
    facts = read_run_ended(envelope("batch-array-parent-failed"), catalogs=catalogs)

    assert facts is not None
    assert facts.seconds_spent == 0
    assert facts.spent_usd is None
    assert facts.cells_measured is None
    assert facts.failed_cell_indexes is None


class FakeCellLister:
    """One page per status, which is the whole of what the cell reader asks for."""

    def __init__(self, by_status: dict[str, list[dict[str, object]]]) -> None:
        self.by_status = by_status
        self.arguments: list[dict[str, object]] = []

    def list_jobs(self, **arguments: object) -> dict[str, object]:
        self.arguments.append(dict(arguments))
        return {"jobSummaryList": self.by_status.get(str(arguments["jobStatus"]), [])}


def cells(parent: str) -> FakeCellLister:
    """Nineteen cells that ran half an hour each, and cell 13 that died at fifteen minutes."""
    return FakeCellLister(
        {
            "SUCCEEDED": [
                {
                    "jobId": f"{parent}:{index}",
                    "status": "SUCCEEDED",
                    "startedAt": 1785965337885,
                    "stoppedAt": 1785965337885 + 1_800_000,
                }
                for index in range(20)
                if index != 13
            ],
            "FAILED": [
                {
                    "jobId": f"{parent}:13",
                    "status": "FAILED",
                    "startedAt": 1785965337885,
                    "stoppedAt": 1785965337885 + 900_000,
                }
            ],
        }
    )


ARRAY_PARENT_JOB_ID = "77b6ed8a-6d2b-5f56-c540-cfb4e8f5545d"


def test_a_read_fan_out_says_what_it_spent_and_which_cell_died(catalogs: Catalogs) -> None:
    """WHAT THE PARENT EVENT COULD NOT SAY AND BATCH CAN. Mutation: drop the cell read.

    Nineteen cells at 1800 seconds and one at 900 is 35,100 seconds, and gpu-1xl40s is
    $1.8610 an hour, so the sweep cost $18.14 against a ceiling of $55.83. Neither figure is
    derivable from the parent event, which carries an empty attempts array and a count.

    Two calls rather than one, because ListJobs defaults to RUNNING when no status is given.
    Both terminal statuses are asked for, and a terminal parent guarantees every child is in
    one of them.
    """
    lister = cells(ARRAY_PARENT_JOB_ID)

    facts = read_run_ended(
        envelope("batch-array-parent-failed"), catalogs=catalogs, cell_lister=lister
    )

    assert facts is not None
    assert facts.seconds_spent == 35100
    assert facts.spent_usd == Decimal("18.14")
    assert facts.authorised_usd == Decimal("55.83")
    assert facts.cells_measured == 20
    assert facts.failed_cell_indexes == (13,)
    assert [call["jobStatus"] for call in lister.arguments] == ["SUCCEEDED", "FAILED"]
    assert {call["arrayJobId"] for call in lister.arguments} == {ARRAY_PARENT_JOB_ID}


def test_a_refused_cell_listing_leaves_the_spend_unknown_and_never_raises(
    catalogs: Catalogs,
) -> None:
    """THE DIRECTION TO BE WRONG IN, and the same one Task 6 takes for checkpoints.

    A listing that was refused is not evidence that a sweep was free. Every failure here is
    an unknown spend and an unnamed set of cells, and none of them loses the message: the
    counts are in the event and are worth posting on their own.
    """

    class RefusingLister:
        def list_jobs(self, **arguments: object) -> dict[str, object]:
            raise RuntimeError("AccessDeniedException")

    facts = read_run_ended(
        envelope("batch-array-parent-failed"), catalogs=catalogs, cell_lister=RefusingLister()
    )

    assert facts is not None
    assert facts.spent_usd is None
    assert facts.cells_measured is None


def test_a_single_run_never_asks_batch_anything(catalogs: Catalogs) -> None:
    """Mutation: read the cells for every run.

    A run that is not an array carries its own attempts in the event, so the spend is already
    exact and a Batch call would buy nothing and cost a request on every one of the nine
    messages a day.
    """
    lister = cells(ARRAY_PARENT_JOB_ID)

    read_run_ended(envelope("batch-succeeded"), catalogs=catalogs, cell_lister=lister)

    assert lister.arguments == []


def test_an_array_child_owes_nobody_a_message(catalogs: Catalogs) -> None:
    """THE WHOLE FAN-OUT DECISION, AS ONE ASSERTION. Mutation: drop the index check.

    A twenty-checkpoint sweep is one event with one result. At one message per cell the eval
    group's normal workflow was fifty-seven of the ninety-two messages a day, and rolling it
    up is what makes keeping the run-ended message affordable at all. It costs latency: the
    sweep says nothing until its last cell lands.
    """
    assert read_run_ended(envelope("batch-array-child-failed"), catalogs=catalogs) is None


class FakeLister:
    """One page of a listing, which is the whole of what checkpoints_under asks for."""

    def __init__(self, contents: list[dict[str, object]]) -> None:
        self.contents = contents
        self.prefixes: list[str] = []

    def list_objects_v2(self, **arguments: object) -> dict[str, object]:
        self.prefixes.append(str(arguments["Prefix"]))
        return {"Contents": self.contents, "IsTruncated": False}


def test_a_failed_run_that_wrote_a_checkpoint_says_written(catalogs: Catalogs) -> None:
    lister = FakeLister(
        [
            {
                "Key": (
                    "teams/scratch/runs/run_019fd3cc-79a0-70f5-aa29-6db4a2061a61/"
                    "checkpoints/step100/model.pt"
                ),
                "Size": 2048,
                "LastModified": "2026-08-05T21:29:00+00:00",
            }
        ]
    )
    facts = read_run_ended(envelope("batch-failed"), catalogs=catalogs, checkpoint_lister=lister)

    assert facts is not None
    assert facts.checkpoint_state == "written"
    assert lister.prefixes == [
        "teams/scratch/runs/run_019fd3cc-79a0-70f5-aa29-6db4a2061a61/checkpoints/"
    ]


def test_a_failed_run_that_wrote_nothing_says_none(catalogs: Catalogs) -> None:
    facts = read_run_ended(
        envelope("batch-failed"), catalogs=catalogs, checkpoint_lister=FakeLister([])
    )

    assert facts is not None
    assert facts.checkpoint_state == "none"


def test_a_refused_listing_says_unknown_rather_than_none(catalogs: Catalogs) -> None:
    """THE DIRECTION TO BE WRONG IN. Mutation: treat a refusal as an empty prefix.

    `no checkpoint written` is a claim about the run. `unknown` is a claim about the reader.
    Reporting the first when the second is true tells somebody their work is gone when it may
    be sitting in S3, which is the one wrong answer this message must not give.
    """

    class RefusingLister:
        def list_objects_v2(self, **arguments: object) -> dict[str, object]:
            raise RuntimeError("AccessDenied")

    facts = read_run_ended(
        envelope("batch-failed"), catalogs=catalogs, checkpoint_lister=RefusingLister()
    )

    assert facts is not None
    assert facts.checkpoint_state == "unknown"


def test_no_lister_means_unknown_and_costs_nothing(catalogs: Catalogs) -> None:
    facts = read_run_ended(envelope("batch-failed"), catalogs=catalogs)

    assert facts is not None
    assert facts.checkpoint_state == "unknown"
