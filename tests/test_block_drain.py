"""The clock the last morning of a capacity block turns on, and the count that checks a copy.

Two things in this module are worth a test and the rest is arrangement around them.

**THE CLOCK IS MEASURED AGAINST THE RECLAIM AND NOT AGAINST THE END OF THE WINDOW.** A block
sold until 11:30 UTC is a block whose instances start being terminated at 11:00, and every
mistake available here is off by that half hour in the direction that matters: a drain told it
has thirty minutes left when it has none, a warning fired after the machines have already begun
going away. It is subtracted once, in :func:`countdown`, and every test below that touches time
is really testing that it was subtracted at all.

**A SYNC THAT REPORTED SUCCESS AND COPIED MOST OF A DIRECTORY IS THE FAILURE THIS EXISTS FOR.**
``aws s3 sync`` exits zero after a file rotated out from under it, after a permissions refusal
on one path, and after a throttle it gave up retrying. Reading the exit status is therefore not
a check, and the node counts the files it was going to copy and then counts the objects that
landed. What must never happen is a node reading as safe because it answered.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from edullm_platform.block_drain import (
    DRAIN_FROM_MINUTES,
    RECLAIM_MARGIN_MINUTES,
    countdown,
    drain_markdown,
    drain_rows,
    horizon_for,
    outstanding,
    parse_drain_reading,
    read_checkpoints,
    remaining_as,
    unflushed_instances,
)

ENDS_AT = datetime(2026, 8, 11, 11, 30, tzinfo=UTC)


def answered(output: str, *, node: int = 1, instance_id: str = "i-0001") -> Any:
    """A complete answer from one node.

    The trailing ``drained_at`` is appended here rather than written into every case, because
    it is what the node prints last and its absence means something specific -- see
    :func:`cut_short`, which is the one test that leaves it off deliberately.
    """
    ending = "" if "drained_at" in output else "drained_at\t2026-08-11T10:30:00Z\n"
    return parse_drain_reading(
        node=node, instance_id=instance_id, status="Success", output=output + ending
    )


def cut_short(output: str, *, node: int = 1, instance_id: str = "i-0001") -> Any:
    """An answer Systems Manager truncated, which is an answer with no terminator on it."""
    return parse_drain_reading(
        node=node, instance_id=instance_id, status="Success", output=output
    )


def object_at(key: str, *, written: str = "2026-08-11T10:00:00+00:00") -> dict[str, str]:
    return {"Key": key, "LastModified": written}


def test_the_countdown_is_to_the_reclaim_and_not_to_the_end_of_the_window() -> None:
    """THE MUTATION THIS MODULE EXISTS FOR: subtract nothing, and count to ``EndDate``.

    That version reads as obviously correct -- the reservation says 11:30, so 11:30 is the
    deadline -- and it is wrong by the whole reclaim margin in the only direction that costs
    anything. At 11:00 it would report half an hour remaining, at exactly the moment AWS has
    started terminating the machines, so the final flush would be scheduled into a window that
    does not exist.
    """
    clock = countdown(ends_at=ENDS_AT, now=datetime(2026, 8, 11, 10, 0, tzinfo=UTC))

    assert clock.reclaim_at == ENDS_AT - timedelta(minutes=RECLAIM_MARGIN_MINUTES)
    assert clock.remaining == timedelta(minutes=60)
    assert not clock.past_reclaim


def test_a_drain_running_after_the_reclaim_began_says_so_rather_than_reporting_zero() -> None:
    """Mutation: clamp the remaining time at zero, which is what a duration formatter wants.

    The tick that fired at 10:58 is still copying at 11:01, and that is the single most
    important state this report can be in. Rendered as "0h00m remaining" it reads as being at
    the deadline; it is past it, and what somebody does about it is different.
    """
    clock = countdown(ends_at=ENDS_AT, now=datetime(2026, 8, 11, 11, 4, tzinfo=UTC))

    assert clock.past_reclaim
    assert clock.remaining == timedelta(minutes=-4)
    assert "began reclaiming" in clock.describe()
    assert "0h04m ago" in clock.describe()


def test_the_reclaim_margin_the_nodes_are_given_is_the_one_this_module_decides() -> None:
    """The launch workflow writes both of these into every node's settings file out of this
    module rather than typing them, so that the shell on the machine and the report read from a
    laptop cannot come to disagree about when the deadline is."""
    assert RECLAIM_MARGIN_MINUTES == 30
    assert DRAIN_FROM_MINUTES > RECLAIM_MARGIN_MINUTES


def test_a_node_reads_the_margin_it_was_launched_with_rather_than_todays_constant() -> None:
    """AWS could change the margin, and a fleet already up would still be running against the
    old one. A reading taken from a laptop has to be able to reproduce what the node believes,
    which is why this is a parameter and not the constant read directly."""
    clock = countdown(
        ends_at=ENDS_AT, now=datetime(2026, 8, 11, 11, 0, tzinfo=UTC), reclaim_minutes=45
    )

    assert clock.remaining == timedelta(minutes=-15)


def test_the_horizon_is_the_smallest_one_that_still_contains_the_time_left() -> None:
    """Mutation: take the largest horizon the remaining time is under.

    Twenty-eight minutes from the reclaim would then report the two-hour warning, which is the
    wrong end of the same table and is invisible to a reader -- the page says a horizon was
    crossed and looks entirely normal.
    """
    assert horizon_for(timedelta(minutes=73)) == 120
    assert horizon_for(timedelta(minutes=28)) == 30
    assert horizon_for(timedelta(minutes=15)) == 15
    assert horizon_for(timedelta(minutes=-9)) == 15


def test_a_fleet_with_hours_left_has_crossed_no_horizon_at_all() -> None:
    """The other direction. A rule that always answered would make every quarter-hourly report
    look like a warning, and a warning that is always on is not one."""
    assert horizon_for(timedelta(hours=30)) is None


def test_a_duration_is_hours_and_minutes_and_never_days() -> None:
    """Seventy-two hours is the longest window this lane serves and ``2d3h`` makes a reader do
    arithmetic at the moment they are least able to. The same rule ``elapsed_as`` follows."""
    assert remaining_as(timedelta(hours=27, minutes=6)) == "27h06m"
    assert remaining_as(timedelta(seconds=-5)) == "0h00m"


def test_a_node_that_did_not_answer_is_not_a_node_with_nothing_to_save() -> None:
    """THE READING THAT WOULD KILL SOMEBODY'S WEEKEND, so it gets the same guard
    ``parse_reading`` has.

    An invocation that timed out produces no output, and no output parses perfectly into a node
    reporting no run directories -- which renders as "nothing outstanding" beside seven machines
    that really are clean. Somebody reads that at 10:55 and stops.
    """
    reading = parse_drain_reading(
        node=4, instance_id="i-0004", status="TimedOut", output=""
    )

    assert not reading.reachable
    assert not reading.flushed
    assert reading.detail == "TimedOut"
    assert unflushed_instances([reading]) == ("i-0004",)


def test_a_node_that_answered_with_nothing_on_it_is_flushed() -> None:
    """The other direction, and it has to be different from the one above or the report says
    every idle machine in the fleet needs attention."""
    reading = answered("node\t2\nusable_seconds\t7200\nclaim\t\t\ndrained_at\t2026-08-11T09:00:00Z")

    assert reading.reachable
    assert reading.flushed
    assert unflushed_instances([reading]) == ()


def test_a_run_record_carries_both_counts_rather_than_a_verdict() -> None:
    reading = answered(
        "node\t3\n"
        "usable_seconds\t3600\n"
        "claim\teric\tshared-experts-a\n"
        "container\trunning\n"
        "run\tshared-experts-a\t124\t124\tok\n"
        "drained_at\t2026-08-11T10:30:00Z\n"
    )

    assert reading.who == "eric"
    assert reading.run == "shared-experts-a"
    assert reading.container == "running"
    assert reading.usable_seconds == 3600
    assert [(found.run, found.local, found.remote) for found in reading.runs] == [
        ("shared-experts-a", 124, 124)
    ]
    assert reading.flushed
    assert reading.drained_at == datetime(2026, 8, 11, 10, 30, tzinfo=UTC)


def test_a_sync_that_reported_success_and_left_files_behind_is_not_flushed() -> None:
    """THE MUTATION THE COUNT EXISTS FOR: believe the status word.

    ``aws s3 sync`` exits zero on a partial copy in every case that actually happens here -- a
    file rotated out from under it, one path refused, a throttle it stopped retrying. The node
    writes ``ok`` because the command succeeded; the two counts are what say it did not finish.
    """
    reading = answered("run\tcurriculum-b\t310\t298\tok\n")

    assert not reading.flushed
    assert reading.runs[0].shortfall == 12
    assert "12 files short of the 310" in outstanding(reading)


def test_a_sync_that_failed_outright_is_not_flushed_even_with_the_counts_agreeing() -> None:
    """The complement, and it is reachable: a sync that refused before copying anything leaves
    the remote count equal to whatever a previous drain put there, which can match."""
    reading = answered("run\tcurriculum-b\t8\t8\tfailed\n")

    assert not reading.flushed
    assert "reported failed" in outstanding(reading)


def test_more_objects_than_files_is_not_a_shortfall() -> None:
    """The node counts the disk before it syncs and lists S3 after, so a run writing while the
    copy happens lands on the remote side of the comparison. Earlier drains also leave objects
    for files that have since been deleted. Neither is a problem and neither may read as one."""
    reading = answered("run\tshared-experts-a\t100\t137\tok\n")

    assert reading.flushed
    assert reading.runs[0].shortfall == 0


def test_a_report_systems_manager_cut_in_half_is_not_a_report_of_what_is_there() -> None:
    """THE TRUNCATION THAT LOOKS EXACTLY LIKE GOOD NEWS.

    A tag-targeted fan-out is read back through ``list-command-invocations``, which returns a
    couple of thousand characters per node and says nothing when it drops the rest. The records
    that survive a cut are the early ones, they all parse, and the runs whose lines went missing
    read as runs that were never there -- so the more work a machine is holding, the more likely
    it is to report itself clean.

    ``drained_at`` is printed last and unconditionally, so its absence is the only evidence
    available that the answer is a prefix of the answer.
    """
    reading = cut_short("node\t1\nusable_seconds\t600\nrun\ta-run\t4\t4\tok\n")

    assert reading.truncated
    assert not reading.flushed
    assert "cut off part way through" in outstanding(reading)
    assert unflushed_instances([reading]) == ("i-0001",)


def test_a_timestamp_the_node_mangled_is_treated_as_no_terminator_rather_than_ignored() -> None:
    """The records are written by ``printf`` on a machine rather than by a serializer, so a
    malformed field is reachable -- and a half-written terminator is the shape a cut leaves
    when it lands inside the last line rather than between two. Erring towards "this answer
    cannot be trusted" is free; erring the other way is a node reported safe."""
    reading = cut_short("run\ta-run\t4\t4\tok\ndrained_at\tyesterday\n")

    assert reading.drained_at is None
    assert reading.truncated
    assert not reading.flushed


