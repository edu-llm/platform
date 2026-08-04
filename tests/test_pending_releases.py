"""The one thing allowed to turn a red release tripwire green, and its limits.

`tests/test_phase2_lambda_package.py::test_the_released_zip_is_the_one_this_tree_builds`
and its Phase 3 sibling compare a zip built from this tree against the digest the release
record says is deployed. They are the only things in the repository that see
deployed-versus-tree skew, and `infra/admission-validator-release.yaml` records what one
missed release cost: a GPU submission refused with `unprovisioned_compute_profile` by a
validator holding the previous catalog, correct for its own bytes and wrong about the
account.

They were also unclearable before the merge that caused them. The zip is uploaded by
`deploy-phase2-admission.yml`, which runs from `main` and nowhere else, so a change to a
packaged module could not be released until it merged and could not merge green until it was
released. Every such change landed by an administrator merging past a required check.

So :mod:`edullm_platform.pending_amendments` now carries a second kind of record beside the
undeployed IAM amendment it was written for, and this module is where that record is held to
being narrower than the thing it stands in for. What it must not become is a way of turning
the tripwire off, which is why almost every case below is about a record *failing* to
explain something rather than succeeding.
"""

from __future__ import annotations

import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml

from edullm_platform import pending_amendments
from edullm_platform.pending_amendments import (
    PENDING_RELEASES,
    RELEASABLE_FUNCTIONS,
    RELEASE_COMMAND,
    RELEASE_WINDOW,
    PendingRelease,
    PendingReleaseError,
    ReleaseVerdict,
    compare_release,
    one_record_per_function,
    pending_release_for,
    releases_beyond_their_window,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "tools"))

from release_lambda import FUNCTIONS

BUILT = "a" * 64
DEPLOYED = "b" * 64
TODAY = date(2026, 8, 4)


def a_record(**overrides: Any) -> PendingRelease:
    fields: dict[str, Any] = {
        "function": "validator",
        "reason": "the container memory correction moved execution.py, which the zip carries.",
        "cleared_by": "uv run python tools/release_lambda.py --function validator",
        "builds_to": BUILT,
        "released": DEPLOYED,
        "recorded_on": TODAY,
        **overrides,
    }
    return PendingRelease(**fields)


def recorded_digest(function: str) -> str:
    path = PROJECT_ROOT / RELEASABLE_FUNCTIONS[function].release_record
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    digest = loaded["sha256"]
    assert isinstance(digest, str)
    return digest


# --------------------------------------------------------------------------------------
# The register itself
# --------------------------------------------------------------------------------------


def test_the_releasable_functions_are_the_ones_the_release_tool_can_release() -> None:
    """Mutation: rename a function in the tool and leave this register alone.

    The register restates the two functions because a library module importing `tools/`
    is the wrong direction. A restatement held to nothing is how a pending release gets
    recorded for a key `tools/release_lambda.py` no longer has, at which point the command
    in `cleared_by` is one nobody can run and the record can only lapse.

    Both directions, and both fields. The display name is what a skipped test prints and
    the record path is what it tells a reader to look at, so a rename that reaches one
    spelling and not the other produces a skip pointing at a file that is not there.
    """
    assert set(RELEASABLE_FUNCTIONS) == set(FUNCTIONS)
    for key, releasable in RELEASABLE_FUNCTIONS.items():
        assert releasable.display == FUNCTIONS[key].name
        assert (
            PROJECT_ROOT / releasable.release_record
        ).resolve() == FUNCTIONS[key].release_record.resolve()


def test_every_recorded_release_says_what_it_waits_on_and_what_ends_it() -> None:
    # The fields that distinguish a record somebody is waiting on from an exemption, held
    # over whatever the register happens to carry. Empty is its ordinary state and the
    # loop is then vacuous, which is fine: the cases below build their own records.
    for release in PENDING_RELEASES:
        assert release.function in RELEASABLE_FUNCTIONS
        assert release.reason.strip()
        assert RELEASE_COMMAND in release.cleared_by
        assert release.builds_to != release.released


