"""Finding one run's log in a bucket, and reading the end of it without downloading all of it.

The failure modes here are quieter than the drain's and there are three worth naming.

**PICKING BETWEEN TWO RUNS THAT SHARE A NAME.** Run names are chosen by people on a shared
sheet and nothing enforces uniqueness across eight machines, so two people naming a run
``baseline`` is reachable. Printing one of the two under a heading that names the run is
indistinguishable, to the reader, from printing theirs.

**THE RANGE READ CUTTING A LINE IN HALF.** The tail is fetched by byte offset, so unless the
object is small the first line of what comes back begins mid-word -- which reads as corruption
to somebody who does not know a byte range was involved and is being asked to interpret a log.

**PAIRING THE LOSS TO THE STEP MARKER POSITIONALLY.** The console callback prints the marker and
then the metrics under it as separate lines, so a tail that lands between them has a step with
no loss beneath it. Read as a pair, the most recent loss would be dropped about as often as it
was reported.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from edullm_platform.block_logs import (
    MAXIMUM_TAIL_BYTES,
    MINIMUM_TAIL_BYTES,
    AmbiguousRunError,
    choose_run,
    common_prefixes,
    log_key,
    logs_markdown,
    progress,
    read_candidates,
    tail,
    tail_range,
)

BLOCK = "cr-0afc33f3a1af417a7"

#: What the OLMo-core console logger actually writes, with the metrics under the marker rather
#: than beside it, because that shape is the whole reason the two are read independently.
TRAINING_OUTPUT = """\
2026-08-08 12:00:01 INFO [olmo_core.train.trainer:734] Training for 2,000 steps
2026-08-08 12:01:00 INFO [olmo_core.train.callbacks.console_logger:68] [step=100/2000,epoch=0,eta=5h11m]
    train/CE loss=6.2341
    train/PPL=511.9
2026-08-08 12:03:10 INFO [olmo_core.train.trainer:1003] Saving checkpoint for step 100 to 's3://b/k'...
2026-08-08 12:03:44 INFO [olmo_core.train.trainer:1064] Checkpoint for step 100 saved successfully
2026-08-08 12:04:00 INFO [olmo_core.train.callbacks.console_logger:68] [step=200/2000,epoch=0,eta=4h58m]
    train/CE loss=5.8817