def test_a_stopped_run_is_recorded_because_somebody_asked_for_it_to_be() -> None:
    reading = answered("claim\teric\tshared-experts-a\nstopped\tshared-experts-a\n")

    assert reading.stopped == ("shared-experts-a",)


def test_a_step_directory_missing_its_last_written_marker_is_torn() -> None:
    """WHAT AN INTERRUPTED SAVE LOOKS LIKE, TRANSCRIBED FROM ``dir_is_checkpoint``.

    ``train/rank0.pt`` is written before the first ``model_and_optim`` shard and
    ``.metadata.json`` is written last, so a host taken away mid-save leaves some of the three
    and not all of them. OLMo-core clears exactly these on the way into a resume; what does not
    exist is anybody knowing one is there, on a bucket nothing resumes from after the window.
    """
    found = read_checkpoints(
        {
            "Contents": [
                object_at("block/cr-1/node-3/a-run/checkpoints/step50/.metadata.json"),
                object_at("block/cr-1/node-3/a-run/checkpoints/step50/train/rank0.pt"),
                object_at("block/cr-1/node-3/a-run/checkpoints/step50/model_and_optim/.metadata"),
                object_at("block/cr-1/node-3/a-run/checkpoints/step100/train/rank0.pt"),
                object_at(
                    "block/cr-1/node-3/a-run/checkpoints/step100/model_and_optim/.metadata",
                    written="2026-08-11T10:59:00+00:00",
                ),
            ]
        },
        prefix="block/cr-1/node-3/a-run/checkpoints",
    )

    assert found.complete == (50,)
    assert found.torn == (100,)
    assert found.latest_step == 100
    assert found.latest_written == datetime(2026, 8, 11, 10, 59, tzinfo=UTC)


