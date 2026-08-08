"""Finding and fetching one run's log, with the AWS CLI replaced by a recording of its answers.

Three things here are worth holding to a test.

**A MISSING LOG IS AN ANSWER AND NOT A FAILURE.** ``head-object`` on a run that never printed a
line answers 404, and the probe goes through ``capture_tooling.aws`` rather than ``aws_json``
for exactly that reason -- the second turns a non-zero exit into a raised capture failure, which
would take the whole page down over a prefix that is allowed to be empty.

**THE TAIL IS A RANGE READ.** Somebody will reach for ``aws s3 cp s3://... -`` because it is one
line, and it downloads three days of training output to print two hundred lines of it.

**THE PAGE IS THE PRODUCT.** This workflow exists so that the people here with no AWS role can
read a log, so a run that resolves and a page that never reaches ``GITHUB_STEP_SUMMARY`` is the
same as no workflow at all.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from edullm_platform.block_logs import log_key
from tools import block_logs

BLOCK = "cr-0afc33f3a1af417a7"
BODY = (
    "2026-08-08 12:01:00 INFO [console_logger:68] [step=200/2000,epoch=0,eta=4h58m]\n"
    "    train/CE loss=5.8817\n"
    "2026-08-08 12:03:44 INFO [trainer:1064] Checkpoint for step 200 saved successfully\n"
)


def prefixes(*names: str) -> dict[str, Any]:
    return {"CommonPrefixes": [{"Prefix": f"whatever/{name}/"} for name in names]}


class FakeJson:
    """The calls whose answer is required: the delimited listings and the ranged fetch."""

    def __init__(self, *, answers: dict[str, Any], body: str = BODY) -> None:
        self.answers = answers
        self.body = body
        self.calls: list[list[str]] = []

    def __call__(
        self, arguments: Sequence[str], *, profile: str | None = None, region: str | None = None
    ) -> Any:
        self.calls.append(list(arguments))
        head = " ".join(arguments[:2])
        if head == "s3api get-object":
            # The CLI writes the object body to the path it is given and prints its metadata on
            # stdout, which is the shape the tool depends on and therefore the shape to fake.
            Path(arguments[-1]).write_text(self.body, encoding="utf-8")
            return {"ContentLength": len(self.body)}
        return self.answers[self._prefix_of(arguments)]

    @staticmethod
    def _prefix_of(arguments: Sequence[str]) -> str:
        return arguments[arguments.index("--prefix") + 1]

    def argv_for(self, head: str) -> list[str]:
        matching = [call for call in self.calls if " ".join(call[:2]) == head]
        assert matching, f"nothing called {head}"
        return matching[0]

    def called(self, head: str) -> bool:
        return any(" ".join(call[:2]) == head for call in self.calls)


class FakeHead:
    """``head-object``, which is allowed to answer that there is nothing there."""

    def __init__(self, *, present: dict[str, dict[str, Any]]) -> None:
        self.present = present
        self.keys: list[str] = []

    def __call__(
        self, arguments: Sequence[str], *, profile: str | None = None, region: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        key = arguments[arguments.index("--key") + 1]
        self.keys.append(key)
        described = self.present.get(key)
        if described is None:
            return subprocess.CompletedProcess(
                args=list(arguments), returncode=254, stdout="", stderr="An error occurred (404)"
            )
        return subprocess.CompletedProcess(
            args=list(arguments), returncode=0, stdout=json.dumps(described), stderr=""
        )


def described(size: int, modified: str) -> dict[str, Any]:
    return {"ContentLength": size, "LastModified": modified}


@pytest.fixture
def one_block(monkeypatch: pytest.MonkeyPatch) -> tuple[FakeJson, FakeHead]:
    listings = FakeJson(
        answers={
            "block/": prefixes(BLOCK),
            f"block/{BLOCK}/": prefixes("node-3", "node-4"),
            f"block/{BLOCK}/node-3/": prefixes("shared-experts-a", "abandoned"),
            f"block/{BLOCK}/node-4/": prefixes("curriculum-b"),
        }
    )
    heads = FakeHead(
        present={
            log_key(reservation=BLOCK, node=3, run="shared-experts-a"): described(
                4096, "2026-08-10T22:14:00+00:00"
            ),
            log_key(reservation=BLOCK, node=4, run="curriculum-b"): described(
                900, "2026-08-09T08:00:00+00:00"
            ),
        }
    )
    monkeypatch.setattr(block_logs, "aws_json", listings)
    monkeypatch.setattr(block_logs, "aws", heads)
    return listings, heads


def test_a_run_that_never_printed_a_line_does_not_take_the_whole_page_down(
    one_block: tuple[FakeJson, FakeHead],
) -> None:
    """Mutation: probe with ``aws_json``, which raises on a non-zero exit.

    ``abandoned`` is a run somebody claimed and never started: it has a prefix in the bucket and
    no log under it, which is ordinary. Treating that 404 as a capture failure would refuse the
    dispatch of somebody asking about a completely healthy run on the same node.
    """
    _, record = block_logs.resolve(
        bucket="b", reservation_id=None, node=3, run=None, profile=None, region="us-east-2"
    )

    assert record.run == "shared-experts-a"


def test_with_no_node_every_node_is_searched_and_the_newest_log_wins(
    one_block: tuple[FakeJson, FakeHead],
) -> None:
    """The person most likely to run this was told "look at the block" and nothing else."""
    reservation, record = block_logs.resolve(
        bucket="b", reservation_id=None, node=None, run=None, profile=None, region="us-east-2"
    )

    assert reservation == BLOCK
    assert (record.node, record.run) == (3, "shared-experts-a")


def test_a_named_run_on_another_node_is_found_without_being_told_which(
    one_block: tuple[FakeJson, FakeHead],
) -> None:
    _, record = block_logs.resolve(
        bucket="b",
        reservation_id=BLOCK,
        node=None,
        run="curriculum-b",
        profile=None,
        region="us-east-2",
    )

    assert record.node == 4


def test_the_tail_is_a_range_read_rather_than_a_download(
    one_block: tuple[FakeJson, FakeHead],
) -> None:
    """THE MUTATION SOMEBODY WOULD MAKE FOR BREVITY: ``aws s3 cp s3://... -``.

    One line, obviously correct, and it pulls a seventy-two hour training log through a runner
    to print the last two hundred lines of it -- on every dispatch, by everybody, during the
    window they are all trying to use.
    """
    listings, _ = one_block
    _, record = block_logs.resolve(
        bucket="b", reservation_id=BLOCK, node=3, run=None, profile=None, region="us-east-2"
    )

    body, partial = block_logs.fetch_tail(
        bucket="b", record=record, lines=200, profile=None, region="us-east-2"
    )

    argv = listings.argv_for("s3api get-object")
    assert not listings.called("s3 cp")
    assert argv[argv.index("--range") + 1] == "bytes=0-4095"
    assert body == BODY
    assert partial is False


def test_a_log_larger_than_the_window_is_fetched_from_the_end_of_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """And the caller is told the first line was cut, which is the half that makes the page
    readable rather than merely cheap."""
    listings = FakeJson(answers={})
    monkeypatch.setattr(block_logs, "aws_json", listings)
    record = block_logs.RunLog(
        node=3, run="a-run", key="block/x/log/train.log", size=10_000_000, modified=None
    )

    _, partial = block_logs.fetch_tail(
        bucket="b", record=record, lines=200, profile=None, region="us-east-2"
    )

    argv = listings.argv_for("s3api get-object")
    assert argv[argv.index("--range") + 1] == "bytes=9897600-9999999"
    assert partial is True


def test_the_page_reaches_the_job_summary_and_carries_the_numbers(
    one_block: tuple[FakeJson, FakeHead], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The whole workflow is a page for people who cannot read the bucket, so a run that
    resolves and a summary that stays empty is the same as this not existing."""
    summary = tmp_path / "summary.md"

    exit_code = block_logs.main(
        ["--no-profile", "--node", "3", "--lines", "50", "--summary", str(summary), "--bucket", "b"]
    )
    page = summary.read_text(encoding="utf-8")

    assert exit_code == 0
    assert "### `shared-experts-a` on node 3" in page
    assert "| step | 200 of 2000 |" in page
    assert "| last logged loss | 5.8817 |" in page
    assert "| last checkpoint | step200 saved |" in page
    assert "train/CE loss=5.8817" in page
    # Printed as well as written, so that a maintainer running this from a laptop with no
    # GITHUB_STEP_SUMMARY to append to still sees the thing they asked for.
    assert "shared-experts-a" in capsys.readouterr().out


