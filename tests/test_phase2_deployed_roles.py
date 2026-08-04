"""The three roles the admission gate runs as, read from a capture somebody committed.

Every other test of these roles reads a CloudFormation template. A template is a claim
about the account rather than a description of it, and the stack that creates these roles
is applied from a laptop, so nothing in the suite could tell a template that had been
deployed from one that had not. This module is the half that closes that distance, and it
is the same machinery Phase 1, Phase 3 and the dataset validator already use:
``read_committed_role_captures`` over ``PHASE2_ROLE_TEMPLATES``, with the one recorded
tolerance in :mod:`edullm_platform.pending_amendments`.

**Why it did not exist until now, and what the absence cost.** The registry did exist --
``PHASE2_ROLE_TEMPLATES`` has been declared since Phase 2, and ``declared_role_templates``
merges it so a pending amendment may name one of these roles. What was missing was a tool
that walked it and a test that read the result, so these three were the only committed
roles in the repository never compared against the account.

On 2026-08-02 that gap was paid for. ``infra/iam/admission-service-roles.yaml`` enumerates
sixteen job queues on ``batch:SubmitJob``; the deploy that would have applied the last five
failed on IAM's inline-policy size limit and CloudFormation rolled it back. The template was
right, ``tests/test_phase3_infrastructure.py`` compared it against the queues the compute
templates create and passed in both directions, and five compute profiles were unsubmittable
for two days. Nothing was wrong with any document. The account had simply not received one
of them, and no test in this repository was looking.

So the check this module adds is not "does the template enumerate every queue" -- that one
already existed and was green throughout. It is "did the account ever receive the
enumeration", which is a different question and the one that was going unasked.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from edullm_platform.evidence import CAPTURE_SUFFIX, CaptureLoadVerdict, scan_for_secrets
from edullm_platform.pending_amendments import UNREACHABLE_COMPUTE_PROFILES, pending_for
from edullm_platform.phase1_capture import CaptureVerdict, read_committed_role_captures
from edullm_platform.phase2_evidence import PHASE2_ROLE_CAPTURE_DIR, PHASE2_ROLE_TEMPLATES

from tests.infrastructure_support import INFRA_ROOT, load_template

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CAPTURE_DIR = PROJECT_ROOT / PHASE2_ROLE_CAPTURE_DIR
STATES_ROLE = "sbsandbox-intern-edullm-admission-states"

#: Every template that creates a job queue. The same three
#: ``tests/test_phase3_infrastructure.py`` reads, and named here rather than imported from
#: that module for the reason it gives about its own tuple: a fourth compute template is one
#: edit, and a test that read only some of them would go green while half the seam was
#: broken.
COMPUTE_PATHS = (
    INFRA_ROOT / "batch-compute.yaml",
    INFRA_ROOT / "batch-compute-gpu.yaml",
    INFRA_ROOT / "batch-compute-gpu-shapes.yaml",
)


def captures() -> tuple:
    return read_committed_role_captures(
        PROJECT_ROOT, capture_dir=CAPTURE_DIR, role_templates=PHASE2_ROLE_TEMPLATES
    )


def queues_the_templates_create() -> set[str]:
    """Every job queue name the compute templates declare."""
    return {
        resource["Properties"]["JobQueueName"]
        for path in COMPUTE_PATHS
        for resource in load_template(path)["Resources"].values()
        if isinstance(resource, dict) and resource.get("Type") == "AWS::Batch::JobQueue"
    }


def queues_the_deployed_role_may_submit_to() -> set[str]:
    """Every queue the captured ``batch:SubmitJob`` grant actually names.

    Read out of the committed capture as JSON rather than through the evidence contract, for
    the reason ``tests/test_dataset_validator_role.py`` gives about the same read: this asks
    what the account held on the day it was captured, and a freshness window that refused to
    load an old capture would turn a question about a grant into a question about the clock.
    Freshness is asserted on its own below, where a failure says so.
    """
    captured = json.loads(
        (CAPTURE_DIR / f"{STATES_ROLE}{CAPTURE_SUFFIX}").read_text(encoding="utf-8")
    )
    return {
        resource.rsplit("/", 1)[-1]
        for policy in captured["inline_policies"]
        for statement in policy["statements"]
        if "batch:SubmitJob" in statement["action_match"]["actions"]
        for resource in statement["resource_match"]["resources"]
        if ":job-queue/" in resource
    }


def test_a_capture_is_committed_for_every_role_the_phase_2_registry_declares() -> None:
    """Mutation: delete a capture. Mutation: add a role to the registry and capture nothing.

    Driven off the registry rather than off the directory, so a capture somebody deleted is
    reported as a role nobody has looked at instead of shortening the answer. That is the
    direction that matters here: this whole module exists because an absent comparison reads
    exactly like a passing one.
    """
    found = captures()
    absent = [one.role_name for one in found if one.verdict is CaptureLoadVerdict.ABSENT]

    assert [capture.role_name for capture in found] == sorted(
        role_name for role_name, _template in PHASE2_ROLE_TEMPLATES
    )
    assert absent == []


def test_every_committed_phase_2_capture_is_inside_its_freshness_window() -> None:
    """This is the test that expires. When it goes red thirty days after the capture,
    nothing has gone wrong with the roles: the evidence has stopped being evidence, and the
    account is unobserved again until somebody re-runs the capture.
    """
    for capture in captures():
        assert capture.verdict is not CaptureLoadVerdict.STALE, capture.detail
        assert capture.expires_at is not None
        assert capture.expires_at > datetime.now(tz=UTC), capture.detail


def test_every_committed_phase_2_capture_matches_the_template_that_declares_it() -> None:
    """Mutation: widen a deployed role in the console. Mutation: amend a template and do
    not deploy it.

    Equality against what ``PENDING_AMENDMENTS`` records, in both directions, which is the
    same contract ``tests/test_phase1_deployed_roles.py`` holds its two roles to. A finding
    no record names fails whichever way it points, and so does a recorded finding the
    comparison has stopped reporting -- which is what a deployed amendment looks like and
    what forces the record to be deleted rather than left behind.
    """
    for capture in captures():
        pending = pending_for(capture.role_name)
        assert capture.report is not None, capture.detail
        assert capture.report.findings == (() if pending is None else pending.findings), (
            capture.detail
        )
        assert capture.verdict is (
            CaptureVerdict.OK if pending is None else CaptureVerdict.PENDING_DEPLOY
        ), capture.detail


def test_the_deployed_states_role_may_submit_to_every_queue_the_templates_create() -> None:
    """THE CHECK WHOSE ABSENCE LET FIVE COMPUTE PROFILES GO UNSUBMITTABLE FOR TWO DAYS.
    Mutation: promote a compute profile and deploy the queue without deploying the role.

    ``tests/test_phase3_infrastructure.py`` already compares the template's enumeration
    against the queues the compute templates create, in both directions, and it was green
    the whole time. It reads two documents. This one reads a document and the account, which
    is the only way to see a deploy that did not land.

    Stated in the domain's own terms rather than as a role diff, because the failure it
    describes is a researcher's. A queue in the compute stack that the admission role cannot
    submit to is a compute profile the submission form will offer, admission will accept, an
    approver will release -- and Batch will refuse with a 403 at the submit state.

    The tolerance runs through ``pending_for`` rather than through a list of its own, so
    there is one mechanism for "expected to differ" and it is the self-clearing one. Delete
    the amendment and this becomes a bare equality; leave it after the deploy and the
    equality below fails on the profiles that are no longer missing.
    """
    created = queues_the_templates_create()
    deployed = queues_the_deployed_role_may_submit_to()
    pending = pending_for(STATES_ROLE)
    expected_gap = (
        set()
        if pending is None
        else {f"sbsandbox-intern-edullm-{profile}" for profile in UNREACHABLE_COMPUTE_PROFILES}
    )

    assert created, "no compute template declares a queue, so the comparison would be vacuous"
    assert created - deployed == expected_gap
    # The other direction, and it is not symmetric with the one above: a pending amendment
    # can only leave the account behind the template, so a queue the deployed role reaches
    # that no template creates is never expected and nothing here may excuse it.
    assert deployed - created == set()


def test_no_committed_phase_2_capture_carries_an_account_id() -> None:
    """The contract refuses one on load, so this is the same claim made against the bytes on
    disk: what is committed here is reviewable by anybody, not merely loadable.
    """
    for role_name, _template in PHASE2_ROLE_TEMPLATES:
        text = (CAPTURE_DIR / f"{role_name}{CAPTURE_SUFFIX}").read_text(encoding="utf-8")
        assert scan_for_secrets(text) == text, role_name
