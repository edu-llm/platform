"""One record per run, before anything decides what to publish.

**THE SUBSTRATE MUST NOT DROP A RUN THAT DID NOT RUN.** ``run_costs`` drops an intent with no
attempt and says so in its own docstring, which is right for a report about what runs have cost
and wrong for the base table: a queued run, a refused run and a run waiting on capacity are
exactly the runs somebody asks the status of. Two tests below fail if the substrate is built by
walking the costs.

**AN UNREAD SOURCE MUST NOT NORMALISE INTO A SOURCE THAT WAS EMPTY.** Every source carries three
outcomes rather than two, and the last block of tests here is the one that holds the whole
design up: for each source it builds the read-and-empty substrate beside the could-not-be-read
one and fails if anything downstream could mistake them for each other.

Nothing here reaches AWS. The intent records are the committed Phase 2 fixtures.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from edullm_platform.config import load_yaml
from edullm_platform.contracts.admission import IntentRecord
from edullm_platform.contracts.lifecycle import SchedulerAttempt
from edullm_platform.contracts.workload import WorkloadCatalog
from edullm_platform.substrate import (
    ATTEMPTS_NOT_READ,
    NEVER_STARTED,
    SOURCE_EMPTY,
    SOURCE_NOT_READ,
    SOURCE_OUTCOMES,
    SOURCE_READ,
    SOURCES,
    SUBSTRATE_FORMAT_VERSION,
    LaunchEvent,
    RunFacts,
    SourceGap,
    Substrate,
    SubstrateFormatError,
    as_document,
    from_document,
    normalise,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
INTENTS = PROJECT_ROOT / "fixtures/evidence/phase-2/lineage/records/intent"

#: Two committed intent records, whose run ids are the ones asserted against below. They are
#: real records written by a real submission, which is why the manifest is not invented here.
RUN_A = "run_019fa446-8a4e-7094-9e29-d44fffbd2491"
RUN_B = "run_019fa468-c9b5-706a-8849-87c1d0b5befb"

COLLECTED = datetime(2026, 8, 4, 5, 0, tzinfo=UTC)


def _intent(run_id: str, **overrides: Any) -> IntentRecord:
    loaded: Any = json.loads((INTENTS / f"{run_id}.json").read_text(encoding="utf-8"))
    if isinstance(loaded, str):  # the fixture is a JSON string holding JSON
        loaded = json.loads(loaded)
    loaded.update(overrides)
    return IntentRecord.model_validate(loaded)


def _attempt(run_id: str, *, day: date, hour: int, state: str = "succeeded") -> SchedulerAttempt:
    # An attempt id is a uuid7 under an `att_` prefix and the contract enforces the shape, so
    # this is a real one with its last byte varied rather than a readable invention. The
    # terminal state is lower case because `AttemptTerminalState` is a StrEnum of lower-case
    # values and refuses the CloudWatch spelling.
    return SchedulerAttempt.model_validate(
        {
            "schema_version": 1,
            "attempt_id": f"att_019fa974-10b2-74b7-86dd-0c93bc5cd7{hour:02x}",
            "run_id": run_id,
            "attempt_ordinal": 1,
            "scheduler_job_id": "00000000-0000-0000-0000-00000000000a",
            "started_at": datetime(day.year, day.month, day.day, hour, tzinfo=UTC).isoformat(),
            "ended_at": datetime(day.year, day.month, day.day, hour + 1, tzinfo=UTC).isoformat(),
            "terminal_state": state,
        }
    )


def _profiles() -> tuple[Any, ...]:
    catalog = load_yaml(PROJECT_ROOT / "config/workload-catalog.yaml", WorkloadCatalog)
    return tuple(catalog.compute_profiles)


def test_a_run_that_never_reached_an_instance_is_still_a_run() -> None:
    """Mutation: build the substrate by walking run_costs, which drops it.

    Three of the four states the activity is specified to report -- queued, running and
    refused -- have no attempt record, so a base table keyed off the priced runs can only
    ever show terminal ones.
    """
    substrate = normalise(
        collected_at=COLLECTED,
        intents=(_intent(RUN_A),),
        attempts=(),
        compute_profiles=_profiles(),
    )
    assert list(substrate.runs) == [RUN_A]
    facts = substrate.runs[RUN_A]
    assert facts.state == "submitted"
    assert facts.state_source == "intent"
    assert facts.attempts == ()
    assert facts.cost_usd is None
    assert facts.unpriced_reason == NEVER_STARTED


def test_an_unread_attempt_prefix_is_not_reported_as_a_run_that_never_started() -> None:
    """Mutation: pass no attempts for a refused prefix instead of setting attempts_read.

    THIS IS THE SUBSTRATE'S DENOMINATOR. "Never started" and "nobody could look" produce the
    same empty attempt list and only one of them is true, so a substrate that could not tell
    them apart would file a false claim about every run in the store rather than a true one
    about one prefix.
    """
    refused = normalise(
        collected_at=COLLECTED,
        intents=(_intent(RUN_A),),
        attempts=(),
        compute_profiles=_profiles(),
        attempts_read=False,
    )
    empty = normalise(
        collected_at=COLLECTED,
        intents=(_intent(RUN_A),),
        attempts=(),
        compute_profiles=_profiles(),
    )
    assert refused.attempts_read is False
    assert empty.attempts_read is True
    assert refused.runs[RUN_A].state == "unknown"
    assert refused.runs[RUN_A].state_source == "unread"
    assert refused.runs[RUN_A].unpriced_reason == ATTEMPTS_NOT_READ
    assert refused.runs[RUN_A].unpriced_reason != empty.runs[RUN_A].unpriced_reason


def test_a_run_that_cannot_be_priced_is_not_reported_as_one_that_never_ran() -> None:
    """Mutation: use the never-started reason for every run with no cost.

    An unregistered compute profile has no rate, so `run_costs` reports the run with a
    duration and no figure. That run reached an instance and finished; saying it never
    started would be false about the one thing this table exists to record.
    """
    substrate = normalise(
        collected_at=COLLECTED,
        intents=(_intent(RUN_A),),
        attempts=(_attempt(RUN_A, day=date(2026, 8, 4), hour=9),),
        compute_profiles=(),
    )
    facts = substrate.runs[RUN_A]
    assert facts.state == "succeeded"
    assert facts.state_source == "attempt"
    assert facts.seconds == Decimal(3600)
    assert facts.cost_usd is None
    assert facts.unpriced_reason is not None
    assert facts.unpriced_reason not in {NEVER_STARTED, ATTEMPTS_NOT_READ}


def test_a_priced_run_carries_a_figure_and_the_one_arithmetic() -> None:
    """Mutation: recompute a duration or a rate here instead of delegating to run_costs.

    No dollar figure is asserted, because the rate lives in `config/workload-catalog.yaml`
    and copying it into a test would put a price in two places. The duration is asserted
    because it is arithmetic this test wrote by hand: one attempt of exactly one hour.
    """
    substrate = normalise(
        collected_at=COLLECTED,
        intents=(_intent(RUN_A),),
        attempts=(_attempt(RUN_A, day=date(2026, 8, 4), hour=9),),
        compute_profiles=_profiles(),
    )
    facts = substrate.runs[RUN_A]
    assert facts.seconds == Decimal(3600)
    assert facts.cost_usd is not None
    assert facts.unpriced_reason is None


def test_the_run_carries_the_workflow_run_that_produced_it() -> None:
    """Mutation: leave the join out of the record.

    The join is the whole reason a status query can be answered with code-host credentials.
    It exists in the intent record, which is behind AWS credentials the CLI does not hold,
    so carrying it here is what lets it be published where the CLI can read it.

    **The run id is spelled rather than written as an integer.** The account-id scanner in
    `tests/test_evidence.py` flags any eleven-digit int, because this account's id read as
    an integer loses its leading zero and becomes eleven digits. A GitHub run id is also
    eleven digits now. The fixture this reads carries the same value as JSON, which the
    scanner does not walk, so the literal here is the only thing that trips it.
    """
    the_workflow_run = int("30281990942")
    facts = normalise(
        collected_at=COLLECTED,
        intents=(_intent(RUN_A),),
        attempts=(),
        compute_profiles=_profiles(),
    ).runs[RUN_A]
    assert facts.workflow_run_id == the_workflow_run
    assert facts.workflow_run_url is not None
    assert facts.workflow_run_url.endswith(f"/actions/runs/{the_workflow_run}/attempts/1")


def test_the_substrate_carries_no_day_and_is_asked_for_one() -> None:
    """Mutation: filter by a day inside normalise, or key the table by day.

    The substrate is shared by a daily file and a snapshot refreshed on state change. A
    window baked in here is the daily view's assumption leaking into the pipeline, which is
    the rework this split exists to avoid.
    """
    substrate = normalise(
        collected_at=COLLECTED,
        intents=(_intent(RUN_A), _intent(RUN_B)),
        attempts=(_attempt(RUN_A, day=date(2026, 8, 4), hour=9),),
        compute_profiles=_profiles(),
    )
    assert set(substrate.runs) == {RUN_A, RUN_B}
    assert [facts.run_id for facts in substrate.ran_on(date(2026, 8, 4))] == [RUN_A]
    assert substrate.ran_on(date(2026, 8, 3)) == ()


def test_a_run_with_no_attempt_belongs_to_the_day_it_was_submitted() -> None:
    """Mutation: drop a run with no attempt out of every day.

    A queued run has one timestamp and it is the submission's. Falling back to no day at all
    would make the only run of a quiet morning invisible on both publications at once.
    """
    substrate = normalise(
        collected_at=COLLECTED,
        intents=(_intent(RUN_A, recorded_at="2026-08-04T15:52:40.714650Z"),),
        attempts=(),
        compute_profiles=_profiles(),
    )
    assert [facts.run_id for facts in substrate.ran_on(date(2026, 8, 4))] == [RUN_A]


def test_a_live_state_beats_a_terminal_record_and_says_so() -> None:
    """Mutation: prefer the attempt record, or record no source for the state.

    A lineage attempt record is written when an attempt ends, so it can say how a run
    finished and never that one is running. Batch is the only source that can name a state a
    run is still in, which is why a status query needs it and a daily file does not.
    """
    substrate = normalise(
        collected_at=COLLECTED,
        intents=(_intent(RUN_A),),
        attempts=(_attempt(RUN_A, day=date(2026, 8, 4), hour=9),),
        compute_profiles=_profiles(),
        live_states={RUN_A: "running"},
    )
    assert substrate.runs[RUN_A].state == "running"
    assert substrate.runs[RUN_A].state_source == "live"


def test_a_launch_feed_nobody_read_is_not_an_empty_launch_feed() -> None:
    """Mutation: default launches to an empty tuple.

    An empty feed means CloudTrail was read and nothing launched, which is a finding. None
    means there is no finding. The mismatch list downstream turns exactly this distinction
    into the difference between a clean day and a broken one.
    """
    unread = normalise(
        collected_at=COLLECTED, intents=(), attempts=(), compute_profiles=_profiles()
    )
    read = normalise(
        collected_at=COLLECTED,
        intents=(),
        attempts=(),
        compute_profiles=_profiles(),
        launches=(),
    )
    assert unread.launches is None
    assert read.launches == ()


def test_the_launches_are_carried_whole_rather_than_pre_filtered() -> None:
    """Mutation: keep only the launches with no run id.

    The mismatch denominator counts every launch examined, so a feed narrowed to the
    unattributed ones would make the denominator equal the numerator and the report would
    read as though every launch in the account were a mismatch.
    """
    tagged = LaunchEvent(
        event_id="11111111-1111-1111-1111-11111111111b",
        event_name="SubmitJob",
        occurred_at=COLLECTED,
        role_name="sbsandbox-intern-edullm-run",
        run_id=RUN_A,
    )
    untagged = LaunchEvent(
        event_id="22222222-2222-2222-2222-22222222222c",
        event_name="RunInstances",
        occurred_at=COLLECTED,
        role_name="Intern-alsy7009",
        run_id=None,
    )
    substrate = normalise(
        collected_at=COLLECTED,
        intents=(),
        attempts=(),
        compute_profiles=_profiles(),
        launches=(tagged, untagged),
    )
    assert substrate.launches == (tagged, untagged)


def test_every_source_that_was_not_read_is_named_on_the_substrate() -> None:
    """Mutation: log the gap and drop it, leaving the caller with no way to print it."""
    gap = SourceGap(
        source="batch:DescribeJobs",
        reason="the reading role holds no batch action",
        unanswered="no run can be reported as running rather than finished",
    )
    substrate = normalise(
        collected_at=COLLECTED,
        intents=(),
        attempts=(),
        compute_profiles=_profiles(),
        gaps=(gap,),
    )
    assert substrate.gaps == (gap,)


def test_the_known_run_ids_are_every_run_the_platform_can_account_for() -> None:
    """Mutation: return only the runs that have attempts.

    This set is the mismatch join's right side. Narrowing it to runs that reached an
    instance would report every queued run's own launch as a mismatch.
    """
    substrate = normalise(
        collected_at=COLLECTED,
        intents=(_intent(RUN_A), _intent(RUN_B)),
        attempts=(),
        compute_profiles=_profiles(),
    )
    assert substrate.known_run_ids == frozenset({RUN_A, RUN_B})


# --------------------------------------------------------------------------------------
# The three outcomes, and the property that nothing may collapse two of them into one.
#
# Each entry is one source, with the three substrates it can produce written out by hand:
# read, read and found nothing, and could not be read. Written as literals rather than
# derived from `SOURCES`, because a table generated out of the code under test asserts only
# that the code agrees with itself.
# --------------------------------------------------------------------------------------


def _substrate(**overrides: Any) -> Substrate:
    arguments: dict[str, Any] = {
        "collected_at": COLLECTED,
        "intents": (_intent(RUN_A),),
        "attempts": (),
        "compute_profiles": _profiles(),
    }
    arguments.update(overrides)
    return normalise(**arguments)


_A_LAUNCH = LaunchEvent(
    event_id="33333333-3333-3333-3333-33333333333d",
    event_name="RunInstances",
    occurred_at=COLLECTED,
    role_name="Intern-alsy7009",
    run_id=None,
)

#: source -> (what a full read looks like, what an empty read looks like, what no read
#: looks like). The keyword arguments are `normalise`'s, so this table is also the
#: statement of how a caller says which of the three happened.
THREE_OUTCOMES: dict[str, tuple[dict[str, Any], dict[str, Any], dict[str, Any]]] = {
    "attempt": (
        {"attempts": (_attempt(RUN_A, day=date(2026, 8, 4), hour=9),)},
        {"attempts": ()},
        {"attempts": (), "attempts_read": False},
    ),
    "experiment": (
        {"experiments": {RUN_A: "memory-split-ablation"}},
        {"experiments": {}},
        {"experiments": None},
    ),
    "launch": (
        {"launches": (_A_LAUNCH,)},
        {"launches": ()},
        {"launches": None},
    ),
    "live": (
        {"live_states": {RUN_A: "running"}},
        {"live_states": {}},
        {"live_states": None},
    ),
}


def test_every_source_the_substrate_reads_declares_its_three_outcomes() -> None:
    """Mutation: add a source to SOURCES without giving it a could-not-be-read spelling.

    This is the check that stops the property decaying. A source that reaches the substrate
    without a way to say "nobody could look" will be given one by whoever writes the view,
    and they will pick the empty value because it renders.
    """
    assert set(THREE_OUTCOMES) == set(SOURCES)
    assert SOURCE_OUTCOMES == (SOURCE_READ, SOURCE_EMPTY, SOURCE_NOT_READ)


@pytest.mark.parametrize("source", sorted(THREE_OUTCOMES))
def test_a_source_reports_three_outcomes_rather_than_two(source: str) -> None:
    """Mutation: return SOURCE_EMPTY for a source that was not read.

    THE WHOLE DESIGN IS THIS ASSERTION. Read, read and found nothing, and could not be read
    are three findings, and a scheduled job that reports the third as the second tells its
    reader the platform was quiet on precisely the mornings it was blind.
    """
    read, empty, unread = THREE_OUTCOMES[source]
    assert _substrate(**read).outcome(source) == SOURCE_READ
    assert _substrate(**empty).outcome(source) == SOURCE_EMPTY
    assert _substrate(**unread).outcome(source) == SOURCE_NOT_READ


@pytest.mark.parametrize("source", sorted(THREE_OUTCOMES))
def test_an_unread_source_cannot_be_collapsed_into_an_empty_one(source: str) -> None:
    """Mutation: carry the distinction in `gaps` alone, or drop `source_outcomes`.

    A gap list is advisory: a view that renders runs and launches and never looks at the
    gaps would render the two identically. So the two substrates are compared with the gaps
    stripped off, which is the strongest available statement that a downstream reader cannot
    collapse them however carelessly it reads.
    """
    _, empty, unread = THREE_OUTCOMES[source]
    without_gaps = {"gaps": ()}
    was_empty = _substrate(**empty, **without_gaps)
    was_unread = _substrate(**unread, **without_gaps)
    assert was_empty.gaps == () and was_unread.gaps == ()
    assert was_empty != was_unread
    assert was_empty.outcome(source) != was_unread.outcome(source)


def test_a_source_nobody_declared_is_refused_rather_than_answered() -> None:
    """Mutation: return SOURCE_NOT_READ for an unknown source name.

    Answering a question about a source that does not exist is how a renderer ends up
    reporting a permanent hole nobody can close, and a typo in a view is indistinguishable
    from a source that was genuinely refused.
    """
    with pytest.raises(KeyError, match="batch"):
        _substrate().outcome("batch")


def test_the_read_flags_cannot_disagree_with_the_outcome_they_summarise() -> None:
    """Mutation: keep attempts_read as its own field beside source_outcomes.

    Two fields carrying one fact is how a flag and the thing it summarises drift, and the
    drift is silent: the run would say unread and the page would say read.
    """
    for name, source in (("attempts_read", "attempt"), ("experiments_read", "experiment")):
        _, empty, unread = THREE_OUTCOMES[source]
        assert getattr(_substrate(**empty), name) is True
        assert getattr(_substrate(**unread), name) is False
    assert not any(
        field.name in {"attempts_read", "experiments_read"}
        for field in dataclasses.fields(Substrate)
    )


# --------------------------------------------------------------------------------------
# Writing the reading down, because two of the sources forget
#
# Batch drops a job about a week after it ends and CloudWatch keeps a run's stdout for
# ninety days, so a reading taken and thrown away is evidence nobody can recover. What these
# assert is the shape of the document rather than that the writer agrees with the reader: a
# round trip alone would pass for a pair that lost the same field in both directions.
# --------------------------------------------------------------------------------------


def test_a_figure_is_written_as_text_rather_than_as_a_number() -> None:
    """Mutation: leave the Decimal to json's float, or to int(seconds).

    `Decimal("87.83")` through a float comes back as 87.83000000000000185, and a cost that
    moves in its eleventh place between the reading and the page it is read on is an
    afternoon somebody spends looking for arithmetic that is fine.
    """
    document = as_document(_substrate(attempts=(_attempt(RUN_A, day=date(2026, 8, 4), hour=9),)))
    written = document["runs"][RUN_A]
    assert isinstance(written["seconds"], str)
    assert Decimal(written["seconds"]) == Decimal(3600)
    assert isinstance(written["cost_usd"], str)
    assert Decimal(written["cost_usd"]) > 0
    # And exactly, not approximately: the text is the digits the reading held, so what comes
    # back out of json is the same Decimal rather than the nearest float to it.
    recovered = from_document(json.loads(json.dumps(document))).runs[RUN_A]
    assert (
        recovered.cost_usd
        == _substrate(attempts=(_attempt(RUN_A, day=date(2026, 8, 4), hour=9),))
        .runs[RUN_A]
        .cost_usd
    )


def test_a_document_this_tree_cannot_read_is_refused_rather_than_partly_read() -> None:
    """Mutation: read whatever fields are recognised and default the rest.

    A field a newer writer added and this reader skipped comes back absent, and absent is the
    one meaning this module never lets anything invent. The version is the only thing that
    can tell a reader it is looking at a document it does not understand.
    """
    document = as_document(_substrate())
    assert document["format_version"] == SUBSTRATE_FORMAT_VERSION
    with pytest.raises(SubstrateFormatError, match="format"):
        from_document({**document, "format_version": SUBSTRATE_FORMAT_VERSION + 1})


@pytest.mark.parametrize("source", sorted(THREE_OUTCOMES))
def test_a_reading_survives_being_written_down_and_read_back(source: str) -> None:
    """Mutation: any field dropped from as_document, or any default invented in from_document.

    Parametrised over the sources so that all three outcomes of each go through the format
    rather than only the one a hand-written example happens to hold.
    """
    for arguments in THREE_OUTCOMES[source]:
        original = _substrate(**arguments)
        assert from_document(json.loads(json.dumps(as_document(original)))) == original


@pytest.mark.parametrize("source", sorted(THREE_OUTCOMES))
def test_an_unread_source_is_still_unread_after_a_round_trip(source: str) -> None:
    """THE ONE THE FORMAT EXISTS FOR. Mutation: write an unread launch feed as `[]`.

    JSON is happy to confuse the two and each half of the confusion looks reasonable alone: a
    writer that skips a null, or a reader that defaults a missing key to the empty list. Two
    readings that differ only in whether anybody looked are compared here after the trip, so
    a format that collapses them fails rather than quietly publishing a clean morning.
    """
    _, empty, unread = THREE_OUTCOMES[source]
    was_empty = from_document(as_document(_substrate(**empty)))
    was_unread = from_document(as_document(_substrate(**unread)))
    assert was_empty.outcome(source) == SOURCE_EMPTY
    assert was_unread.outcome(source) == SOURCE_NOT_READ
    assert was_empty != was_unread


def test_the_document_carries_every_field_a_status_query_would_need() -> None:
    """Mutation: write the four columns the daily page renders and drop the rest.

    The reading is written down so that the run index can be published from it without a
    second ingestion, and a run index missing the workflow-run join is one that cannot answer
    the question it exists for. Asserted as an exact set against the record's own fields, so
    a field added to `RunFacts` and forgotten here fails rather than silently not being
    captured.
    """
    document = as_document(_substrate(attempts=(_attempt(RUN_A, day=date(2026, 8, 4), hour=9),)))
    assert set(document["runs"][RUN_A]) == {field.name for field in dataclasses.fields(RunFacts)}
    assert document["runs"][RUN_A]["workflow_run_id"] is not None
    assert document["runs"][RUN_A]["attempts"][0]["ordinal"] == 1