def test_a_weights_only_checkpoint_is_a_checkpoint_and_not_wreckage() -> None:
    """Mutation: require all three markers of a full checkpoint everywhere.

    A directory holding only ``.metadata`` is what ``dir_is_checkpoint`` calls a checkpoint with
    no trainer state, which is how published weights are written. Calling those torn would
    report every set of weights in the bucket as damage, which is worse than reporting none.
    """
    found = read_checkpoints(
        {"Contents": [object_at("runs/x/checkpoints/step7/.metadata")]},
        prefix="runs/x/checkpoints",
    )

    assert found.torn == ()
    assert found.complete == (7,)


def test_nothing_outside_a_step_directory_is_read_as_a_checkpoint_at_all() -> None:
    """``ConfigSaverCallback`` writes ``config.json`` beside the step directories, and it is
    the record of what the run was configured to do rather than half a save."""
    found = read_checkpoints(
        {
            "Contents": [
                object_at("runs/x/checkpoints/config.json"),
                object_at("runs/x/checkpoints/notes/something.txt"),
                object_at("runs/x/checkpoints/step10/.metadata"),
            ]
        },
        prefix="runs/x/checkpoints",
    )

    assert (found.complete, found.torn) == ((10,), ())


def test_a_listing_that_stopped_early_says_so_rather_than_reporting_nothing_torn() -> None:
    """Mutation: drop the truncation flag, because the answer parses fine without it.

    A partial listing is a statement about the page size, and "no torn checkpoints" read off
    one is the single answer this must never give -- it is indistinguishable from the good news
    it is impersonating.
    """
    found = read_checkpoints(
        {"Contents": [object_at("runs/x/checkpoints/step1/.metadata")], "NextToken": "more"},
        prefix="runs/x/checkpoints",
    )

    assert found.truncated


