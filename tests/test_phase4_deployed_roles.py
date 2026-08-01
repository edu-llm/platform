"""The five roles Phase 4 adds, read from a capture rather than from the template.

Every other test of these roles reads a CloudFormation template, which is a claim about the
account rather than a description of it: the stacks under ``infra/iam/`` are applied from a
laptop and none of them is redeployed by CI. A capture closes that distance, and
``edullm_platform.phase4_capture.role_captures`` is what a test can ask about one without
credentials.

Three GPU roles and two GitHub Actions roles. The GPU trio is the Phase 3 trio's counterpart
on the second compute environment; the canceller stops a job and the nightly reader reads
what the scheduled checks ask about. All five are compared through the same machinery every
earlier phase uses, against a registry of their own, so a role this phase adds cannot fail an
earlier phase's capture.

**WHAT THESE CASES ESTABLISH AND WHAT THEY CANNOT, WHICH IS WORTH READING BEFORE THE FIRST
GREEN RUN.** A capture that matches its template says the account grants what this repository
says it grants. It does not say the grant can be exercised. A condition keyed on something
the action never puts in the request context is identical on both sides of the comparison and
unsatisfiable in the account, and IAM reports the refusal as an implicit deny --
indistinguishable from a grant nobody wrote. ``test_a_condition_no_request_context_can_satisfy
_is_invisible_to_the_comparison`` pins that blind spot as a fact rather than leaving it to be
rediscovered, and the case beside it reads the one condition on the canceller that a real
termination settled.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml

from edullm_platform.evidence import (
    CAPTURE_SUFFIX,
    FRESHNESS_WINDOW,
    CaptureLoadVerdict,
    scan_for_secrets,
)
from edullm_platform.pending_amendments import declared_role_templates, pending_for
from edullm_platform.phase1_capture import CaptureVerdict, CommittedRoleCapture
from edullm_platform.phase1_evidence import DeployedRoleEvidence
from edullm_platform.phase4_capture import ROLE_CAPTURE_DIR, role_captures
from edullm_platform.role_drift import (
    PHASE4_ROLE_TEMPLATES,
    DriftDirection,
    TemplateRole,
    compare_role_to_template,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
IAM_TEMPLATES = PROJECT_ROOT / "infra" / "iam"

CANCELLER_ROLE = "sbsandbox-intern-edullm-run-canceller"
NIGHTLY_READER_ROLE = "sbsandbox-intern-edullm-nightly-reader"
GPU_WORKLOAD_ROLE = "sbsandbox-intern-edullm-batch-gpu-workload"
GPU_EXECUTION_ROLE = "sbsandbox-intern-edullm-batch-gpu-execution"
GPU_INSTANCE_ROLE = "sbsandbox-intern-edullm-batch-gpu-instance"

OUTPUTS_BUCKET_ARN = "arn:aws:s3:::sbsandbox-intern-edullm-outputs"
RUN_PREFIX_ARN = f"{OUTPUTS_BUCKET_ARN}/teams/*/runs/*"
#: The dataset owner's sealed bucket, which a training run reads its corpus out of.
DATASET_BUCKET_ARN = "arn:aws:s3:::edullm-data"

#: The tag Batch does put in the request context for a job, and the value shape that says the
#: job is one this platform submitted. Both halves matter: a key alone would let a tag
#: somebody else writes under the same name authorise a termination.
RUN_ID_TAG_KEY = "aws:ResourceTag/edullm:run-id"

#: What the canceller must not be able to do, whatever the template says. Service prefixes
#: rather than particular calls, because the claim is about whole services: a role that can
#: cancel and submit can replace somebody's run with its own.
CANCELLER_FORBIDDEN_SERVICES = ("s3", "states", "iam", "ecr", "secretsmanager")

#: The verbs that change something. Read as a prefix test rather than a list, because the
#: nightly reader's claim is that it holds none of them at all and an enumeration would only
#: ever be as complete as whoever last edited it.
WRITE_VERBS = ("Put", "Delete", "Create", "Update", "Write", "Terminate", "Submit", "Tag")


@pytest.fixture(scope="module")
def captures() -> tuple[CommittedRoleCapture, ...]:
    return role_captures()


def one(captures: tuple[CommittedRoleCapture, ...], role_name: str) -> DeployedRoleEvidence:
    """The record for one role, refusing a capture that did not load.

    A capture that is absent or stale carries no evidence, and a case that read ``None`` as
    "no grants" would pass every assertion below on a role nobody looked at.
    """
    capture = next(one for one in captures if one.role_name == role_name)
    assert capture.evidence is not None, capture.detail
    return capture.evidence


def granted(evidence: DeployedRoleEvidence) -> tuple[str, ...]:
    """Every action the role's inline policies allow, refusing the negated spelling.

    ``NotAction`` with ``Allow`` permits everything that is *not* listed, so a reader that
    collected its list would report the narrowest-looking actions on the widest possible
    grant.
    """
    actions: list[str] = []
    for policy in evidence.inline_policies:
        for statement in policy.statements:
            assert statement.effect == "Allow", (evidence.role_name, policy.policy_name)
            assert statement.action_match.element == "Action", evidence.role_name
            actions.extend(statement.action_match.actions)
    return tuple(sorted(set(actions)))


def committed_payload(role_name: str) -> dict[str, Any]:
    path = ROLE_CAPTURE_DIR / f"{role_name}{CAPTURE_SUFFIX}"
    payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return payload


def write_capture(directory: Path, role_name: str, payload: dict[str, Any]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{role_name}{CAPTURE_SUFFIX}").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


# --------------------------------------------------------------------------------------
# Which roles are compared to anything at all
# --------------------------------------------------------------------------------------


def test_every_role_a_committed_template_declares_is_in_some_registry() -> None:
    """Mutation: add a role to a template under ``infra/iam/`` and register it nowhere.

    A registry is what the capture tool walks and what the drift report iterates over, so a
    role that is committed and unregistered is deployed, live, and compared to nothing. That
    is the gap this whole module exists to close, and it closes for one phase at a time
    unless something reads across all of them -- which is what this does.

    Both directions. A registry entry naming a role no template declares would fail a capture
    on ``iam get-role`` rather than reporting anything a reader could act on.
    """
    declared: dict[str, str] = {}
    for path in sorted(IAM_TEMPLATES.glob("*.yaml")):
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        for resource in (document.get("Resources") or {}).values():
            if isinstance(resource, dict) and resource.get("Type") == "AWS::IAM::Role":
                declared[resource["Properties"]["RoleName"]] = str(
                    path.relative_to(PROJECT_ROOT)
                )

    registered = declared_role_templates()

    assert declared, "a glob that matched nothing would make every assertion below vacuous"
    assert sorted(registered) == sorted(declared)
    # And each is registered against the template that actually declares it, so a capture
    # cannot compare a role to a document describing a different one.
    assert registered == declared


def test_this_phases_registry_names_the_five_roles_it_adds() -> None:
    assert dict(PHASE4_ROLE_TEMPLATES) == {
        GPU_EXECUTION_ROLE: "infra/iam/batch-gpu-roles.yaml",
        GPU_INSTANCE_ROLE: "infra/iam/batch-gpu-roles.yaml",
        GPU_WORKLOAD_ROLE: "infra/iam/batch-gpu-roles.yaml",
        NIGHTLY_READER_ROLE: "infra/iam/nightly-reader-role.yaml",
        CANCELLER_ROLE: "infra/iam/run-canceller-role.yaml",
    }


# --------------------------------------------------------------------------------------
# What the committed captures say
# --------------------------------------------------------------------------------------


def test_a_capture_is_committed_for_every_role_this_phase_declares(
    captures: tuple[CommittedRoleCapture, ...],
) -> None:
    # Read off the registry rather than off the directory: a capture somebody deleted has to
    # show up as a role nobody has looked at, not as a shorter list.
    assert [capture.role_name for capture in captures] == sorted(
        role_name for role_name, _template in PHASE4_ROLE_TEMPLATES
    )
    assert [one.role_name for one in captures if one.verdict is CaptureLoadVerdict.ABSENT] == []


def test_every_committed_capture_is_inside_its_freshness_window(
    captures: tuple[CommittedRoleCapture, ...],
) -> None:
    # This is the case that expires. When it goes red thirty days after the capture, nothing
    # has gone wrong with the roles: the evidence has stopped being evidence, and the account
    # is an unchecked claim again until somebody looks.
    for capture in captures:
        assert capture.verdict is not CaptureLoadVerdict.STALE, capture.detail
        assert capture.expires_at is not None
        assert capture.expires_at > datetime.now(tz=UTC), capture.detail


def test_every_committed_capture_matches_the_template_that_declares_it(
    captures: tuple[CommittedRoleCapture, ...],
) -> None:
    # Equality against what is recorded, in both directions. A finding no pending amendment
    # names fails here whichever way it points, and so does a recorded finding the comparison
    # has stopped reporting -- which is what a deployed amendment looks like.
    for capture in captures:
        pending = pending_for(capture.role_name)
        expected = () if pending is None else pending.findings
        assert capture.report is not None, capture.detail
        assert capture.report.findings == expected, capture.detail
        assert capture.verdict is (
            CaptureVerdict.OK if pending is None else CaptureVerdict.PENDING_DEPLOY
        ), capture.detail


def test_no_committed_capture_carries_an_account_id() -> None:
    # The contract refuses one on load, so this is the same claim made against the bytes on
    # disk: what is committed here is reviewable by anybody, not merely loadable.
    for role_name, _template in PHASE4_ROLE_TEMPLATES:
        text = (ROLE_CAPTURE_DIR / f"{role_name}{CAPTURE_SUFFIX}").read_text(encoding="utf-8")
        assert scan_for_secrets(text) == text, role_name


# --------------------------------------------------------------------------------------
# What the account grants, read from the capture rather than from the template
# --------------------------------------------------------------------------------------


def test_the_deployed_canceller_stops_jobs_and_reaches_nothing_else(
    captures: tuple[CommittedRoleCapture, ...],
) -> None:
    """Mutation: attach ``batch:SubmitJob`` to the deployed role in the console.

    The trade this role is built on is that the authorisation lives in a workflow file, so
    what bounds it is the set of things it can do at all. Every other case about that set
    reads the template; this reads what IAM returned.
    """
    evidence = one(captures, CANCELLER_ROLE)
    actions = granted(evidence)

    assert actions == ("batch:DescribeJobs", "batch:ListJobs", "batch:TerminateJob")
    assert not [
        action for action in actions if action.split(":", 1)[0] in CANCELLER_FORBIDDEN_SERVICES
    ]
    assert evidence.attached_managed_policies == ()
    assert evidence.permissions_boundary_policy_name == "InternSandboxBoundary"


def test_the_deployed_canceller_is_conditioned_on_a_key_terminate_job_supplies(
    captures: tuple[CommittedRoleCapture, ...],
) -> None:
    """Mutation: scope the termination by ``batch:JobQueue`` instead.

    ``TerminateJob`` takes a job id and a reason, so ``batch:JobQueue`` is never in its
    request context and a condition on it can never match. A role written that way describes
    and lists and cannot stop anything, and says so as an implicit deny -- which reads as a
    missing grant. ``aws:ResourceTag`` is a key Batch does put on a job, which is why the
    condition is written against it, and only a real termination settled that.

    The value is asserted alongside the key. A condition on the key alone would let a tag
    somebody else writes under the same name authorise a termination on their job.
    """
    terminate = [
        statement
        for statement in one(captures, CANCELLER_ROLE).inline_policies[0].statements
        if statement.action_match.actions == ("batch:TerminateJob",)
    ]

    assert len(terminate) == 1
    conditions = terminate[0].conditions
    assert [(one.operator, one.condition_key, one.values) for one in conditions] == [
        ("StringLike", RUN_ID_TAG_KEY, ("run_*",))
    ]


def test_the_deployed_nightly_reader_can_change_nothing_it_reads(
    captures: tuple[CommittedRoleCapture, ...],
) -> None:
    """Mutation: grant ``s3:PutObject`` on the outputs bucket.

    A check that can change what it is checking can manufacture its own all-clear, which is
    the one property of this role worth reading off the account rather than off the file.
    """
    actions = granted(one(captures, NIGHTLY_READER_ROLE))

    assert actions
    assert not [action for action in actions if any(verb in action for verb in WRITE_VERBS)]
    assert not [action for action in actions if action.endswith(":*") or action == "*"]


def test_the_deployed_gpu_workload_reaches_the_outputs_bucket_only_under_a_run_prefix(
    captures: tuple[CommittedRoleCapture, ...],
) -> None:
    """Mutation: widen the object grant to the whole bucket.

    ``fixtures/evidence/phase-4/workload-role-scope.sanitized.json`` records the same role
    reduced to the prefixes the isolation checks ask about. This reads the statements whole,
    which is the half that reduction cannot show: a grant on a bucket ARN with no key, or a
    second statement on another bucket, is invisible to a record of prefix patterns.

    The dataset bucket is named here rather than excluded, because a role that reads a
    published corpus is supposed to reach it and a case that ignored other buckets would
    ignore the next one too.
    """
    evidence = one(captures, GPU_WORKLOAD_ROLE)
    by_resource = {
        statement.resource_match.resources: set(statement.action_match.actions)
        for policy in evidence.inline_policies
        for statement in policy.statements
    }

    assert set(by_resource) == {
        (RUN_PREFIX_ARN,),
        (OUTPUTS_BUCKET_ARN,),
        (f"{DATASET_BUCKET_ARN}/*",),
        (DATASET_BUCKET_ARN,),
    }
    assert "s3:DeleteObject" not in granted(evidence)
    # Each bucket's own ARN grants listing and nothing else. An object action on one would be
    # a grant over every key in it wearing the shape of a bucket-level grant.
    assert by_resource[(OUTPUTS_BUCKET_ARN,)] == {"s3:ListBucket"}
    assert by_resource[(DATASET_BUCKET_ARN,)] == {"s3:ListBucket"}
    # The corpus is read and never written, which is what makes it a published input.
    assert by_resource[(f"{DATASET_BUCKET_ARN}/*",)] == {"s3:GetObject"}


# --------------------------------------------------------------------------------------
# What the comparison cannot see
# --------------------------------------------------------------------------------------


def unsatisfiable_role(role_name: str) -> TemplateRole:
    """A role whose one grant is conditioned on a key its action never supplies.

    The shape the canceller carried until a real termination disproved it: ``TerminateJob``
    scoped by ``batch:JobQueue``, which Batch does not put in the request context.
    """
    return TemplateRole.model_validate(
        {
            "role_name": role_name,
            "permissions_boundary_policy_name": "InternSandboxBoundary",
            "max_session_duration_seconds": 3600,
            "trust_policy_version": "2012-10-17",
            "trust_statements": [
                {
                    "sid": None,
                    "effect": "Allow",
                    "action_match": {"element": "Action", "actions": ["sts:AssumeRole"]},
                    "principal_match": {
                        "element": "Principal",
                        "principals": [
                            {"principal_type": "Service", "identifier": "batch.amazonaws.com"}
                        ],
                    },
                    "conditions": [],
                }
            ],
            "inline_policies": [
                {
                    "policy_name": "stop-a-job-on-a-condition-nothing-can-satisfy",
                    "policy_version": "2012-10-17",
                    "statements": [
                        {
                            "sid": None,
                            "effect": "Allow",
                            "action_match": {
                                "element": "Action",
                                "actions": ["batch:TerminateJob"],
                            },
                            "resource_match": {
                                "element": "Resource",
                                "resources": ["arn:aws:batch:us-east-1:<aws-account-id>:job/*"],
                            },
                            "conditions": [
                                {
                                    "operator": "ArnEquals",
                                    "condition_key": "batch:JobQueue",
                                    "values": [
                                        "arn:aws:batch:us-east-1:<aws-account-id>:job-queue/q"
                                    ],
                                }
                            ],
                        }
                    ],
                }
            ],
            "attached_managed_policies": [],
        }
    )


def test_a_condition_no_request_context_can_satisfy_is_invisible_to_the_comparison() -> None:
    """Not a defect to fix here, and the reason it is pinned rather than left implicit.

    The comparison answers one question -- does the account grant what the committed template
    says it grants -- and answers it correctly for a grant that cannot work: the condition is
    in both documents and identical in both, so there is nothing to report. A reader who took
    a clean drift report as "this role can do its job" would be reading a claim nobody made.

    Nothing this module could assert would change that. Whether an action supplies a
    condition key is a fact about the service, not about either document, and the only thing
    that settles it is calling the action. What this case buys is that the limit is written
    down where somebody reading the registry will meet it.
    """
    template = unsatisfiable_role(CANCELLER_ROLE)
    deployed = DeployedRoleEvidence.model_validate(
        template.model_dump(mode="json")
        | {
            "source": "aws",
            "environment": "sandbox",
            "status": "ok",
            "observed_at": datetime.now(tz=UTC).replace(microsecond=0).isoformat(),
        }
    )

    report = compare_role_to_template(
        deployed,
        template,
        template_path="infra/iam/run-canceller-role.yaml",
        partition="aws",
        region="us-east-1",
    )

    assert report.findings == ()
    assert report.matches is True


# --------------------------------------------------------------------------------------
# What happens when it stops being true
# --------------------------------------------------------------------------------------


def test_a_role_widened_after_the_template_was_written_does_not_hold(tmp_path: Path) -> None:
    payload = committed_payload(CANCELLER_ROLE)
    payload["inline_policies"][0]["statements"][0]["action_match"]["actions"].append(
        "batch:SubmitJob"
    )
    write_capture(tmp_path, CANCELLER_ROLE, payload)

    capture = next(
        one for one in role_captures(tmp_path) if one.role_name == CANCELLER_ROLE
    )

    assert capture.verdict is CaptureVerdict.DRIFTED
    assert not capture.holds
    assert capture.report is not None
    assert DriftDirection.WIDER in {finding.direction for finding in capture.report.findings}
    assert "batch:SubmitJob" in capture.detail


def test_a_capture_that_has_expired_stops_establishing_anything(tmp_path: Path) -> None:
    # Aged here rather than waited for. Past the window the record does not load at all, so
    # nothing downstream can read a stale role as a role.
    expired = (datetime.now(tz=UTC) - FRESHNESS_WINDOW - timedelta(minutes=1)).isoformat()
    for role_name, _template in PHASE4_ROLE_TEMPLATES:
        write_capture(tmp_path, role_name, committed_payload(role_name) | {"observed_at": expired})

    for capture in role_captures(tmp_path):
        assert capture.verdict is CaptureLoadVerdict.STALE, capture.detail
        assert not capture.holds
        assert capture.evidence is None
        assert capture.report is None


def test_a_directory_with_no_captures_reports_every_role_as_absent(tmp_path: Path) -> None:
    captures = role_captures(tmp_path)

    assert [capture.verdict for capture in captures] == [CaptureLoadVerdict.ABSENT] * len(
        PHASE4_ROLE_TEMPLATES
    )
    assert all(capture.template_path is not None for capture in captures)
