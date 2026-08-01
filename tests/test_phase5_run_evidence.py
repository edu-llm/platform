"""What the committed captures of the pilot runs establish, and what they refuse to.

Every Phase 5 criterion that is a claim about people rather than about pure Python cites a
test in this file. The records these read were taken from the live account against the runs
a researcher who is not the author actually submitted on 2026-07-30, and against the
registry and the branch protection as they stood when the captures were taken.

**This is the first phase whose evidence is about two people rather than one mechanism.**
Twenty-five submissions preceded these and every one of them was dispatched and
self-authorized by the person who built the platform, so every decision record in the store
before today reads ``routine_self_authorized`` or ``exception_self_approved_by_admin``. What
these captures hold is the first ``routine_approved_by_lead_or_admin`` this project has ever
written, and the tests below are written against the two fields that make it worth
anything -- a submitter and an approver who are different people.

**The first test is the one that makes the rest mean anything.** A test written as "load the
record, assert the field" passes the moment the record stops being there, and a green suite
is the worst possible way to learn that the evidence went missing. So the reader refuses an
absent capture, and a test asserts that it does.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from edullm_platform.phase5_capture import (
    ADMITTED_RUN_RECORD,
    CAPTURE_ROOT,
    MissingCaptureError,
    UnreadableCaptureError,
    admitted_runs,
    branch_protection,
    published_image,
    read_capture,
    released_by_another_person,
)
from edullm_platform.phase5_evidence import (
    LEAD_APPROVAL_REASON,
    AdmittedRunEvidence,
)

#: Where a hand-written per-digest allowlist would have to be written for an image to be
#: accepted without anybody reading its findings. Read from the repository rather than
#: captured, because it is a file in this tree and a capture of it would be a second copy.
IMAGE_EXCEPTIONS = Path("config/image-exceptions.yaml")

#: The paths a released Lambda packages, which are the paths the containment argument for
#: the write grant rests on. Read from the repository for the same reason.
CODEOWNERS = Path(".github/CODEOWNERS")


# ---------------------------------------------------------------------------------------
# The reader, which is what stops every test below passing vacuously
# ---------------------------------------------------------------------------------------


def test_a_capture_that_is_not_there_is_reported_rather_than_read_as_nothing_to_prove() -> None:
    """Mutation: return None, or an empty model, when the file is absent.

    Every test in this file would then pass on a tree with no ``fixtures/evidence/phase-5/``
    at all -- and a criterion citing one of them would report itself covered by a check that
    read nothing. This is the test that makes the others worth their names.
    """
    with pytest.raises(MissingCaptureError):
        read_capture(CAPTURE_ROOT / "no-such-record.sanitized.json", AdmittedRunEvidence)


def test_a_capture_that_will_not_load_is_a_different_failure_from_an_absent_one(
    tmp_path: Path,
) -> None:
    """Mutation: catch the validation error and raise the missing-capture one.

    "Somebody committed a broken capture" and "nobody captured this" send a reader to
    different places, and collapsing them means the first arrives wearing the second's
    message -- so the reader goes looking for a capture that is sitting right there.
    """
    damaged = tmp_path / ADMITTED_RUN_RECORD
    damaged.write_text(json.dumps({"observed_at": "2026-07-30T00:00:00Z"}))

    with pytest.raises(UnreadableCaptureError):
        read_capture(damaged, AdmittedRunEvidence)


def test_every_committed_run_loads_as_the_record_its_directory_says_it_holds() -> None:
    """Mutation: commit a record under a name the reader does not look for.

    A capture written under a name the reader does not open is an absent record, and an
    absent record reads as a run that did less than it did.
    """
    runs = admitted_runs()

    assert runs, "a check over every committed run must observe at least one"
    assert all(run.run_id == run.record.run_id for run in runs), (
        "the directory a record sits in and the run id inside it must be the same run"
    )


# ---------------------------------------------------------------------------------------
# Criterion 1 -- a researcher who is not the author dispatches the workflow
# ---------------------------------------------------------------------------------------


def test_the_workflow_was_dispatched_by_somebody_who_did_not_build_the_platform() -> None:
    """Mutation: accept a run whose submitter is the author.

    Twenty-five dispatches preceded these and all twenty-five were the author's. The whole
    claim of this phase is that the twenty-sixth was not, so a check that passed on a
    self-submitted run would be measuring nothing at all.
    """
    runs = admitted_runs()

    submitters = {run.record.submitter for run in runs}
    assert submitters, "no committed run names a submitter"
    assert "philote-dev" not in submitters, (
        "every pilot run committed here has to be somebody else's; a run by the author "
        "proves only what the previous twenty-five already did"
    )
    assert all(run.record.workflow_path.endswith("submit-run.yml") for run in runs), (
        "the dispatch has to be of the submission workflow rather than of anything else"
    )


def test_the_dispatch_reached_admission_rather_than_stopping_at_the_form() -> None:
    """Mutation: count a workflow run that never produced an intent record.

    "Dispatches successfully" is not "pressed the button". A dispatch that failed on the
    first form field would leave a workflow run and nothing else, and the thing that says
    admission read the submission is a manifest hash recorded against the run id.
    """
    for run in admitted_runs():
        assert run.record.manifest_sha256.startswith("sha256:"), run.run_id
        assert run.record.workflow_run_id > 0, run.run_id


# ---------------------------------------------------------------------------------------
# Criterion 2 -- released by a lead who is not the submitter
# ---------------------------------------------------------------------------------------


def test_a_run_was_released_by_a_lead_who_is_not_the_person_who_submitted_it() -> None:
    """Mutation: compare the approver against nothing, or against a constant.

    This is the criterion the phase is named after and the one the entire two-person
    approval design exists to produce. The comparison that matters is submitter against
    approver on the *same* record: a run self-approved by a lead reads identically on every
    other field.
    """
    released = released_by_another_person()

    assert released, (
        "no committed run was released by somebody other than its submitter, which is the "
        "one thing Phase 5 exists to demonstrate"
    )
    for run in released:
        assert run.record.authorization.approver != run.record.submitter, run.run_id
        assert run.record.authorization.granted, run.run_id


def test_the_reason_code_the_two_person_design_exists_to_produce_was_written() -> None:
    """Mutation: accept any granted authorization.

    ``routine_self_authorized`` and ``exception_self_approved_by_admin`` are also granted
    authorizations, and both were written twenty-five times before today. The reason code is
    the field that distinguishes a lead releasing somebody else's run from a lead waving
    through their own, and it had never been written until these runs.
    """
    released = released_by_another_person()

    assert released
    for run in released:
        assert run.record.authorization.reason == LEAD_APPROVAL_REASON, run.run_id
        assert run.record.authorization.approval_class == "routine", run.run_id
        assert run.record.authorization.approval_scope == "organization", run.run_id


def test_the_team_the_submitter_claimed_is_recorded_as_a_claim_and_not_as_a_fact() -> None:
    """Mutation: assert ``team_verified`` is true, or stop recording it.

    ``team`` routes approval and grants nothing, and the record says so in a field rather
    than in a comment. Asserting the opposite would be asserting a binding that has not been
    built, because ``team_bindings`` is unpopulated, and the pilot limitations page tells a
    user the same thing this field tells a reader.
    """
    for run in admitted_runs():
        assert run.record.authorization.claimed_team == run.record.team, run.run_id
        assert not run.record.authorization.team_verified, (
            f"{run.run_id} records a verified team, but nothing binds a team to a person "
            "yet; a record claiming otherwise would be evidence for a control that does "
            "not exist"
        )


# ---------------------------------------------------------------------------------------
# Criterion 3 -- an image built today, with no hand-written exception, is accepted
# ---------------------------------------------------------------------------------------


def test_the_image_the_pilot_runs_were_admitted_on_was_published_from_their_commit() -> None:
    """Mutation: capture the image without checking which commit published it.

    The registry tags an image with the first twelve characters of the commit that built it,
    so the tag is what ties the digest a run was admitted on to the commit it declared.
    Without that the capture records two facts that happen to sit beside each other.
    """
    image = published_image()

    assert image.image_tag == image.commit_sha[:12], (
        "the published tag has to be the declared commit's own prefix, or the digest "
        "belongs to some other commit's build"
    )
    for run in admitted_runs():
        assert run.record.declared_commit_sha == image.commit_sha, run.run_id
        assert run.record.declared_image_digest == image.image_digest, run.run_id


def test_no_hand_written_exception_entry_stood_behind_the_image_that_was_accepted() -> None:
    """Mutation: leave the per-digest allowlist unexamined.

    This is the whole of criterion 3. Until the exceptions list was emptied, exactly two
    digests in the world could be submitted and every rebuild needed an admin's pull request
    before it could run -- which made "an image built today is accepted" unpassable by
    construction rather than merely unproven. A test that did not read the file could not
    tell the two situations apart.
    """
    document = yaml.safe_load(IMAGE_EXCEPTIONS.read_text(encoding="utf-8"))
    image = published_image()

    entries = document.get("exceptions") or ()
    assert not entries, (
        "the per-digest allowlist is not empty, so an image may be running on somebody's "
        "signature rather than on a reviewed finding"
    )
    assert image.image_digest not in json.dumps(document), (
        "the accepted digest is named in the exception file, so its acceptance says "
        "nothing about a freshly built image"
    )


def test_the_image_was_published_on_the_day_the_runs_that_used_it_were_submitted() -> None:
    """Mutation: accept an image published at any time.

    "Built from a commit pushed today" is the part of criterion 3 that stops a stale, long
    ago hand-blessed image satisfying it. The comparison is against the runs' own
    submissions rather than against the clock, so the check keeps meaning the same thing
    tomorrow.
    """
    image = published_image()
    runs = admitted_runs()

    assert runs
    for run in runs:
        assert image.pushed_at.date() == run.record.observed_at.date(), (
            f"{run.run_id} ran on an image published on a different day, so it is not "
            "evidence that a freshly built image is accepted"
        )
        assert image.pushed_at <= run.record.observed_at, run.run_id


# ---------------------------------------------------------------------------------------
# Criterion 4 -- the digest admitted is the digest that ran
# ---------------------------------------------------------------------------------------


def test_the_container_that_ran_was_the_image_the_manifest_was_admitted_on() -> None:
    """Mutation: read the image out of the CloudFormation template instead.

    Evidencing this from the template would prove the template, which is the thing that used
    to select the image -- the digest a submitter declared was validated, gated admission
    through the ECR scan, and was written immutably into lineage while the container that
    actually ran was whatever the template said. The two coincided only because the
    exception file happened to contain those digests.

    So the comparison is against ``container.image`` in the scheduler's own description of
    the job, which is the only source that knows what was pulled.
    """
    ran = [run for run in admitted_runs() if run.record.container_image_digest is not None]

    assert ran, (
        "no committed run records the image its container was given, so nothing here "
        "establishes that the digest in lineage is the digest that ran"
    )
    for run in ran:
        assert run.record.container_image_digest == run.record.declared_image_digest, run.run_id
        assert run.record.image_that_ran_is_the_image_admitted, run.run_id


def test_the_job_definition_a_pilot_run_was_submitted_against_is_its_own() -> None:
    """Mutation: submit against a shared definition and read the image from there.

    A per-run definition is the mechanism that makes the digest selectable at all. A shared
    definition pins one image for every run, so a matching digest would be a coincidence
    maintained by the exception file rather than a property of the submission.
    """
    for run in admitted_runs():
        if run.record.job_definition_name is None:
            continue
        assert run.run_id in run.record.job_definition_name, (
            f"{run.run_id} was submitted against {run.record.job_definition_name}, which is not a "
            "definition registered for it"
        )


# ---------------------------------------------------------------------------------------
# Criterion 8 -- an accepted run tells the submitter where its results are
# ---------------------------------------------------------------------------------------


def test_a_run_that_succeeded_recorded_where_its_output_went() -> None:
    """Mutation: record the outcome and drop the prefixes.

    The submitter's question after a run is where the results are, and the result manifest
    is what the workflow's summary derives its answer from. A record with an outcome and no
    prefix answers the question the platform already knew.
    """
    succeeded = [run for run in admitted_runs() if run.record.result_outcome == "succeeded"]

    assert succeeded, "no committed run succeeded, so nothing here has results to find"
    for run in succeeded:
        assert run.record.output_prefixes, run.run_id
        for prefix in run.record.output_prefixes:
            assert run.run_id in prefix, run.run_id
            assert f"teams/{run.record.team}/" in prefix, run.run_id


def test_the_result_manifest_still_names_no_weights_and_biases_run() -> None:
    """Mutation: assert the W&B run is present, or stop recording the field.

    ``lifecycle_projection`` hardcodes ``wandb_run=None`` on every result manifest, so the
    one artifact a researcher actually wants is the one the platform does not link -- and
    that is true even of the pilot run whose command logged to W&B throughout. Recording it
    as a passing check would claim a link that is not written; recording nothing would lose
    the finding. It closes when lineage records the W&B run.
    """
    for run in admitted_runs():
        assert run.record.wandb_run is None, (
            f"{run.run_id} names a W&B run, which lifecycle_projection cannot yet write -- "
            "if this is failing, item 7.4 landed and this criterion should be reread"
        )


# ---------------------------------------------------------------------------------------
# Criterion 10 -- a member cannot merge a workflow change without a code-owner review
# ---------------------------------------------------------------------------------------


def test_the_default_branch_requires_a_review_from_somebody_who_owns_the_code() -> None:
    """Mutation: assert the review count alone.

    A required review that any member may give is not the control this phase's write grant
    rests on. Write access to this repository is merge access to five workflow files pinned
    by name in three IAM trust policies, and to the source and configuration that ship
    inside the released Lambda zips -- so the count and the code-owner rule are one control
    and neither half is worth anything alone.
    """
    protection = branch_protection()

    assert protection.branch == "main"
    assert protection.required_approving_review_count >= 1
    assert protection.require_code_owner_reviews


def test_the_control_binds_members_and_the_admins_may_still_bypass_it() -> None:
    """Mutation: assert ``enforce_admins``.

    The unqualified statement -- nobody may merge a workflow change without a code-owner
    review -- is false in this repository and asserting it would make the gate claim
    something untrue. ``enforce_admins`` stays false by decision, because turning it on
    makes every pull request the author writes wait on the one other code owner. The
    criterion is therefore about a *member*, and this test records both halves so a reader
    is not left to infer the second from the absence of an assertion.
    """
    protection = branch_protection()

    assert not protection.enforce_admins, (
        "enforce_admins was turned on, which is a stronger control than the criterion "
        "claims; the criterion should be widened rather than left understating the account"
    )
    assert protection.required_status_checks, (
        "a code-owner review with no required checks beside it lets a member merge a red "
        "branch, which is the same bypass by another route"
    )
    assert not protection.allow_force_pushes


def test_every_path_a_released_lambda_packages_is_owned_by_a_code_owner() -> None:
    """Mutation: cover the workflows and leave the validator's own source uncovered.

    ``tools/build_admission_lambda.py`` copies ``config/*.yaml`` and the whole
    ``src/edullm_platform`` tree into the validator zip, so a change to either decides
    whether a run is authorized. That makes them the same kind of path as a workflow file,
    and until write access was granted to somebody who did not build this platform they
    were not covered here.
    """
    owned = {
        line.split()[0]
        for line in CODEOWNERS.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    for required in (
        "/.github/workflows/**",
        "/src/edullm_platform/**",
        "/config/**",
        "/infra/**",
        "/tools/build_admission_lambda.py",
    ):
        assert required in owned, f"{required} ships inside a release and nobody owns it"