def test_no_recorded_release_has_stood_longer_than_the_window_allows() -> None:
    """THE CASE THAT MAKES A FORGOTTEN RELEASE VISIBLE, AND IT IS DELIBERATELY CHEAP.

    Mutation: leave a record in place and never cut the release.

    The comparison this register feeds needs a zip, so it costs a `uv pip install`, is
    marked slow, and is skipped by `-m "not slow"`. A window enforced only there would let
    a lapsed record sit green on every fast run. This needs nothing but the clock, so a
    release nobody cut becomes a failure on the run everybody makes.
    """
    lapsed = releases_beyond_their_window()

    assert lapsed == (), "\n".join(
        f"{release.function}: recorded {release.recorded_on.isoformat()}, stopped explaining "
        f"anything after {release.expires_on.isoformat()}. Run "
        f"`{release.cleared_by.strip()}` from main, or replace the entry with a fresh one "
        "saying why it still cannot be cut."
        for release in lapsed
    )


def test_every_recorded_release_still_describes_what_the_release_record_says() -> None:
    """The other cheap half of self-clearing. Mutation: cut the release, keep the record.

    `tools/release_lambda.py` writes the new digest into the release record and cannot
    delete Python, so the entry survives the release that clears it unless the same commit
    removes it. Reading the record's `sha256` costs one file read, so the leftover is a
    failure here rather than something only the slow tripwire notices.
    """
    stale = [
        release
        for release in PENDING_RELEASES
        if release.released != recorded_digest(release.function)
    ]

    assert stale == [], "\n".join(
        f"{release.function}: the record is waiting for {release.builds_to} to replace "
        f"{release.released}, and "
        f"{RELEASABLE_FUNCTIONS[release.function].release_record} now says "
        f"{recorded_digest(release.function)} is deployed. Delete the entry."
        for release in stale
    )


def test_the_window_is_shorter_than_a_capture_s_and_long_enough_for_a_review() -> None:
    # Not a number this file gets to choose freely. Below a couple of days it would go red
    # on any pull request that waits for a reviewer, and a window that routinely expires
    # before the thing it is waiting on is a window people learn to renew. Above a capture's
    # thirty days it would outlast the memory of whoever moved the bytes.
    assert timedelta(days=2) < RELEASE_WINDOW < timedelta(days=30)


# --------------------------------------------------------------------------------------
# A record a reader could not act on
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("broken", "expected"),
    [
        ({"function": "some-other-lambda"}, "names no releasable function"),
        ({"reason": "   "}, "does not say reason"),
        ({"cleared_by": ""}, "does not say cleared by"),
        ({"builds_to": DEPLOYED}, "nothing to clear"),
        ({"builds_to": "not a digest"}, "not sixty-four lowercase"),
        ({"released": "B" * 64}, "not sixty-four lowercase"),
        (
            {"cleared_by": "release it from main"},
            f"does not name {RELEASE_COMMAND}",
        ),
        (
            {"cleared_by": "uv run python tools/release_lambda.py --function recorder"},
            "does not select validator",
        ),
        ({"recorded_on": datetime.now(tz=UTC).date() + timedelta(days=1)}, "in the future"),
    ],
    ids=[
        "a function nothing releases",
        "no reason",
        "no trigger",
        "no difference",
        "a digest that is not one",
        "an uppercase digest",
        "a command that is not the command",
        "a command for the other function",
        "dated ahead of the clock",
    ],
)
def test_a_pending_release_a_reader_could_not_act_on_is_refused(
    broken: dict[str, Any], expected: str
) -> None:
    """Every field is checked because every field is load-bearing in the skip message.

    Three of these are worth reading twice. A record whose two digests are equal describes
    no difference and would sit in the register explaining nothing. A record whose
    `cleared_by` names the *other* function gives a reader a command that releases the wrong
    zip and leaves this one exactly as it was. And a record dated in the future is the way
    to stand for longer than `RELEASE_WINDOW` allows without saying so -- the window is
    measured from that field, so postdating it by a month is a month of silence bought with
    one character.
    """
    with pytest.raises(PendingReleaseError, match=expected):
        a_record(**broken)


def test_a_record_may_name_the_command_that_releases_both() -> None:
    # `--function all` is the tool's default and is a correct way to clear either record.
    # Refusing it would push people towards editing the message rather than the register.
    assert a_record(cleared_by="uv run python tools/release_lambda.py --function all")