def test_a_prefix_nothing_has_been_written_to_is_not_an_error() -> None:
    """Every run before its first save, and every run whose command writes checkpoints
    somewhere this lane does not know about."""
    found = read_checkpoints({}, prefix="runs/x/checkpoints")

    assert (found.complete, found.torn, found.latest_step) == ((), (), None)


def test_the_rows_name_the_machines_that_still_hold_something() -> None:
    rows = drain_rows(
        (
            answered("run\tshared-experts-a\t124\t124\tok\nclaim\teric\tshared-experts-a\n"),
            answered("run\tcurriculum-b\t310\t298\tok\n", node=2, instance_id="i-0002"),
            answered("", node=3, instance_id="i-0003"),
            parse_drain_reading(
                node=4, instance_id="i-0004", status="Undeliverable", output=""
            ),
            cut_short("run\tsomething\t9\t9\tok\n", node=5, instance_id="i-0005"),
        )
    )

    assert "SAVED" in rows[0]
    assert "eric" in rows[0]
    assert "IN FLIGHT" in rows[1]
    assert "12 of 310 files are not in S3" in rows[2]
    assert "NOTHING TO SAVE" in rows[3]
    assert "UNREACHABLE" in rows[4]
    assert "ANSWER CUT SHORT" in rows[5]


def test_the_summary_names_the_torn_checkpoints_and_the_machines_still_copying() -> None:
    """The page is the only surface roughly fifteen people here can read, so what it leaves out
    is not visible to them anywhere else."""
    page = drain_markdown(
        (
            answered("run\tcurriculum-b\t310\t298\tok\nclaim\tgrant\tcurriculum-b\n"),
            parse_drain_reading(node=2, instance_id="i-0002", status="TimedOut", output=""),
        ),
        clock=countdown(ends_at=ENDS_AT, now=datetime(2026, 8, 11, 10, 45, tzinfo=UTC)),
        checkpoints={
            "curriculum-b": read_checkpoints(
                {"Contents": [object_at("p/step100/train/rank0.pt")]}, prefix="p"
            )
        },
    )

    assert "0h15m until AWS starts terminating" in page
    assert "nodes with work still in flight | 2" in page
    assert "held by grant" in page
    assert "did not answer (TimedOut)" in page
    assert "step100" in page
    assert "remove_torn_checkpoints" in page


def test_the_summary_survives_a_window_where_no_fleet_is_up() -> None:
    """Which is every scheduled run before the Saturday and every one after the Tuesday."""
    page = drain_markdown(
        (), clock=countdown(ends_at=ENDS_AT, now=datetime(2026, 8, 8, 12, 0, tzinfo=UTC))
    )

    assert "nothing to drain" in page
    assert drain_rows(()) == ()
