"""The spine's done-condition, committed so it can be re-checked without the account.

**The synthetic job ran twice and the two lineage records are identical except for time
and id.** That sentence is the whole of the spine slice, and it was true on the day
somebody ran it. This is what keeps it checkable afterwards. The comparison the capture
produced is committed under ``fixtures/evidence/spine/``, and these assert that every
difference in it still has a name under today's cause list, that the fields which had to be
equal were and are recorded with the values they held, and that the record still says what
it does not cover.

It is not a re-run and does not pretend to be. What it catches is a cause being deleted or
narrowed later, which would make the recorded comparison stop being explained and would say
so here rather than the next time somebody spent a GPU finding out.

**WHAT THE COMMITTED RECORD DOES NOT ESTABLISH, BECAUSE A DOCSTRING IS WHERE THE NEXT
PERSON LOOKS.** The word a reader brings to this file is "reproducible", and it is wider
than what two agreeing records support.

The two runs are not the same computation. Their checkpoint payloads are the same
762,258,865 bytes long and S3 attests a different CRC32C for each, 716,708,889 of those
bytes differ, and their losses diverge from the first step. That is ordinary floating-point
reduction order on a GPU and it is not a defect. What is established is that the two runs'
*records* agree, and explicitly not that their weights do. Nothing in the lineage record
can see the difference either, because ``result.checkpoints[].checksum`` is a digest of a
listing rather than of the bytes, so the comparison prints no ``checksum`` row and that
silence is not agreement.

Also uncovered, and each for its own reason. A multi-hour run, because these took about
seven seconds of compute each. A fan-out, because both are one cell on one instance. A
resume from a checkpoint, because both wrote one and neither read one back. A real corpus,
because the workload draws random bytes and its dataset release is ``none``. The retry
path, because each run took exactly one attempt.

**Why the difference count is not asserted.** Thirteen differences is what this pair
produced, and a schema change that adds a field carrying the run id would make it fourteen
for a reason that is not a regression. What must not move is that every one of them carries
a name, so that is what is asserted.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from edullm_platform.evidence import redact_content_digests, scan_for_secrets
from edullm_platform.run_comparison import (
    REQUIRED_FIELDS,
    VARIANCE_CAUSES,
    ComparedField,
    TwoRunEvidence,
    cause_for,
)

RECORD = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "evidence"
    / "spine"
    / "two-runs.sanitized.json"
)


@pytest.fixture(scope="module")
def recorded() -> TwoRunEvidence:
    return TwoRunEvidence.model_validate_json(RECORD.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------------------
# The reader, which is what stops every test below passing vacuously
# ---------------------------------------------------------------------------------------


def test_the_committed_record_is_there_and_loads_as_what_it_says_it_is(
    recorded: TwoRunEvidence,
) -> None:
    """Mutation: delete the fixture, or commit one the model does not accept.

    Every test below reads this file, and a reader that answered an absent file with an
    empty record would make all of them pass on a tree holding no evidence at all. Loading
    through the model rather than through ``json.loads`` is what makes that impossible:
    ``does_not_establish`` and ``agreed`` are both required to be non-empty, so a hollowed
    out document fails here rather than several tests later.
    """
    assert RECORD.is_file()
    assert recorded.comparison.left != recorded.comparison.right
    assert recorded.source == "aws"
    assert recorded.environment == "sandbox"
    assert recorded.observed_at.tzinfo is not None


def test_the_two_runs_disagreed_about_something(recorded: TwoRunEvidence) -> None:
    """Mutation: commit a comparison of a run with itself.

    Two runs carry two ids and two timestamps, so a comparison with no differences at all
    is a comparison that was not performed on two runs. It would pass every assertion about
    causes by having nothing to check.
    """
    paths = {one.path for one in recorded.comparison.differences}

    assert {"intent.run_id", "decision.run_id", "result.run_id"} <= paths
    assert "intent.recorded_at" in paths
    assert "result.completed_at" in paths


# ---------------------------------------------------------------------------------------
# The done-condition itself
# ---------------------------------------------------------------------------------------


def test_every_difference_between_the_two_runs_carries_a_named_cause(
    recorded: TwoRunEvidence,
) -> None:
    """The done-condition, and the only assertion in this file that is it.

    Not a count. Thirteen is what this pair produced and a schema change that added a
    field derived from the run id would make it fourteen without anything regressing. The
    claim is that nothing differs which nothing explains.
    """
    assert recorded.comparison.unexplained_paths == ()
    assert all(one.cause for one in recorded.comparison.differences)


def test_every_cause_the_recorded_comparison_cites_still_exists(
    recorded: TwoRunEvidence,
) -> None:
    """Mutation: delete a cause and leave the committed comparison behind.

    A comparison whose causes have been removed since it was written is a document that
    reads as a pass and would not be recomputed as one. Checking the recorded paths against
    today's list is what makes the artifact keep meaning what it said, and it is a
    different question from the one above: that one asks whether the capture found a cause,
    this one asks whether the cause is still there and still says the same thing.
    """
    for difference in recorded.comparison.differences:
        found = cause_for(difference.path)
        assert found is not None, difference.path
        assert found.name == difference.cause


def test_an_unexplained_difference_in_the_record_would_be_caught(
    recorded: TwoRunEvidence,
) -> None:
    """The check above, run against a document carrying the failure it exists to find.

    ``tests/test_workload_dataset_reach.py`` is the standing reminder that an assertion
    can sit green over the exact state it was written to refuse. This plants a leaf no
    cause matches into the real committed comparison and requires both checks to report
    it, so a reader knows the two above are measuring something.
    """
    payload = json.loads(RECORD.read_text(encoding="utf-8"))
    payload["comparison"]["differences"].append(
        {
            "path": "intent.manifest.image_digest",
            "left": '"sha256:' + "1a" * 32 + '"',
            "right": '"sha256:' + "2b" * 32 + '"',
            "cause": None,
        }
    )
    planted = TwoRunEvidence.model_validate(payload)

    assert planted.comparison.unexplained_paths == ("intent.manifest.image_digest",)
    assert cause_for("intent.manifest.image_digest") is None
    assert recorded.comparison.unexplained_paths == ()


def test_a_cause_that_has_since_been_renamed_would_be_caught(
    recorded: TwoRunEvidence,
) -> None:
    """The other half, which the unexplained check cannot see.

    A cause narrowed or renamed after this record was written leaves the recorded
    difference citing a name that no longer exists. The comparison still carries a cause
    string, so ``unexplained_paths`` stays empty and only the comparison against today's
    list goes red.
    """
    difference = recorded.comparison.differences[0]
    renamed = difference.model_copy(update={"cause": "a cause nobody wrote"})
    found = cause_for(renamed.path)

    assert found is not None
    assert found.name != renamed.cause


# ---------------------------------------------------------------------------------------
# What agreed, which is what stops the table above being a comparison of almost nothing
# ---------------------------------------------------------------------------------------


def test_the_record_says_what_matched_and_not_only_what_differed(
    recorded: TwoRunEvidence,
) -> None:
    """A table of differences reads the same however little was compared.

    Every name in ``REQUIRED_FIELDS`` is either in ``agreed`` or is one of the two
    absences the comparison reports, so this asserts the set rather than a count. A
    required field that stopped being examined would drop out of both and be caught here.

    **Asserted as equality in both directions, because the subset this used to assert
    could not fail in the direction the list actually moves.** ``accounted`` is read out
    of a frozen record and ``REQUIRED_FIELDS`` is read out of today's code, so a subset
    gets easier to satisfy every time a name leaves the list -- and a name leaving the
    list is exactly how the comparison silently stops covering something. Mutation:
    delete any entry from ``REQUIRED_FIELDS``. Under ``<=`` the whole file stays green
    while ``compare_two_runs.py`` quietly stops requiring that field of any future pair;
    under ``==`` the deleted name is left over in ``accounted`` and this goes red.

    The other direction is not slack either. A field added to ``REQUIRED_FIELDS`` after
    this record was captured is a field the committed evidence says nothing about, and
    the honest answer to that is a re-capture rather than a test that shrugs.
    """
    agreed = {one.path for one in recorded.agreed}
    accounted = agreed | set(recorded.comparison.unverified)

    assert set(REQUIRED_FIELDS) == accounted
    assert recorded.comparison.unverified == ()


def test_the_two_dispatches_were_of_one_submission_that_nobody_released(
    recorded: TwoRunEvidence,
) -> None:
    """The three facts the done-condition rests on, none of which is in the table.

    They are absent from the differences precisely because they held, which is why they
    are read out of ``agreed``. One manifest digest makes these two dispatches of one
    submission rather than two submissions that resemble each other. The automatic class
    is what makes "nobody touching anything after submit" true. Exit code 0 is what makes
    them runs rather than attempts.
    """
    agreed = {one.path: json.loads(one.value) for one in recorded.agreed}

    assert agreed["intent.manifest_sha256"] == recorded.manifest_sha256
    assert agreed["decision.manifest_sha256"] == recorded.manifest_sha256
    assert agreed["decision.approval_class"] == "automatic"
    assert agreed["decision.approving_environment"] == "run-approval-automatic"
    assert agreed["result.outcome"] == "succeeded"
    assert agreed["result.exit_code"] == 0
    assert agreed["attempt_count"] == 1


def test_a_header_digest_the_records_do_not_carry_is_refused() -> None:
    """Mutation: hand-edit the digest at the top of the file.

    ``manifest_sha256`` is quoted in the header because it is the subject of the claim,
    and a quoted value nothing checks is a value somebody will eventually retype.
    """
    payload = json.loads(RECORD.read_text(encoding="utf-8"))
    payload["manifest_sha256"] = "sha256:" + "3c" * 32

    with pytest.raises(ValidationError, match="not the digest"):
        TwoRunEvidence.model_validate(payload)


# ---------------------------------------------------------------------------------------
# What the record refuses to let a reader believe
# ---------------------------------------------------------------------------------------


def test_the_record_says_what_it_does_not_establish(recorded: TwoRunEvidence) -> None:
    """The caveats are in the artifact, because the artifact is what somebody will find.

    A plan explains this at length and nobody opens a plan to read a fixture. The model
    requires at least one line, so this asserts the ones that are load bearing are the
    ones present rather than that the field is populated.
    """
    written = "\n".join(recorded.does_not_establish).lower()

    assert len(recorded.does_not_establish) >= 5
    assert "not the same computation" in written
    assert "records agree" in written
    for uncovered in ("multi-hour", "fan-out", "resume", "corpus", "retry"):
        assert uncovered in written


def test_the_record_says_the_weights_differ_and_the_store_attests_it(
    recorded: TwoRunEvidence,
) -> None:
    """The one fact no lineage record can carry, and the reason the caveat is not a guess.

    Two payloads of one length with two CRC32C values is S3 speaking about the bytes.
    Asserting the sizes are equal as well as the checksums differing is what separates
    "two runs computed different weights", which is expected, from "one checkpoint was
    truncated", which would be a finding.
    """
    payloads = recorded.checkpoint_payloads

    assert payloads is not None
    assert payloads.left_size_bytes == payloads.right_size_bytes
    assert not payloads.payloads_agree
    assert payloads.differing_bytes
    assert payloads.measured_by


def test_the_committed_record_carries_no_credential() -> None:
    """The scan the capture ran, asserted again against what actually landed.

    Digests are masked before the scan for the reason
    ``tests/test_phase1_rebuild_comparison.py`` masks them. The record legitimately holds a
    manifest digest, an image digest and a commit SHA, and forty hexadecimal characters is
    also the shape of an AWS secret access key.
    """
    text = RECORD.read_text(encoding="utf-8")

    assert scan_for_secrets(redact_content_digests(text)) is not None
    assert "AKIA" not in text
    assert "ASIA" not in text
    assert "-----BEGIN" not in text


def test_a_record_with_nothing_it_declines_to_establish_is_refused() -> None:
    """Mutation: drop the caveats and keep the comparison.

    The document would still load, still pass every assertion about causes, and still read
    as a proof of something wider than it is. Requiring the field is how the artifact is
    stopped from being written that way in the first place.
    """
    payload = json.loads(RECORD.read_text(encoding="utf-8"))
    payload["does_not_establish"] = []

    with pytest.raises(ValidationError):
        TwoRunEvidence.model_validate(payload)


def test_a_difference_can_still_be_recorded_with_no_cause_at_all() -> None:
    """The model must not be what makes ``unexplained_paths`` empty.

    If ``cause`` were required, a comparison that found something nothing explains could
    not be written down, and this file would be asserting a property of the schema rather
    than a property of the two runs.
    """
    assert ComparedField(path="anything", left="1", right="2").cause is None
    assert cause_for("anything") is None
    assert VARIANCE_CAUSES