"""


def head(*, size: int, modified: str | None) -> dict[str, Any]:
    described: dict[str, Any] = {"ContentLength": size}
    if modified is not None:
        described["LastModified"] = modified
    return described


def candidate(node: int, run: str, *, size: int = 1000, modified: str | None) -> tuple[Any, ...]:
    return (node, run, log_key(reservation=BLOCK, node=node, run=run), head(size=size, modified=modified))


def test_the_key_is_the_layout_the_bootstrap_decided() -> None:
    """The reservation is a path segment because two blocks in one month is two fleets and node
    numbers repeat across them. A disagreement between this and the node's log sync is a
    workflow that reports every run as having no log."""
    assert (
        log_key(reservation=BLOCK, node=3, run="shared-experts-a")
        == f"block/{BLOCK}/node-3/shared-experts-a/log/train.log"
    )


def test_the_run_names_come_out_of_a_delimited_listing_rather_than_a_recursive_one() -> None:
    """Mutation: list the node prefix recursively and take the distinct first segments.

    A run prefix holds every checkpoint shard the trainer wrote, so that version reads
    thousands of keys to answer a question about a handful of names -- on every dispatch, by
    everybody, during the window.
    """
    listed = {
        "CommonPrefixes": [
            {"Prefix": f"block/{BLOCK}/node-3/shared-experts-a/"},
            {"Prefix": f"block/{BLOCK}/node-3/curriculum-b/"},
        ]
    }

    assert common_prefixes(listed) == ("shared-experts-a", "curriculum-b")


def test_a_run_with_no_log_object_yet_is_dropped_rather_than_reported_empty() -> None:
    """A run that was claimed and never started, and one whose container died before printing a
    line, both leave a prefix with no log under it. That is ordinary rather than an error, and
    it must not become a candidate -- choosing it would print an empty page for a run that has
    a perfectly readable log on another node."""
    found = read_candidates(
        [
            (1, "never-started", "block/x/node-1/never-started/log/train.log", None),
            candidate(2, "went-fine", modified="2026-08-08T13:00:00+00:00"),
        ]
    )

    assert [item.run for item in found] == ["went-fine"]


def test_the_most_recently_written_log_is_the_one_a_bare_node_number_means() -> None:
    """Somebody who passes only a node is asking about the run that is on it now, which after
    a weekend of iteration is the newest of several prefixes rather than the first
    alphabetically."""
    found = read_candidates(
        [
            candidate(3, "monday-idea", modified="2026-08-09T02:00:00+00:00"),
            candidate(3, "the-one-running", modified="2026-08-10T22:14:00+00:00"),
            candidate(3, "first-attempt", modified="2026-08-08T12:00:00+00:00"),
        ]
    )

    assert choose_run(found).run == "the-one-running"


def test_a_log_with_no_readable_timestamp_sorts_last_rather_than_raising() -> None:
    """Mutation: sort on the parsed timestamps directly.

    Python refuses to order a timezone-aware datetime against anything standing in for a
    missing one, so that version raises on a listing rather than returning a worse ordering --
    and it raises inside a workflow whose whole purpose is to answer somebody who has no other
    way to look.
    """
    found = read_candidates(
        [
            candidate(1, "undated", modified=None),
            candidate(1, "dated", modified="2026-08-10T22:14:00+00:00"),
        ]
    )

    assert [item.run for item in found] == ["dated", "undated"]


def test_a_run_name_on_two_nodes_is_a_refusal_that_names_both() -> None:
    """THE MUTATION WORTH THE MOST HERE: take the first match.

    Nothing stops two people on a shared sheet calling a run ``baseline``. Printing one of the
    two logs under a heading naming the run looks exactly like printing the other, and the
    reader has no way to tell which they were shown.
    """
    found = read_candidates(
        [
            candidate(2, "baseline", modified="2026-08-09T09:00:00+00:00"),
            candidate(6, "baseline", modified="2026-08-09T10:00:00+00:00"),
        ]
    )

    with pytest.raises(AmbiguousRunError) as refused:
        choose_run(found, run="baseline")

    assert "run_is_on_more_than_one_node:baseline" in refused.value.reason
    assert "2, 6" in refused.value.reason


def test_a_name_that_matches_nothing_says_what_is_actually_there() -> None:
    """A typo in a run name is the likeliest way this is used wrongly, and the reader cannot
    list the bucket to find out what they should have typed."""
    found = read_candidates([candidate(2, "shared-experts-a", modified="2026-08-09T09:00:00Z")])

    with pytest.raises(AmbiguousRunError) as refused:
        choose_run(found, run="shared-experts-b")

    assert "shared-experts-a" in refused.value.reason


def test_a_prefix_with_no_runs_under_it_refuses_rather_than_returning_nothing() -> None:
    with pytest.raises(AmbiguousRunError) as refused:
        choose_run(())

    assert "no_run_has_a_log_here" in refused.value.reason


def test_the_range_is_bounded_at_both_ends() -> None:
    """The floor stops a small request fetching so little that one wrapped traceback fills it.
    The ceiling is what keeps a request for ten thousand lines from pulling a large object
    through a runner to print into a page GitHub truncates at a mebibyte."""
    start, length = tail_range(size=10_000_000, lines=10)
    assert length == MINIMUM_TAIL_BYTES
    assert start == 10_000_000 - MINIMUM_TAIL_BYTES

    _, wide = tail_range(size=10_000_000, lines=100_000)
    assert wide == MAXIMUM_TAIL_BYTES


def test_an_object_smaller_than_the_window_is_read_whole_from_its_start() -> None:
    """A run that started a minute ago. Reading it from a negative offset, or from the same
    offset as a large object, is how the first look at a run that has just begun comes back
    empty."""
    assert tail_range(size=900, lines=200) == (0, 900)


def test_an_object_of_no_bytes_asks_for_no_range() -> None:
    """S3 answers an unsatisfiable range with an error rather than with nothing, so a log file
    that exists and is empty would fail the fetch instead of printing an empty tail."""
    assert tail_range(size=0, lines=200) == (0, 0)


def test_the_first_line_is_dropped_only_when_the_range_actually_cut_one() -> None:
    """Mutation: always drop it, or never.

    Always drops the first real line of a short log, which for a run that died on an import is
    the line somebody needed. Never leaves a line beginning mid-word at the top of the page,
    which reads as corruption.
    """
    body = "half a li\nsecond\nthird\n"

    assert tail(body, 10, partial_first_line=True) == "second\nthird"
    assert tail(body, 10, partial_first_line=False) == "half a li\nsecond\nthird"


def test_the_step_and_the_loss_are_read_independently_of_each_other() -> None:
    """THE MUTATION THIS PARSING EXISTS FOR: pair the loss to the marker above it.

    The console callback logs the marker and the metrics as separate lines of one record, so a
    tail can land between the two. Paired, the most recent loss is dropped roughly as often as
    it is reported, and what the page shows instead is a step from one record and nothing at
    all from the next.
    """
    measured = progress(TRAINING_OUTPUT + "[step=300/2000,epoch=0,eta=4h40m]\n")

    assert measured.step == 300
    assert measured.max_steps == "2000"
    assert measured.epoch == 0
    assert measured.loss == 5.8817


def test_a_checkpoint_that_started_and_never_reported_landing_is_in_flight() -> None:
    """The state the drain cares about. A run interrupted here is the one that leaves a torn
    ``stepN`` directory behind in the bucket, and it is visible in the log before it is
    visible anywhere else."""
    measured = progress(TRAINING_OUTPUT + "Saving checkpoint for step 200 to 's3://b/k'...\n")

    assert measured.checkpoint_started == 200
    assert measured.checkpoint_landed == 100
    assert measured.checkpoint_in_flight


def test_a_checkpoint_that_landed_is_not_in_flight() -> None:
    measured = progress(TRAINING_OUTPUT)

    assert (measured.checkpoint_started, measured.checkpoint_landed) == (100, 100)
    assert not measured.checkpoint_in_flight


def test_a_tail_that_caught_only_the_landing_still_reports_the_checkpoint() -> None:
    """Mutation: decide the phrase on whether a save was seen *starting*.

    A save prints twice and a byte-range tail can begin between the two lines, so a landing
    with no start above it is ordinary. Reading it as "no checkpoint in this tail" hides the
    most recent one the run actually wrote, which is the number somebody is looking for when
    they are deciding whether a run is worth restarting.
    """
    found = read_candidates([candidate(1, "a-run", modified=None)])
    body = "INFO [trainer:1064] Checkpoint for step 400 saved successfully\n"

    page = logs_markdown(
        found[0],
        reservation=BLOCK,
        body=body,
        measured=progress(body),
        lines=10,
        bucket="b",
    )

    assert "| last checkpoint | step400 saved |" in page


def test_the_line_that_reads_as_trouble_is_lifted_above_the_log() -> None:
    """The question asked in the ninety seconds after a run stops moving is whether something
    broke, and the answer is one line somewhere in two hundred. Being approximately right about
    which line is worth much more here than being exhaustive about exception classes."""
    measured = progress(TRAINING_OUTPUT + "torch.OutOfMemoryError: CUDA out of memory.\n")

    assert measured.trouble is not None
    assert "CUDA out of memory" in measured.trouble


def test_a_tail_carrying_no_metrics_at_all_reports_that_rather_than_guessing() -> None:
    """Which is every run in its first minute, and every run that died before training."""
    measured = progress("ImportError: cannot import name 'Trainer'\n")

    assert (measured.step, measured.loss, measured.checkpoint_started) == (None, None, None)
    assert measured.trouble is None  # an ImportError line carries none of the words


def test_the_page_carries_the_log_and_the_numbers_above_it() -> None:
    found = read_candidates([candidate(3, "shared-experts-a", size=4096, modified="2026-08-10T22:14:00+00:00")])
    body = tail(TRAINING_OUTPUT, 200, partial_first_line=False)

    page = logs_markdown(
        found[0],
        reservation=BLOCK,
        body=body,
        measured=progress(body),
        lines=200,
        bucket="edullm-block-outputs-us-east-2",
    )

    assert "### `shared-experts-a` on node 3" in page
    assert "| step | 200 of 2000 |" in page
    assert "| last logged loss | 5.8817 |" in page
    assert "| last checkpoint | step100 saved |" in page
    assert "2026-08-10T22:14:00+00:00" in page
    assert "train/CE loss=6.2341" in page
    assert page.count("```") == 2


def test_the_page_says_when_the_tail_carried_no_step_at_all() -> None:
    """Mutation: print ``None``. A reader who has no other window onto the run cannot tell a
    missing measurement from a measurement of nothing."""
    found = read_candidates([candidate(1, "just-started", size=40, modified=None)])

    page = logs_markdown(
        found[0],
        reservation=BLOCK,
        body="starting",
        measured=progress("starting"),
        lines=10,
        bucket="b",
    )

    assert "| step | not yet reported |" in page
    assert "| last logged loss | none in this tail |" in page
    assert "| last written | unknown |" in page


def test_a_timestamp_is_read_as_the_instant_it_is() -> None:
    found = read_candidates([candidate(1, "a-run", modified="2026-08-10T22:14:00+00:00")])

    assert found[0].modified == datetime(2026, 8, 10, 22, 14, tzinfo=UTC)