def test_a_pending_release_explains_only_the_exact_pair_it_records() -> None:
    """Equality in both directions, which is the whole of what makes this self-clearing.

    Mutation: accept any mismatch while a record for this function exists.

    Containment or a one-sided match would let a second packaged change arrive under cover
    of the first -- the record was written for a config edit, somebody then edits a contract
    the handler imports, and the skip covers both while naming one. It would also go on
    reading as explained after a release somebody else cut moved the deployed digest out
    from under it.
    """
    record = a_record()

    assert record.explains(built=BUILT, released=DEPLOYED)
    assert not record.explains(built="c" * 64, released=DEPLOYED)
    assert not record.explains(built=BUILT, released="c" * 64)
    assert not record.explains(built=DEPLOYED, released=BUILT)


def test_a_record_lapses_the_day_after_its_window_and_not_before() -> None:
    record = a_record()

    assert record.expires_on == TODAY + RELEASE_WINDOW
    assert not record.expired(TODAY)
    assert not record.expired(record.expires_on)
    assert record.expired(record.expires_on + timedelta(days=1))


def test_what_a_skipped_release_test_prints_names_the_function_digests_and_command() -> None:
    """Mutation: skip with "waiting on a deploy" and nothing else.

    A skip that says only that something is expected tells its reader that things are fine,
    which is the failure this whole mechanism is trying not to commit. Everything needed to
    act has to be in the message, because the register is the last place a reader would
    think to look.
    """
    printed = a_record().describe()

    assert "admission validator" in printed
    assert BUILT in printed
    assert DEPLOYED in printed
    assert "infra/admission-validator-release.yaml" in printed
    assert "tools/release_lambda.py --function validator" in printed
    assert (TODAY + RELEASE_WINDOW).isoformat() in printed


# --------------------------------------------------------------------------------------
# The comparison the tripwires make
# --------------------------------------------------------------------------------------


def register(monkeypatch: pytest.MonkeyPatch, *records: PendingRelease) -> None:
    """Put exactly these records in the register for the length of one case.

    Every case below sets the register, including the ones that want it empty, and that is
    not ceremony. `tests/test_phase1_deployed_roles.py` records the mistake being avoided:
    its equivalent read the live register, so the day a real entry appeared -- or the day
    one was cleared -- the case proving the self-clearing rule was the case that broke. A
    register whose ordinary state is empty makes that failure arrive exactly when somebody
    is already dealing with something else.
    """
    monkeypatch.setattr(pending_amendments, "PENDING_RELEASES", records)


def test_a_mismatch_with_nothing_recorded_is_skew_and_says_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE CASE THE TRIPWIRE EXISTS FOR, AND NOTHING ADDED HERE MAY SOFTEN IT.

    Mutation: return PENDING_RELEASE when the register is empty.

    An empty register is the ordinary state, so this is the path almost every mismatch
    takes. It has to stay the loud one: the deployed-versus-tree window is the only thing
    this repository has that sees a config edit nobody released, and it has already been
    bitten once by a deploy that failed silently and rolled back.
    """
    register(monkeypatch)

    answer = compare_release("validator", built=BUILT, released=DEPLOYED)

    assert answer.verdict is ReleaseVerdict.SKEWED
    assert not answer.waiting
    assert not answer.holds
    # The escape hatch is named in the failure rather than left to be discovered, because a
    # hatch nobody can find is one an administrator merges past instead.
    assert "PendingRelease" in answer.detail
    assert str(RELEASE_WINDOW.days) in answer.detail


def test_a_matching_pair_holds_and_needs_no_record(monkeypatch: pytest.MonkeyPatch) -> None:
    register(monkeypatch)

    answer = compare_release("validator", built=BUILT, released=BUILT)

    assert answer.verdict is ReleaseVerdict.MATCHES
    assert answer.holds


def test_the_recorded_pair_becomes_a_labelled_skip(monkeypatch: pytest.MonkeyPatch) -> None:
    record = a_record()
    register(monkeypatch, record)

    answer = compare_release("validator", built=BUILT, released=DEPLOYED)

    assert answer.verdict is ReleaseVerdict.PENDING_RELEASE
    assert answer.waiting
    assert answer.detail == record.describe()


def test_a_record_that_has_lapsed_fails_again(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mutation: check the digests and not the date.

    The same pair, the same record, one day further on. A pending release that is never
    performed must not sit green forever, and the failure has to name the date rather than
    read as a fresh mismatch -- somebody meeting this needs to know the release is overdue,
    not that something has just moved.
    """
    record = a_record()
    register(monkeypatch, record)

    answer = compare_release(
        "validator",
        built=BUILT,
        released=DEPLOYED,
        today=record.expires_on + timedelta(days=1),
    )

    assert answer.verdict is ReleaseVerdict.SKEWED
    assert record.recorded_on.isoformat() in answer.detail
    assert record.expires_on.isoformat() in answer.detail


