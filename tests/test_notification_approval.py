"""The five lines a lead decides on, and the page somebody reads at eight in the morning.

Asserted by equality, for the reason ``tests/test_notification_messages.py`` gives: wording is
the thing the owner will change, and a test checking that a figure is present goes green on a
sentence nobody meant to write.

**Every figure in the expected text below is derived from the committed configuration rather
than typed here twice.** The money is recomputed against ``config/workload-catalog.yaml``, the
bound against ``config/policy.yaml`` and the median against ``config/run-history.json``, so a
catalog edit that moves a rate fails this file with the two numbers side by side instead of
silently agreeing with a copy of the old one.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from edullm_platform.contracts.workload import compute_maximum_compute_cost_usd
from edullm_platform.notifications.approval import (
    APPROVAL_DETAIL_TYPE,
    MANIFEST_FIELDS,
    PLATFORM_EVENT_SOURCE,
    RUNG_SAID,
    SHAPE_FIELDS,
    load_policy,
    read_approval_requested,
)
from edullm_platform.notifications.facts import Catalogs
from edullm_platform.notifications.messages import (
    RUNS_CHANNEL,
    Message,
    render_approval_requested,
    render_morning_page,
)
from edullm_platform.notifications.overnight import (
    OVERNIGHT_DETAIL_TYPE,
    Ended,
    OvernightFacts,
    read_overnight,
    window_of,
)
from edullm_platform.run_history import RUNGS, load_run_history

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CONFIG = PROJECT_ROOT / "config"
EVENTS = PROJECT_ROOT / "fixtures" / "events"


@pytest.fixture(scope="module")
def catalogs() -> Catalogs:
    return Catalogs.load(CONFIG)


def asked(name: str, catalogs: Catalogs) -> Message:
    envelope = json.loads((EVENTS / f"{name}.sanitized.json").read_text(encoding="utf-8"))
    facts = read_approval_requested(
        envelope,
        catalogs=catalogs,
        policy=load_policy(CONFIG),
        history=load_run_history(CONFIG),
    )
    assert facts is not None
    return render_approval_requested(facts)


def test_the_cost_and_the_experiment_are_in_the_first_line(catalogs: Catalogs) -> None:
    """The one rule this message has that no other message here has.

    Every other message opens with the person, because a message about a run that happened is
    accountable to somebody. This one opens with the number, because the reader is deciding
    whether to spend it and has thirty seconds. A figure in the second line is a figure behind
    a scroll on a phone.
    """
    first = asked("approval-requested", catalogs).text.splitlines()[0]

    assert first.startswith("$781.82 ")
    assert "context-length-sweep" in first


def test_one_run_over_the_bound_reads_as_five_lines(catalogs: Catalogs) -> None:
    message = asked("approval-requested", catalogs)

    assert message.channel == RUNS_CHANNEL
    assert message.text == (
        "$781.82 · context-length-sweep · Nathan Zhao · OLMo-core on gpu-8xa10g\n"
        "$16.288/hour x 1 node x 24h x 2 attempts x 1 cell. A ceiling rather than an "
        "estimate.\n"
        "The same workload on this machine has taken 1h00m over 3 runs, and the 24h bound "
        "is 24 times that.\n"
        "routine, run-approval-lead, because $781.82 is not under the $500.00 nobody "
        "releases. Team pre-training routes to alsy7009. Any team lead may release it.\n"
        "Release or decline at https://github.com/edu-llm/platform/actions/runs/00000000001."
        " A decline takes a reason, and that reason is what "
        "`edullm status run_019fd4f0-cf95` prints back to whoever submitted this."
    )


def test_a_fan_out_shows_the_multiplied_total_and_not_one_cell(catalogs: Catalogs) -> None:
    """THE BUG THIS FILE EXISTS TO KEEP OUT, IN THE PLACE IT WOULD COST THE MOST.

    Sixty-four cells of a one-dollar machine is a sixty-four dollar commitment, and a message
    naming one cell would ask a lead to release $1.01 and start sixty-four machines. The
    headline figure is held against the shared function's own product rather than against a
    number typed here, so a renderer that started multiplying its own factors and dropped one
    fails this even if the constant happened to match on today's catalog.
    """
    message = asked("approval-requested-fanout", catalogs)
    profile = next(
        entry for entry in catalogs.catalog.compute_profiles if entry.name == "gpu-1xa10g"
    )
    total = compute_maximum_compute_cost_usd(
        profile.hourly_rate_usd, profile.nodes, Decimal(1), 1, 64
    )

    assert message.text.startswith(f"${total:,.2f} ")
    assert "x 64 cells" in message.text
    assert f"${profile.hourly_rate_usd}/hour" in message.text


def test_a_fan_out_says_that_a_fan_out_is_never_released_automatically(
    catalogs: Catalogs,
) -> None:
    """The routing explains itself, which is the difference between this and a gate label.

    $64.38 is well under the bound nobody releases, so a lead reading "approval required"
    would have no way to tell whether the rule being applied was the right rule. The sentence
    names the test that actually held.
    """
    routing = asked("approval-requested-fanout", catalogs).text.splitlines()[3]

    assert routing.startswith(
        "routine, run-approval-lead, because a fan-out is never released automatically, "
        "whatever it costs."
    )


def test_a_capacity_block_names_the_admins_and_says_no_lead_is_being_asked(
    catalogs: Catalogs,
) -> None:
    """Every message goes to one channel, so a lead reads all of them.

    Mutation: print the submitting team's leads under the admin gate. That would tell eight
    people a run was theirs to release when the gate will not open for any of them, which is
    worse than naming nobody.
    """
    routing = asked("approval-requested-capacity-block", catalogs).text.splitlines()[3]

    assert "exception, run-approval-admin" in routing
    assert "No lead is being asked for this." in routing
    assert "Any platform admin releases it, which is " in routing
    assert "routes to" not in routing


def test_the_ceiling_is_checked_against_what_the_shape_has_taken(catalogs: Catalogs) -> None:
    """An expensive run that is correct and one that is a typo render identically without this.

    The five factors say which of them is large. They cannot say whether the large one is
    right, because ``24h`` looks the same on a shape that runs a day and on one that has never
    taken more than an hour.
    """
    measured = asked("approval-requested", catalogs).text.splitlines()[2]

    assert measured == (
        "The same workload on this machine has taken 1h00m over 3 runs, and the 24h bound "
        "is 24 times that."
    )


def test_a_bound_under_what_the_shape_takes_is_said_as_a_warning(catalogs: Catalogs) -> None:
    """The other direction, which is a different thing to tell somebody.

    A one-hour bound on a workload whose median is nearly two is a sweep that will be cut off
    at the bound, and every cell of it wasted. Reporting that as "the bound is 0.5 times the
    median" would be arithmetic rather than a sentence anybody acts on.
    """
    measured = asked("approval-requested-fanout", catalogs).text.splitlines()[2]

    assert measured.endswith(
        "which is longer than the 1h bound, so this one is likely to be cut off at the bound."
    )


def test_a_looser_cohort_says_it_is_looser(catalogs: Catalogs) -> None:
    """Mutation: print every rung with the same words.

    ``gpu-8xh100`` has never run here, so the median beside a twelve-hour H100 bound is taken
    from the same workload on other machines, which are not the same speed. A lead has to be
    able to see that before trusting or discounting the figure.
    """
    measured = asked("approval-requested-capacity-block", catalogs).text.splitlines()[2]

    assert measured.startswith("The same workload on any machine has taken ")


def test_the_rung_words_cover_every_rung_run_history_has() -> None:
    """Mutation: add a fourth rung to ``run_history`` and leave this list at three.

    An index past the end would fall back to the unnamed wording, which reads as the most
    specific cohort. That is the wrong direction to be wrong in: it would tell a lead the
    figure is about this exact shape when it is about a wider one.
    """
    assert len(RUNG_SAID) == len(RUNGS)


def test_the_shape_key_is_the_key_run_history_is_built_on() -> None:
    """Mutation: rename a manifest field on one side of the lookup.

    The widest rung is a prefix of the narrowest, and this builds the narrowest. A field
    spelled one way here and another way there produces no cohort and therefore the sentence
    saying nothing of this shape has run, which is a wrong answer that reads like a real one.
    """
    assert set(RUNGS[0][0]) == set(SHAPE_FIELDS)


def test_every_manifest_field_this_reads_is_one_a_manifest_carries() -> None:
    """The document is read as JSON, so nothing else stops a rename here from going quiet.

    ``read_approval_requested`` deliberately does not parse the manifest through
    ``RunManifest``, because that would put the whole submission surface into the notifier's
    zip. The cost of that choice is that a renamed field fails at run time as a missing value
    rather than at load time as a refusal, and this is what pays it back.
    """
    from edullm_platform.contracts.manifest import RunManifest

    assert set(MANIFEST_FIELDS) <= set(RunManifest.model_fields)


def test_hours_above_the_workload_profile_bound_are_named(catalogs: Catalogs) -> None:
    """Mutation: trust the arithmetic to speak for itself.

    A submission naming ten thousand hours on a workload declaring twenty-four compiles clean
    and prices clean, and the product is correct. The crossing is between two numbers only one
    of which is otherwise on the page.
    """
    envelope = json.loads((EVENTS / "approval-requested.sanitized.json").read_text("utf-8"))
    envelope["detail"]["manifest"]["maximum_runtime_hours"] = "10000"

    facts = read_approval_requested(
        envelope, catalogs=catalogs, policy=load_policy(CONFIG), history=load_run_history(CONFIG)
    )
    assert facts is not None
    text = render_approval_requested(facts).text

    assert "It asks for 10000h where olmo-core-train declares 24h." in text


def test_a_submission_inside_the_profile_bound_says_nothing_about_it(
    catalogs: Catalogs,
) -> None:
    """The other half of the same rule. A clause on every message is a clause nobody reads."""
    assert "declares" not in asked("approval-requested", catalogs).text


def test_the_policy_bound_is_read_from_the_file_rather_than_written_here(
    catalogs: Catalogs,
) -> None:
    """Mutation: write $500 into the sentence.

    ``config/policy.yaml`` has been bumped five times and the one bound it has left moved by a
    factor of a hundred once already. A message quoting a threshold from memory is a message
    that is wrong for a month after somebody edits the file and correct-looking throughout.
    """
    policy = load_policy(CONFIG)
    figure = f"${policy.thresholds.automatic_below_cost_usd:,.2f}"

    assert f"not under the {figure} nobody releases" in asked("approval-requested", catalogs).text


def test_an_envelope_from_batch_is_not_an_approval(catalogs: Catalogs) -> None:
    """One queue carries three shapes, so every reader has to decline the other two."""
    envelope = json.loads((EVENTS / "batch-succeeded.sanitized.json").read_text("utf-8"))

    assert (
        read_approval_requested(
            envelope, catalogs=catalogs, policy=load_policy(CONFIG), history=None
        )
        is None
    )


def test_no_run_history_is_said_rather_than_read_as_nothing_having_run(
    catalogs: Catalogs,
) -> None:
    """Two different facts, and only one of them is about the platform.

    An install carrying no reading and a platform on which nothing of this shape has run send
    somebody to different places. Substituting one for the other tells a lead they are the
    first to try a shape that has run forty times.
    """
    envelope = json.loads((EVENTS / "approval-requested.sanitized.json").read_text("utf-8"))
    facts = read_approval_requested(
        envelope, catalogs=catalogs, policy=load_policy(CONFIG), history=None
    )
    assert facts is not None

    assert render_approval_requested(facts).text.splitlines()[2] == (
        "No run history is packaged with this notifier, so nothing here checks the ceiling "
        "against what the shape has taken."
    )


def test_a_machine_the_catalog_does_not_price_costs_the_figure_and_not_the_message(
    catalogs: Catalogs,
) -> None:
    """A message nobody got is never better than one with a gap in it that says it is a gap."""
    envelope = json.loads((EVENTS / "approval-requested.sanitized.json").read_text("utf-8"))
    envelope["detail"]["manifest"]["compute_profile"] = "gpu-96xsomething"

    facts = read_approval_requested(
        envelope, catalogs=catalogs, policy=load_policy(CONFIG), history=load_run_history(CONFIG)
    )
    assert facts is not None
    text = render_approval_requested(facts).text

    assert text.startswith("cost unknown · context-length-sweep")
    assert "No execution target prices gpu-96xsomething" in text


def test_no_approval_message_carries_an_em_dash(catalogs: Catalogs) -> None:
    """The house standard, held over the one surface outside this tree that a lead reads."""
    for name in (
        "approval-requested",
        "approval-requested-fanout",
        "approval-requested-capacity-block",
    ):
        assert "—" not in asked(name, catalogs).text


# ---------------------------------------------------------------------------------------
# The morning page
# ---------------------------------------------------------------------------------------


class Listing:
    """A Batch client that answers one page per queue, and nothing for the rest."""

    def __init__(self, answers: dict[str, list[dict[str, Any]]]) -> None:
        self.answers = answers
        self.asked: list[dict[str, Any]] = []

    def list_jobs(self, **arguments: Any) -> Any:
        self.asked.append(arguments)
        return {"jobSummaryList": self.answers.get(arguments["jobQueue"], [])}


def summary(
    name: str, status: str, *, seconds: int, exit_code: int | None = 0, started: int = 1_000_000
) -> dict[str, Any]:
    return {
        "jobName": name,
        "status": status,
        "startedAt": started,
        "stoppedAt": started + seconds * 1000,
        "container": {} if exit_code is None else {"exitCode": exit_code},
    }


RUN = "run_019fd4f0-cf95-70b1-9f65-461245dbd08e"
OTHER = "run_019fd382-2f9c-7024-ac0e-e8e18e6829cf"
NOW = 1_785_984_000_000


def overnight(catalogs: Catalogs, lister: Listing | None) -> Message:
    envelope = json.loads((EVENTS / "overnight-activity.sanitized.json").read_text("utf-8"))
    facts = read_overnight(envelope, catalogs=catalogs, cell_lister=lister, now_ms=NOW)
    assert facts is not None
    return render_morning_page(facts)


def test_the_morning_page_carries_a_budget_line_that_says_it_is_measured(
    catalogs: Catalogs,
) -> None:
    """MEASURED AND NOT AUTHORISED, SAID RATHER THAN LEFT TO BE INFERRED.

    Every other money figure a lead has seen this week is a ceiling. A bare total here would
    be read as one, and the account would look several times more expensive than it is.
    """
    lister = Listing(
        {
            "sbsandbox-intern-edullm-cpu": [
                summary("edullm-validate-on-manifest", "SUCCEEDED", seconds=3600),
            ]
        }
    )

    text = overnight(catalogs, lister).text

    assert text.splitlines()[1] == (
        "$1.43 spent, measured from what the machines ran rather than from what anybody "
        "authorised."
    )


def test_a_queue_that_could_not_be_listed_makes_the_total_a_floor(catalogs: Catalogs) -> None:
    """Mutation: skip a refused listing and print the smaller number.

    A queue nobody could read is not a queue nothing ran on, and the two produce identical
    pages unless the shortfall is said. A total that is quietly short reads exactly like a
    complete one, which is the worst shape a money figure can have.
    """

    class Refusing(Listing):
        def list_jobs(self, **arguments: Any) -> Any:
            if arguments["jobQueue"] == "sbsandbox-intern-edullm-gpu":
                raise RuntimeError("AccessDenied")
            return super().list_jobs(**arguments)

    lister = Refusing(
        {"sbsandbox-intern-edullm-cpu": [summary(RUN, "SUCCEEDED", seconds=3600)]}
    )

    text = overnight(catalogs, lister).text

    assert "A floor rather than a total, because 1 of the 14 queues could not be listed." in text


def test_a_failure_is_named_with_its_exit_code_and_where_it_ran(catalogs: Catalogs) -> None:
    lister = Listing(
        {
            "sbsandbox-intern-edullm-cpu": [
                summary(RUN, "FAILED", seconds=120, exit_code=1),
                summary(OTHER, "SUCCEEDED", seconds=60),
            ]
        }
    )

    text = overnight(catalogs, lister).text

    assert text.splitlines()[0] == "Overnight, 12h: 2 jobs ended and 1 of them worked."
    assert "1 failed. run_019fd4f0-cf95 (exit 1, 2m on cpu-32vcpu)." in text


def test_a_host_that_went_is_not_reported_as_exit_zero(catalogs: Catalogs) -> None:
    """Absent is a fact. A reclaimed host leaves no exit code because there was no exit."""
    lister = Listing(
        {"sbsandbox-intern-edullm-cpu": [summary(RUN, "FAILED", seconds=5, exit_code=None)]}
    )

    assert "(no exit code, 5s on cpu-32vcpu)" in overnight(catalogs, lister).text


def test_a_quiet_night_says_how_many_queues_answered(catalogs: Catalogs) -> None:
    """Mutation: print "nothing happened" without saying whether anybody looked.

    Nothing ran and nothing could be read produce the same empty page, and only one of them
    is good news.
    """
    assert overnight(catalogs, None).text == (
        "Overnight, 12h: nothing ran and nothing is queued. 0 of 14 queues answered."
    )


def test_the_window_is_asked_of_batch_rather_than_filtered_here(catalogs: Catalogs) -> None:
    """``ListJobs`` answers oldest first, so a client-side filter pages through everything.

    Measured 2026-08-06 against the live account: the cpu queue alone holds 113 succeeded jobs
    and 55 failed ones going back weeks. The filter is what makes this one call per queue.
    """
    lister = Listing({})
    overnight(catalogs, lister)

    assert lister.asked, "no queue was asked"
    for call in lister.asked:
        assert call["filters"] == [
            {"name": "AFTER_CREATED_AT", "values": [str(NOW - 12 * 3600 * 1000)]}
        ]
        assert "jobStatus" not in call


def test_a_job_name_that_is_not_a_run_id_is_counted_and_never_attributed() -> None:
    """Hand-run smokes cost real money and belong to nobody."""
    assert Ended(
        name="validator-preflight-091",
        queue="sbsandbox-intern-edullm-cpu",
        compute_profile="cpu-32vcpu",
        succeeded=True,
        seconds=9,
        spent_usd=Decimal("0.00"),
        exit_code=0,
    ).is_a_run is False


@pytest.mark.parametrize(
    ("detail", "expected"),
    [({"hours": 6}, 6), ({}, 12), ({"hours": 0}, 12), ({"hours": "twelve"}, 12)],
)
def test_a_mistyped_window_costs_the_window_and_never_the_page(
    detail: dict[str, Any], expected: int
) -> None:
    """A constant input on a schedule is a string in a template nothing type-checks."""
    assert window_of({"detail": detail}) == expected


def test_the_overnight_trigger_is_the_platform_saying_something_to_itself() -> None:
    """Nothing in AWS produces this event, so it carries a source of its own."""
    assert PLATFORM_EVENT_SOURCE != "aws.batch"
    assert APPROVAL_DETAIL_TYPE != OVERNIGHT_DETAIL_TYPE


def test_nothing_priced_is_reported_as_a_shortfall_rather_than_as_zero() -> None:
    """Two of the account's sixteen queues are named by no execution target."""
    facts = OvernightFacts(
        hours=12,
        ended=(
            Ended(
                name=RUN,
                queue="sbsandbox-intern-edullm-gpu-1xh100",
                compute_profile=None,
                succeeded=True,
                seconds=3600,
                spent_usd=None,
                exit_code=0,
            ),
        ),
        running=0,
        waiting=0,
        queues_read=14,
        queues_asked=14,
    )

    assert facts.spent_usd == Decimal("0.00")
    assert "1 of the jobs ran on a queue nothing prices" in render_morning_page(facts).text