def test_more_than_one_block_in_the_bucket_is_a_refusal_that_names_them(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The bucket outlives the fleet, so from the second window onwards this is the normal
    state rather than an edge case, and picking one would answer a question about a run in
    August with a run from July."""
    monkeypatch.setattr(
        block_logs, "aws_json", FakeJson(answers={"block/": prefixes(BLOCK, "cr-older")})
    )

    assert block_logs.main(["--no-profile", "--bucket", "b"]) == 1
    assert "reservations_under_the_bucket:2" in capsys.readouterr().err


def test_a_line_count_beyond_what_a_summary_holds_is_capped_rather_than_refused(
    one_block: tuple[FakeJson, FakeHead], tmp_path: Path
) -> None:
    """GitHub truncates a job summary at one mebibyte, in the middle of whatever is there. A
    page that stops mid-traceback is worse than one that printed fewer lines and said so."""
    summary = tmp_path / "summary.md"

    assert (
        block_logs.main(
            [
                "--no-profile",
                "--node",
                "3",
                "--lines",
                "999999",
                "--bucket",
                "b",
                "--summary",
                str(summary),
            ]
        )
        == 0
    )
    assert "Last 5000 lines" in summary.read_text(encoding="utf-8")


def test_a_laptop_gets_a_profile_and_a_runner_can_take_it_away() -> None:
    parser = block_logs.build_parser()

    assert parser.parse_args([]).profile == "sbsandbox"
    assert parser.parse_args(["--no-profile"]).profile is None
    assert parser.parse_args([]).bucket == "edullm-block-outputs-us-east-2"
    assert parser.parse_args([]).lines == 200
    assert parser.parse_args([]).node is None