def test_a_second_packaged_change_arriving_after_the_record_is_not_covered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mutation: skip whenever a record for this function exists.

    This is the case a looser check would hide, and it is the likely one: a branch records
    a pending release, review takes two days, and somebody pushes another commit touching a
    packaged file. The built digest moves, the record still names the old one, and a
    containment check would go on skipping while describing a change that is no longer the
    whole difference.
    """
    register(monkeypatch, a_record())

    answer = compare_release("validator", built="c" * 64, released=DEPLOYED)

    assert answer.verdict is ReleaseVerdict.SKEWED
    assert "is not this one" in answer.detail


def test_a_release_somebody_else_cut_is_not_covered_either(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The other direction. `infra/admission-validator-release.yaml` records that riding
    # somebody else's release was tried and produced a recorded digest this tree did not
    # match, because nothing makes the person cutting it notice they should carry your
    # change. A record that kept explaining across that would make the same mistake quiet.
    register(monkeypatch, a_record())

    answer = compare_release("validator", built=BUILT, released="c" * 64)

    assert answer.verdict is ReleaseVerdict.SKEWED


def test_a_record_left_behind_after_its_release_fails_rather_than_lapsing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mutation: treat a matching pair as a pass whatever the register holds.

    Once the release is cut the digests agree and the record describes nothing. Ignoring it
    would be tidy and wrong: a record nobody removed is one that will absorb the next
    difference that happens to land on the same two digests, and the next difference is the
    one nobody expects.
    """
    register(monkeypatch, a_record(builds_to=BUILT, released=DEPLOYED))

    answer = compare_release("validator", built=BUILT, released=BUILT)

    assert answer.verdict is ReleaseVerdict.SKEWED
    assert "Delete the entry" in answer.detail


def test_a_record_for_one_function_says_nothing_about_the_other(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Two zips, two records, two tripwires. A validator release that has not been cut is no
    # reason to stand down on the recorder, whose drift is the quieter of the two.
    register(monkeypatch, a_record())

    assert pending_release_for("recorder") is None
    assert compare_release("recorder", built=BUILT, released=DEPLOYED).verdict is (
        ReleaseVerdict.SKEWED
    )


def test_two_records_for_one_function_are_refused() -> None:
    # Whichever were read first would decide which difference counts as expected, and the
    # second would sit in the register describing nothing anybody checks.
    with pytest.raises(PendingReleaseError, match="one function may carry one"):
        one_record_per_function((a_record(), a_record(builds_to="c" * 64)))

    assert one_record_per_function(
        (
            a_record(),
            a_record(
                function="recorder",
                cleared_by="uv run python tools/release_lambda.py --function recorder",
            ),
        )
    )


def test_comparing_a_function_nothing_releases_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A typo in a caller would otherwise compare two digests against an empty register and
    # report skew for a function that does not exist.
    register(monkeypatch)

    with pytest.raises(PendingReleaseError, match="not a releasable function"):
        compare_release("admission-validator", built=BUILT, released=DEPLOYED)


def test_the_clock_defaults_to_utc_today(monkeypatch: pytest.MonkeyPatch) -> None:
    # The default path is the one the tripwires take, so the injected `today` in the cases
    # above must not be the only one exercised.
    register(monkeypatch, a_record(recorded_on=datetime.now(tz=UTC).date() - RELEASE_WINDOW))

    assert compare_release("validator", built=BUILT, released=DEPLOYED).waiting

    register(
        monkeypatch,
        a_record(recorded_on=datetime.now(tz=UTC).date() - RELEASE_WINDOW - timedelta(days=1)),
    )

    assert not compare_release("validator", built=BUILT, released=DEPLOYED).waiting
