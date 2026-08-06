"""A committed change the account has not caught up with yet.

Two kinds of record live here and they share one mechanism, which is the reason they share
a module. :class:`PendingAmendment` is an IAM template amended in a commit and not yet
applied to the account; :class:`PendingRelease` is a packaged module changed in a commit
and not yet uploaded as a Lambda zip. Both describe the same window -- between the commit
that changes what the repository says and the act that changes what the account does -- and
both have to survive the same objection, that naming a difference is a way of excusing it.
The answers are the same in both cases and are set out below: a record says *what* it is
waiting on precisely enough to stop describing anything the moment that thing happens, says
why and what ends it, and fails loudly rather than quietly when it stops fitting.

The paragraphs that follow are about the IAM case, which came first and is where the
reasoning was worked out. :class:`PendingRelease` restates only what differs.

Every role this repository compares against a template is created or amended from a
laptop, because ``infra/README.md`` exists to keep role creation out of a pipeline. So a
template change and the deploy that realises it are two acts with a window between them,
and inside that window the comparison in :mod:`edullm_platform.role_drift` reports the
deployed role as ``NARROWER`` than the template. It is right to: the committed template
has stopped describing the account.

**Why this is a library module rather than a note in a test.** It was a note in a test,
and the cost showed up immediately. A test module knew which difference was expected;
:mod:`edullm_platform.phase1_capture` did not, so it reported the role as ``DRIFTED`` --
the same verdict a role widened in the console gets -- and every consumer downstream had
to re-derive "is this the expected one?" from whatever it happened to have. The proof
generator did not re-derive it at all: it treated *any* capture that stopped holding as
the pending case, so an expired capture and an undeployed amendment produced the same
skip. An expiry that reads as a deploy nobody has run yet is exactly the kind of quiet
substitution the freshness window exists to prevent.

So the record lives here, the capture reader consults it, and the state gets its own
verdict: :attr:`~edullm_platform.phase1_capture.CaptureVerdict.PENDING_DEPLOY`.

**Naming the state is not the same as excusing it.** A capture waiting on a deploy still
does not hold, the checks resting on it are still not satisfied, and the release tripwire
still refuses to write a bundle. What changes is that the refusal can say which of the two
things happened, and that a reader downstream can tell an expected difference from an
unexplained one without guessing.

**Every record is self-clearing, and that is the whole design.** An entry carries the two
things a ``DEFERRED`` criterion carries -- a :attr:`~PendingAmendment.reason` a reader can
weigh and a :attr:`~PendingAmendment.cleared_by` that says what ends it -- plus a third a
criterion does not: the :attr:`~PendingAmendment.findings` themselves, compared for
equality. So the record fails the moment the account stops differing in exactly this way,
in either direction. It cannot outlive the deploy it is waiting for, and it cannot quietly
absorb a second difference that arrives while it is open.

**Every finding must be ``NARROWER``.** An undeployed template change can only leave the
account behind the template. A deployed role that grants something its template does not
is a security finding, and nothing pending explains one.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from typing import Final

from edullm_platform.phase2_evidence import PHASE2_ROLE_TEMPLATES
from edullm_platform.role_drift import (
    COMMITTED_ROLE_TEMPLATES,
    PHASE3_ROLE_TEMPLATES,
    RESEARCHER_ROLE_TEMPLATES,
    DriftDirection,
    RoleDriftFinding,
)

__all__ = [
    "PENDING_AMENDMENTS",
    "PENDING_RELEASES",
    "RELEASABLE_FUNCTIONS",
    "RELEASE_COMMAND",
    "RELEASE_WINDOW",
    "PendingAmendment",
    "PendingAmendmentError",
    "PendingRelease",
    "PendingReleaseError",
    "ReleasableFunction",
    "ReleaseComparison",
    "ReleaseVerdict",
    "compare_release",
    "declared_role_templates",
    "one_record_per_function",
    "pending_amendments",
    "pending_for",
    "pending_release_for",
    "pending_releases",
    "releases_beyond_their_window",
]

DEPLOYER_ROLE_NAME: Final = "sbsandbox-intern-edullm-infra-deployer"


class PendingAmendmentError(ValueError):
    """A recorded pending amendment is not something a reader could act on."""


def declared_role_templates() -> dict[str, str]:
    """Every role some committed template declares, across the registries merged here.

    The registries stay separate where they are defined, because a Phase 3 role drifting
    must not fail a Phase 1 capture. They are merged only here, and only to answer one
    question: is there a template that will ever compare this role? A pending amendment for
    a role nothing compares would never clear, because nothing would ever report the
    findings it is waiting to stop seeing.

    ``PHASE5_ROLE_TEMPLATES`` and ``DATASET_VALIDATOR_ROLE_TEMPLATES`` are absent and were
    absent before ``RESEARCHER_ROLE_TEMPLATES`` was added. That is a gap rather than a
    decision: an amendment naming the image resolver or the dataset validator would be
    refused here even though both are captured and compared. It is left alone deliberately,
    because widening this is a change with its own reviewer and this one is about a role
    being added.
    """
    return {
        **dict(COMMITTED_ROLE_TEMPLATES),
        **dict(PHASE2_ROLE_TEMPLATES),
        **dict(PHASE3_ROLE_TEMPLATES),
        **dict(RESEARCHER_ROLE_TEMPLATES),
    }


@dataclass(frozen=True)
class PendingAmendment:
    """A template amendment that is committed and has not been applied to the account."""

    role_name: str
    reason: str
    cleared_by: str
    findings: tuple[RoleDriftFinding, ...]

    def __post_init__(self) -> None:
        if not self.findings:
            raise self._fail(
                "records no findings; a pending amendment that expects no difference is "
                "a record with nothing to clear"
            )
        for name in ("reason", "cleared_by"):
            if not getattr(self, name).strip():
                raise self._fail(f"does not say {name.replace('_', ' ')}")
        ahead = [
            finding for finding in self.findings if finding.direction is not DriftDirection.NARROWER
        ]
        if ahead:
            raise self._fail(
                "records a finding that is not narrower: "
                + ", ".join(f"{one.direction.value} at {one.element}" for one in ahead)
                + ". An undeployed template change leaves the account behind the "
                "template; a role that grants more than its template is a security "
                "finding and no pending deploy explains it"
            )

    def _fail(self, problem: str) -> PendingAmendmentError:
        return PendingAmendmentError(f"pending amendment for {self.role_name}: {problem}")

    def explains(self, findings: Sequence[RoleDriftFinding]) -> bool:
        """Whether these are exactly the differences this record is waiting on.

        Equality rather than containment, in both directions. Containment would let a
        second difference arrive under cover of the first, and would go on reading as
        explained after the deploy removed only part of what is recorded.
        """
        return tuple(findings) == self.findings


def pending_amendments() -> tuple[PendingAmendment, ...]:
    """Every committed template amendment the account has not caught up with yet."""
    # Empty, which is the state this registry is meant to spend most of its life in. An
    # entry lives here only between a template amendment being committed and the laptop
    # deploy that realises it. Four have been removed so far: the Phase 2 deployer
    # amendment and the Phase 3 one -- a third job_workflow_ref for deploy-phase3-batch.yml
    # and the deploy-phase3-batch-stacks inline policy -- both on 2026-07-27 when
    # sbsandbox-intern-edullm-infra-deployer-iam was applied; the
    # sbsandbox-intern-edullm-batch-workload `read-the-dataset-airlock` policy on
    # 2026-08-04 when sbsandbox-intern-edullm-phase3-batch-iam was applied and the
    # re-capture reported no findings on any of the four Phase 3 roles; and the
    # sbsandbox-intern-edullm-admission-states queue enumeration later the same day.
    #
    # Removal rather than exemption is the rule. The findings are compared for equality,
    # so a record left here after its deploy fails rather than lingering, and nothing in
    # this module offers a way to keep one that no longer describes a difference.
    #
    # THE FOURTH REMOVAL IS WORTH READING BEFORE THE NEXT RECORD IS WRITTEN, BECAUSE IT WAS
    # NOT THE ORDINARY CASE THIS MODULE WAS BUILT FOR. The other three described a template
    # amendment waiting on somebody to find a laptop. That one described a template IAM
    # refused to store: rendered with the account's own partition, region and id,
    # run-admission-workflow came to 10599 bytes against a 10240 cap on the aggregate of a
    # role's inline policies, so the 2026-08-02 deploy failed with ServiceLimitExceeded,
    # the stack sat in UPDATE_ROLLBACK_COMPLETE, and re-running the deploy reproduced the
    # rollback. Five advertised compute profiles were unsubmittable for two days. What
    # cleared it was a change to the template -- collapsing each paired `-run` and `-run:*`
    # job-definition ARN into one `-run*`, which an IAM wildcard covers because it matches
    # ':' like any other character -- and the deploy of 2026-08-04, after which the
    # re-capture reported no findings on any of the three Phase 2 roles.
    #
    # A record whose `cleared_by` needs a code change rather than a deploy is a legitimate
    # use of this registry and reads exactly like the ordinary one from every consumer's
    # side, so say which it is in the text. The lasting fix for that particular gap is not
    # here: tests/test_phase2_infrastructure.py now measures the rendered policy against
    # IAM's cap, so a document the account will refuse fails before anybody deploys it.
    #
    # ONE RECORD FOR THIS ROLE AND NOT TWO, BECAUSE TWO PLANS AMENDED IT IN THE SAME WEEK.
    # The expiry janitor and the notifier each add a Lambda to a stack CI deploys, and
    # lambda:CreateFunction takes a role ARN the caller must be allowed to pass, so both
    # widened the same iam:PassRole statements; the janitor also needs the EventBridge
    # Scheduler verbs. This registry is keyed by role, and the drift a capture reports is the
    # sum of both, so splitting it into two records would describe an account state that
    # never exists. Applying the deployer stack once clears all of it.
    #
    # A THIRD PLAN JOINED THE SAME RECORD ON 2026-08-05 AND THE ARGUMENT DID NOT CHANGE.
    # The working tier's bucket is granted to the deployer by its exact name, so the same
    # one application of the deployer stack clears this alongside the other two. Joining
    # rather than adding a fourth record is not a preference: `explains` compares the
    # findings for equality, so two records for one role would each describe a difference
    # the account never has on its own, and whichever was read first would decide which
    # half counted as expected.
    #
    # WHY THE NEW FINDING IS A WHOLE STATEMENT RATHER THAN A WIDER RESOURCE LIST, WHICH IS
    # THE PART WORTH READING BEFORE THE NEXT S3 GRANT IS WRITTEN. Adding the bucket to the
    # existing prefix-scoped S3 statement would have been three fewer lines, and it would
    # have granted the deployer s3:DeleteBucket and s3:PutBucketPolicy over the working
    # tier as well. Splitting that statement instead -- sharing its read half between the
    # prefix and the exact name -- was measured and refused for a mechanical reason: a
    # deployed statement that becomes two template statements pairs against the larger of
    # them and reports the actions left behind as WIDER, and __post_init__ refuses a
    # finding that is not NARROWER. A record cannot describe that window at all, which is
    # the registry saying that the account should never be behind a template in that shape.
    amendments: tuple[PendingAmendment, ...] = (
        PendingAmendment(
            role_name="sbsandbox-intern-edullm-ecr-publisher",
            reason=(
                "Registering edullm-p1 widens this role in the three places a registration "
                "always widens it: the repository_id the trust policy accepts, the OIDC "
                "subject it matches, and the ECR repository ARN the inline policy may push "
                "to. All three are template edits, and this is an IAM stack, so the account "
                "catches up from a laptop rather than from CI. Until it does, a build in "
                "edullm-p1 presents a repository id the trust policy does not list and dies "
                "at AssumeRole with a message that reads like a broken role ARN. Nothing "
                "else is waiting on it: the ECR repository itself is a CloudFormation "
                "resource that deploy-phase1-ecr.yml creates when this merges."
            ),
            cleared_by=(
                "aws cloudformation deploy --stack-name "
                "sbsandbox-intern-edullm-ecr-publisher-iam --template-file "
                "infra/iam/ecr-publisher-role.yaml --capabilities CAPABILITY_NAMED_IAM "
                "--no-fail-on-empty-changeset --profile sbsandbox --region us-east-1, then "
                "tools/capture_phase1_evidence.py --target roles, then delete this record"
            ),
            findings=(
                RoleDriftFinding(
                    direction=DriftDirection.NARROWER,
                    element="trust policy statement 1 conditions",
                    detail=(
                        "StringEquals token.actions.githubusercontent.com:repository_id "
                        "does not accept values the template does: 1314176548"
                    ),
                ),
                RoleDriftFinding(
                    direction=DriftDirection.NARROWER,
                    element="trust policy statement 1 conditions",
                    detail=(
                        "StringLike token.actions.githubusercontent.com:sub does not "
                        "accept values the template does: "
                        "repo:edu-llm@306859726/edullm-p1@1314176548:ref:refs/heads/*"
                    ),
                ),
                RoleDriftFinding(
                    direction=DriftDirection.NARROWER,
                    element="inline policy 'publish-research-images' statement 2 resources",
                    detail=(
                        "the template declares resources the deployed role does not: "
                        "arn:<partition>:ecr:<region>:<account>:repository/"
                        "sbsandbox-intern-edullm-p1"
                    ),
                ),
            ),
        ),
        PendingAmendment(
            role_name="sbsandbox-intern-edullm-batch-execution",
            reason=(
                "The ecr:BatchGetImage grant that lets the container start. Same "
                "registration and same omission as the admission states record below: "
                "tools/register_repository.py writes infra/ecr-repositories.yaml and "
                "infra/iam/ecr-publisher-role.yaml and leaves every other file that "
                "enumerates one ARN per submittable repository alone. Until the stack is "
                "applied a job naming edullm-p1 is placed, gets its node, and then cannot "
                "pull the image, which Batch reports as a CannotPullContainerError naming "
                "the image rather than the grant."
            ),
            cleared_by=(
                "The deploy-phase3-batch.yml run that fires on merge to main, then "
                "tools/capture_phase3_evidence.py, then delete this record. CI applies the "
                "Phase 3 stacks, so this needs a workflow run rather than a person."
            ),
            findings=(
                RoleDriftFinding(
                    direction=DriftDirection.NARROWER,
                    element=(
                        "inline policy 'pull-the-image-and-open-the-log-stream' "
                        "statement 2 resources"
                    ),
                    detail=(
                        "the template declares resources the deployed role does not: "
                        "arn:<partition>:ecr:<region>:<account>:repository/"
                        "sbsandbox-intern-edullm-p1"
                    ),
                ),
            ),
        ),
        PendingAmendment(
            role_name="sbsandbox-intern-edullm-batch-instance",
            reason=(
                "The instance-side half of the same pull. The execution role authorises the "
                "pull and this role is what the ECS agent on the node presents, so both have "
                "to name the repository or the image does not arrive. One registration, one "
                "deploy, two records, because the findings are compared per role."
            ),
            cleared_by=(
                "The deploy-phase3-batch.yml run that fires on merge to main, then "
                "tools/capture_phase3_evidence.py, then delete this record."
            ),
            findings=(
                RoleDriftFinding(
                    direction=DriftDirection.NARROWER,
                    element=(
                        "inline policy 'join-the-batch-managed-ecs-cluster' "
                        "statement 3 resources"
                    ),
                    detail=(
                        "the template declares resources the deployed role does not: "
                        "arn:<partition>:ecr:<region>:<account>:repository/"
                        "sbsandbox-intern-edullm-p1"
                    ),
                ),
            ),
        ),
        PendingAmendment(
            role_name="sbsandbox-intern-edullm-admission-states",
            reason=(
                "The other half of registering edullm-p1, and the half tools/"
                "register_repository.py does not write. That tool amends "
                "infra/ecr-repositories.yaml and infra/iam/ecr-publisher-role.yaml and "
                "nothing else, so the five places that enumerate one ARN per submittable "
                "repository were left behind: this role's ecr:DescribeImageScanFindings, "
                "and the four ecr:BatchGetImage grants in infra/iam/batch-roles.yaml and "
                "infra/iam/batch-gpu-roles.yaml. All five are amended here. Only this one "
                "reports drift, because it is the only one of the five whose role has a "
                "committed capture. Until the stack is applied, a submission naming "
                "edullm-p1 is refused at the scan-findings read with a message about the "
                "image rather than about the grant."
            ),
            cleared_by=(
                "The deploy-phase2-admission.yml run that fires on merge to main, then "
                "tools/capture_phase2_evidence.py, then delete this record. Unlike the "
                "publisher role above this needs no laptop: the Phase 2 stacks are applied "
                "by CI, so the window is one workflow run rather than one person."
            ),
            findings=(
                RoleDriftFinding(
                    direction=DriftDirection.NARROWER,
                    element="inline policy 'run-admission-workflow' statement 7 resources",
                    detail=(
                        "the template declares resources the deployed role does not: "
                        "arn:<partition>:ecr:<region>:<account>:repository/"
                        "sbsandbox-intern-edullm-p1"
                    ),
                ),
            ),
        ),
        PendingAmendment(
            role_name=DEPLOYER_ROLE_NAME,
            reason=(
                "Two stacks deployed by deploy-phase3-batch.yml need grants this role does "
                "not hold yet. The expiry janitor needs iam:PassRole on its two roles and the "
                "EventBridge Scheduler verbs, because the schedule is a schedule rather than "
                "a rule: a rule targeting a Lambda needs an AWS::Lambda::Permission and "
                "therefore lambda:AddPermission, which this role withholds deliberately -- "
                "infra/batch-events.yaml met the same fork and recorded the rule, a "
                "capability added rather than a restriction removed. The notifier needs "
                "iam:PassRole on sbsandbox-intern-edullm-notifier-lambda for the same "
                "CreateFunction reason. All of it is committed and the account has not caught "
                "up, because this stack is applied from a laptop."
                "\n\n"
                "A third stack joined them. infra/scratch-bucket.yaml creates edullm-scratch, and "
                "every S3 grant this role held was scoped to arn:aws:s3:::sbsandbox-intern-"
                "edullm-*, which that name does not match, so the working tier was a stack "
                "only a laptop could apply. It is granted by its exact name now rather than "
                "by widening that scope to edullm-*, because a wildcard there would bring "
                "edullm-data and edullm-landing inside a pipeline's reach and this role holds "
                "s3:PutBucketPolicy. The actions are read off what that template actually "
                "sets: CreateBucket, PutLifecycleConfiguration, PutBucketPublicAccessBlock "
                "and PutEncryptionConfiguration, with the configuration reads the "
                "AWS::S3::Bucket handler makes on every Read, and no s3:DeleteBucket because "
                "the template retains the bucket on delete and on replace."
            ),
            cleared_by=(
                "Applying sbsandbox-intern-edullm-notifier-iam and then "
                "sbsandbox-intern-edullm-infra-deployer-iam from a laptop with an SSO "
                "session, per infra/README.md under 'The expiry janitor's stacks' and 'The "
                "notifier', and re-taking the Phase 1 role capture. Until it is applied the "
                "CI deploys of sbsandbox-intern-edullm-janitor and "
                "sbsandbox-intern-edullm-notifier are refused on CreateFunction, and the "
                "refusal reads like a broken template rather than a missing grant; the CI "
                "deploy of sbsandbox-intern-edullm-scratch is refused on CreateBucket, which "
                "reads the same way. That apply needs --s3-bucket "
                "sbsandbox-intern-edullm-artifacts --s3-prefix "
                "cloudformation-templates/checksummed, because this template is past the "
                "51200-byte limit on a template submitted inline."
            ),
            findings=(
                RoleDriftFinding(
                    direction=DriftDirection.NARROWER,
                    element="inline policy 'deploy-phase2-admission-stacks' statement 8 resources",
                    detail=(
                        "the template declares resources the deployed role does not: "
                        "arn:<partition>:iam::<account>:role/sbsandbox-intern-edullm-janitor-lambda, "
                        "arn:<partition>:iam::<account>:role/sbsandbox-intern-edullm-janitor-schedule"
                    ),
                ),
                RoleDriftFinding(
                    direction=DriftDirection.NARROWER,
                    element="inline policy 'deploy-phase2-admission-stacks'",
                    detail=(
                        "the template declares a statement the deployed role does not: Allow "
                        "Action [s3:CreateBucket, s3:Get*, s3:ListBucket, "
                        "s3:PutBucketPublicAccessBlock, s3:PutEncryptionConfiguration, "
                        "s3:PutLifecycleConfiguration] Resource "
                        "[arn:<partition>:s3:::edullm-scratch]"
                    ),
                ),
                RoleDriftFinding(
                    direction=DriftDirection.NARROWER,
                    element="inline policy 'deploy-phase3-batch-stacks' statement 16 resources",
                    detail=(
                        "the template declares resources the deployed role does not: "
                        "arn:<partition>:iam::<account>:role/sbsandbox-intern-edullm-notifier-lambda"
                    ),
                ),
                RoleDriftFinding(
                    direction=DriftDirection.NARROWER,
                    element="inline policy 'deploy-phase3-batch-stacks'",
                    detail=(
                        "the template declares a statement the deployed role does not: Allow "
                        "Action [scheduler:CreateSchedule, scheduler:DeleteSchedule, "
                        "scheduler:GetSchedule, scheduler:ListTagsForResource, "
                        "scheduler:TagResource, scheduler:UntagResource, "
                        "scheduler:UpdateSchedule] Resource "
                        "[arn:<partition>:scheduler:<region>:<account>:schedule/default/sbsandbox-intern-edullm-*]"
                    ),
                ),
            ),
        ),
        # A SECOND RECORD BECAUSE IT IS A SECOND ROLE, WHICH IS THE ONE SPLIT THIS REGISTRY
        # WANTS. The note above refuses two records for one role, because a capture reports
        # the sum of the drift on it and two records would each describe an account state
        # that never exists. That reasoning is about one role, and the deploys here are
        # separate acts on separate stacks: this one is the lifecycle recorder learning to
        # read the metrics.json an eval run writes, which is what puts eval_metrics on a
        # ResultManifest instead of leaving the scores only in W&B.
        PendingAmendment(
            role_name="sbsandbox-intern-edullm-lifecycle-lambda",
            reason=(
                "infra/iam/lifecycle-lambda-role.yaml gained one s3:GetObject statement in "
                "the record-what-batch-reported inline policy, scoped to "
                "sbsandbox-intern-edullm-outputs/teams/*/runs/*/metrics.json. The recorder "
                "needs it to read the metrics document an eval run writes and put the scores "
                "on the ResultManifest it was already writing."
                "\n\n"
                "Narrower is the only direction this can be in, and the read is the narrowest "
                "form of the grant that works: one action, on one key name, under a prefix "
                "the role could already ListBucket. It cannot reach a checkpoint, a log or "
                "any other object a run wrote."
                "\n\n"
                "WHAT THE OUTSTANDING DEPLOY COSTS, WHICH IS LESS THAN IT LOOKS. "
                "lifecycle_handler._MetricsReader answers None on both NoSuchKey and "
                "AccessDenied, and lifecycle_projection then records a result with no scores "
                "rather than failing the delivery. So while this stands, an eval run's result "
                "manifest is written and is silent about metrics; it does not lose the run. "
                "tests/test_eval_metrics.py holds that behaviour to it in both directions, "
                "which is why the grant is safe to commit before it is applied."
            ),
            cleared_by=(
                "Deploying the sbsandbox-intern-edullm-phase3-batch-iam stack from a laptop "
                "with credentials, per infra/README.md, then re-capturing the Phase 3 roles. "
                "The re-capture is what ends this record: the findings are compared for "
                "equality, so it fails the moment the account stops differing in exactly "
                "this way."
            ),
            findings=(
                RoleDriftFinding(
                    direction=DriftDirection.NARROWER,
                    element="inline policy 'record-what-batch-reported'",
                    detail=(
                        "the template declares a statement the deployed role does not: Allow "
                        "Action [s3:GetObject] Resource "
                        "[arn:<partition>:s3:::sbsandbox-intern-edullm-outputs/teams/*/runs/"
                        "*/metrics.json]"
                    ),
                ),
            ),
        ),
    )
    declared = declared_role_templates()
    for amendment in amendments:
        if amendment.role_name not in declared:
            raise PendingAmendmentError(
                f"pending amendment for {amendment.role_name}: no committed template "
                "declares that role, so nothing here will ever compare it and the record "
                "would never clear"
            )
    names = [amendment.role_name for amendment in amendments]
    if len(set(names)) != len(names):
        raise PendingAmendmentError(f"one role may carry one pending amendment; got {names}")
    return amendments


PENDING_AMENDMENTS: Final = pending_amendments()


def pending_for(role_name: str) -> PendingAmendment | None:
    """The amendment recorded for this role, or ``None`` if none is."""
    return next(
        (amendment for amendment in PENDING_AMENDMENTS if amendment.role_name == role_name), None
    )


# --------------------------------------------------------------------------------------
# A packaged change that is committed and has not been uploaded yet
# --------------------------------------------------------------------------------------
#
# THE SAME SHAPE AS AN UNDEPLOYED AMENDMENT, ARRIVED AT FROM THE OTHER END, AND IT WAS
# COSTING AN ADMIN MERGE EVERY TIME. `tools/build_admission_lambda.py` packages the modules
# the handler imports and the seven config files it reads, so a change to any of them moves
# the release digest and `tests/test_phase2_lambda_package.py` goes red until the zip is
# uploaded and `infra/admission-validator-release.yaml` records it. The upload needs AWS
# credentials, and the only path to them that is always available is
# `deploy-phase2-admission.yml`, which runs from `main` and nowhere else.
#
# Those three facts make a cycle: the zip cannot be uploaded until the change is on `main`,
# and the change cannot merge green while the tripwire is red. Every change to a packaged
# module therefore lands by an administrator merging past a required check -- twice on
# 2026-08-04 alone, once for this structural reason and once for a plain missing signature.
# That is the real cost rather than the inconvenience: a bypass that happens routinely stops
# being read, and it is the same bypass that would let a genuinely broken change through.
#
# WHAT IS NOT DONE HERE, BECAUSE IT WOULD DEFEAT THE CONTROL.
# `infra/admission-validator-release.yaml` sets out both of the tempting repairs and refuses
# them: writing a digest for a zip nobody uploaded makes the record assert the one thing it
# exists to deny, and suppressing the test is the same act differently spelled. Neither is
# what happens below. The records still tie a digest to an S3 object version and still
# describe what is deployed rather than what builds; nothing added here can write one, and a
# release still has to be cut before the tripwire passes.
#
# What changes is only that the interval between "the tree moved" and "the release was cut"
# can be *named* -- by a record that says which function, which two digests, and which
# command -- and that an unnamed interval still fails exactly as loudly as it did before.


@dataclass(frozen=True)
class ReleasableFunction:
    """One Lambda a release can be cut for, and where what is deployed is written down."""

    #: What a reader calls it. Matches `name` on tools/release_lambda.py's Function.
    display: str
    #: Repository-relative path to the record naming the deployed digest and object version.
    release_record: str


#: Every function `tools/release_lambda.py` can release, restated rather than imported.
#:
#: A library module reaching into `tools/` is the wrong direction -- it would put the
#: release tool's subprocess and argparse machinery inside everything that reads this
#: registry -- so the two spellings are held together by a test instead.
#: `tests/test_pending_releases.py` compares this against `release_lambda.FUNCTIONS` in both
#: directions, so a third function added to the tool and not here is a failure by name.
#:
#: It is here at all for the reason `declared_role_templates` exists above: a pending record
#: for something nothing ever compares would never clear, because nothing would ever report
#: the difference it is waiting to stop seeing.
RELEASABLE_FUNCTIONS: Final[dict[str, ReleasableFunction]] = {
    "validator": ReleasableFunction(
        display="admission validator",
        release_record="infra/admission-validator-release.yaml",
    ),
    "recorder": ReleasableFunction(
        display="lifecycle recorder",
        release_record="infra/lifecycle-recorder-release.yaml",
    ),
    "janitor": ReleasableFunction(
        display="expiry janitor",
        release_record="infra/expiry-janitor-release.yaml",
    ),
    "notifier": ReleasableFunction(
        display="notifier",
        release_record="infra/notifier-release.yaml",
    ),
}

#: How long a recorded pending release may stand before it stops explaining anything.
#:
#: A quarter of the thirty days `edullm_platform.evidence.FRESHNESS_WINDOW` gives a capture,
#: and the gap between the two numbers is the gap between what each one is waiting on. A
#: role capture waits on somebody finding a laptop, a browser login and a set of credentials
#: the broker has been known to revoke; a release waits on one command run against a tree
#: that is already on `main`, and the only reason it could not be run sooner is that the
#: workflow holding the credential refuses to run from a branch.
#:
#: So the window has to be longer than review latency and shorter than the point at which
#: whoever moved the packaged bytes has stopped thinking about them. Seven days is both.
#: What it must not be is absent: a record with no expiry turns the tripwire off for one
#: function permanently the first time somebody forgets, and a forgotten release looks
#: exactly like a performed one from every consumer's side.
RELEASE_WINDOW: Final = timedelta(days=7)

#: A sha256 written the way a release record writes it. Lowercase is part of the shape:
#: an uppercase digest never compares equal to one the builder reports, so a record holding
#: one would read as unexplained skew for a defect in a committed file.
_HEX_DIGEST: Final = re.compile(r"^[0-9a-f]{64}$")

#: The command that clears a pending release, and the only one that can. Recorded entries
#: are required to name it, so a reader who meets a skipped tripwire is looking at the
#: instruction rather than at a description of one.
RELEASE_COMMAND: Final = "tools/release_lambda.py"


class PendingReleaseError(ValueError):
    """A recorded pending release is not something a reader could act on."""


def _today() -> date:
    return datetime.now(tz=UTC).date()


@dataclass(frozen=True)
class PendingRelease:
    """A change to a packaged module that is committed and has not been uploaded yet.

    Every field is compared against something, which is what stops this being an exemption
    with a date on it. :attr:`builds_to` and :attr:`released` are compared for equality
    against the pair the tripwire actually finds, in both directions and for the same reason
    :meth:`PendingAmendment.explains` does it that way -- containment would let a second
    packaged change arrive under cover of the first, and would go on reading as explained
    after the release that cleared it. :attr:`recorded_on` is compared against the clock.
    """

    #: A key of :data:`RELEASABLE_FUNCTIONS`, so the record names something releasable.
    function: str
    #: Why the bytes moved, in a sentence somebody reading a skipped test can weigh.
    reason: str
    #: The exact command that ends this. Required to name :data:`RELEASE_COMMAND` and this
    #: function, because a skip whose reader has to go and look up the procedure is a skip
    #: that gets ignored.
    cleared_by: str
    #: What a zip built from this tree hashes to. Moves if anything else the package
    #: carries is edited while this record is open, at which point it stops explaining.
    builds_to: str
    #: What the release record still names, which is what the account is running. Moves if
    #: somebody else cuts a release, at which point this stops explaining too.
    released: str
    #: The day this was written down. Not the day the change was made and not a deadline
    #: somebody chose: it is what :data:`RELEASE_WINDOW` is measured from.
    recorded_on: date

    def __post_init__(self) -> None:
        if self.function not in RELEASABLE_FUNCTIONS:
            raise self._fail(
                f"names no releasable function; {sorted(RELEASABLE_FUNCTIONS)} are the ones "
                "tools/release_lambda.py can cut a release for, and a record for anything "
                "else would never clear because nothing would ever compare it"
            )
        for name in ("reason", "cleared_by"):
            if not getattr(self, name).strip():
                raise self._fail(f"does not say {name.replace('_', ' ')}")
        for name in ("builds_to", "released"):
            if _HEX_DIGEST.fullmatch(getattr(self, name)) is None:
                raise self._fail(
                    f"records {name} as {getattr(self, name)!r}, which is not sixty-four "
                    "lowercase hexadecimal characters and so is not a digest anything here "
                    "will ever compare equal to"
                )
        if self.builds_to == self.released:
            raise self._fail(
                "records the same digest as built and released; a pending release that "
                "expects no difference is a record with nothing to clear"
            )
        if RELEASE_COMMAND not in self.cleared_by:
            raise self._fail(
                f"does not name {RELEASE_COMMAND} in cleared_by, and that is the only thing "
                "that ends a pending release. A skip that does not carry the command sends "
                "its reader to the procedure to work one out, which is how a labelled skip "
                "becomes an ignored one"
            )
        if not any(
            flag in self.cleared_by for flag in (f"--function {self.function}", "--function all")
        ):
            raise self._fail(
                f"gives a command that does not select {self.function}, so following it "
                "would release some other function and leave this record standing"
            )
        if self.recorded_on > _today():
            raise self._fail(
                f"is dated {self.recorded_on.isoformat()}, which is in the future. The "
                "window is measured from this field, so a date ahead of the clock is a way "
                "to stand for longer than RELEASE_WINDOW allows"
            )

    def _fail(self, problem: str) -> PendingReleaseError:
        return PendingReleaseError(f"pending release of the {self.function}: {problem}")

    @property
    def releasable(self) -> ReleasableFunction:
        return RELEASABLE_FUNCTIONS[self.function]

    @property
    def expires_on(self) -> date:
        """The last day this record explains anything."""
        return self.recorded_on + RELEASE_WINDOW

    def expired(self, today: date | None = None) -> bool:
        return (today or _today()) > self.expires_on

    def explains(self, *, built: str, released: str) -> bool:
        """Whether this is exactly the difference the record is waiting on.

        Equality on both digests, so the record cannot outlive either half of what it
        describes: not the release it is waiting for, and not the tree it was written
        against.
        """
        return (built, released) == (self.builds_to, self.released)

    def describe(self) -> str:
        """What a reader who meets this as a skipped test needs in order to act.

        Names the function, both digests, the record that would carry the new one, the day
        this stops being an explanation, and the command. A skip that says only that
        something is expected tells a reader that things are fine, which is the failure this
        whole module is trying not to commit.
        """
        return (
            f"Waiting on a release of the {self.releasable.display}. This tree builds "
            f"{self.builds_to} and {self.releasable.release_record} records {self.released} "
            f"as deployed, so the account is running the previous bytes on purpose and for "
            f"a recorded reason: {self.reason.strip()} "
            f"Clear it by running `{self.cleared_by.strip()}` from main and committing the "
            f"two files it edits. Recorded {self.recorded_on.isoformat()}; this record stops "
            f"explaining the difference after {self.expires_on.isoformat()}, when this test "
            "fails again whether or not the release has been cut."
        )


def pending_releases() -> tuple[PendingRelease, ...]:
    """Every committed change to a packaged module that has not been uploaded yet."""
    # Empty, which is the state this registry is meant to spend most of its life in. An
    # entry lives here only between a change to a packaged module being committed and the
    # release cut from `main` that uploads it, and that interval is a review plus one
    # command.
    #
    # Removal rather than exemption is the rule, and here it is not even a rule so much as
    # an inevitability: `tools/release_lambda.py` writes the new digest into the release
    # record, at which point `released` no longer matches the file and the entry fails
    # rather than lingering. The tool cannot remove the entry itself -- it edits two files
    # by regular expression and adding a third that deletes Python would be a worse tool --
    # so what happens instead is that the same commit carrying the release deletes it, and
    # the suite says so if it does not.
    #
    # WHAT AN ENTRY MUST NOT BE USED FOR. A digest that moved for a reason nobody has read
    # is the exact thing the tripwire exists to catch, and writing an entry to make it green
    # is writing down a sentence that is not true -- `reason` is compared against nothing,
    # so it is the one field here that can lie. What keeps that honest is that the entry is
    # a diff in a reviewed pull request, and that the two digests beside the reason are not
    # guessable: they have to be read off a build and off the release record, and a reader
    # can rebuild the zip and check both.
    #
    # ONE HAS BEEN REMOVED SO FAR AND IT IS THE WHOLE LIFECYCLE IN ONE ENTRY. The validator
    # record written on 2026-08-05 named the two refusals #221 removed from the packaged
    # half of the platform, and it was deleted the same day in the commit that cut the
    # release. The deploy is what actually ends it: uploading the zip and editing the
    # template makes `released` match `builds_to`, at which point `compare_release` reports
    # the record as SKEWED rather than letting it stand. So the deletion is not tidiness. A
    # record left here would go on absorbing the next unexplained difference that happened
    # to arrive on the same pair of digests.
    #
    # This entry is the next cycle: org.yaml header rewrite plus frontload-cl corpora on
    # top of the released zip, and it has been extended rather than joined by a second
    # record. `one_record_per_function` refuses two entries for one zip, and it is right to:
    # a zip carries whatever the tree holds when it is built, so there is one difference
    # between the account and this tree however many changes went into it, and a second
    # record would be describing nothing. Extending means `builds_to` moves and the reason
    # gains a paragraph, which is what a reviewer needs to see.
    releases: tuple[PendingRelease, ...] = (
        PendingRelease(
            function="validator",
            reason=(
                "The header of config/organization.yaml was rewritten on 2026-08-05, and "
                "config/organization.yaml is one of the seven files in ADMISSION_CONFIG, so "
                "comment bytes are packaged bytes. The header claimed team_leads was the "
                "whole of who may release a routine run, and that a hand-check had found it "
                "equal to the GitHub team-leads team. holds_routine_approver_role is "
                "is_admin or is_team_lead, so the approvers are admins and team_leads "
                "together, and the GitHub team holds exactly those nine. Neither list moved."
                "\n\n"
                "Nothing the validator reads changed and no admission decision moves with "
                "this. admins, team_leads, every team binding and every member entry are "
                "byte-identical; what changed is the prose above them and nothing parses "
                "that. So the deployed validator is not stale in any sense a submitter could "
                "meet, and the release exists to keep the digest honest rather than to carry "
                "a behaviour."
                "\n\n"
                "The same zip also packages config/datasets.yaml, which now registers "
                "pretrain/frontload-cl-10b/v1 and sft/frontload-cl-chat-sft/v1."
                "\n\n"
                "And it packages the two comment lines the rename of the scheduled workflow "
                "reworded, one in config/organization.yaml and one in "
                "contracts/authorization.py. The nightly is the audit; nothing parses either "
                "line. All three edits are one release rather than three, because a zip "
                "carries whatever the tree holds when it is built, and three records "
                "describing one difference would mean two of them describing nothing."
                "\n\n"
                "A fourth edit joined them before this was cut, and it is the one worth "
                "reading carefully because it looks like a behaviour change and is not. "
                "`retired` in config/datasets.yaml is enforced now rather than only removing "
                "a menu item: DatasetRegistry grew is_retired, names_a_run_may_still_use and "
                "current_versions_of, and contracts/dataset_registry.py is a module this zip "
                "carries. What reads them is the submission path -- `edullm check` and "
                "tools/compile_submission.py, both before the approval gate -- and neither "
                "is in this package. The refusal is deliberately not a condition "
                "config/policy.yaml denies outright, so build_request_facts derives no new "
                "fact, denied_outright_conditions gains no new condition, and the validator "
                "this release deploys admits and refuses exactly what the one before it did. "
                "The header of config/datasets.yaml was recounted in the same change -- "
                "twenty-four published entries rather than sixteen, eleven not offered rather "
                "than eight, four tokenizers rather than two -- which is comment bytes in a "
                "packaged config file again."
                "\n\n"
                "A fifth edit joins them and it is a new field on a packaged contract, which "
                "is the kind that deserves the most reading. OrganizationInventory gained "
                "aws_identities, and contracts/bindings.py gained AwsRoleBinding, "
                "ExcludedRole and AwsIdentityTable beside it; config/organization.yaml now "
                "carries twenty role bindings and one exclusion. Both files are in this "
                "zip. Nothing the validator decides reads any of it: the table is joined "
                "against CloudTrail launch events by the morning message, which runs in the "
                "audit and is not packaged anywhere. The field defaults to an empty table, so "
                "every record and fixture written before it still parses, and admission "
                "admits and refuses exactly what it did before."
                "\n\n"
                "TWENTY RATHER THAN NINETEEN, AND THE ONE THAT MOVED IS WORTH NAMING BECAUSE "
                "IT IS A NUMBER TWO DOCUMENTS DISAGREED ABOUT. Intern-linjian.ni-sbsandbox "
                "was held out of the table on the grounds that no roster name matches it, "
                "which treated an answered question as open: the roster comment above ninLi0 "
                "records the owner confirming on 2026-08-04 that the role is that person, and "
                "that comment has been on main since the W&B accounts commit. Binding it "
                "moves the count of roster members with no bound role from sixteen to "
                "fifteen, which is the difference the build index and this table had between "
                "them. Still nothing the validator reads."
                "\n\n"
                "A SIXTH EDIT IS THE LARGEST OF THEM BY LINE COUNT AND THE SMALLEST BY "
                "DEPLOYED BEHAVIOUR. Schema version two of the run manifest landed in "
                "contracts/manifest.py as RunManifestV2 and RunInput, beside a "
                "read_run_manifest that dispatches on the declared version; "
                "contracts/vocabulary.py gained InputRole; contracts/dataset_registry.py "
                "gained WEIGHTS_FAMILIES and may_fill; contracts/results.py gained "
                "EvalMetric, EvalMetrics and an optional ResultManifest.eval_metrics. All "
                "four modules are packaged, so the digest moved again."
                "\n\n"
                "NOTHING THE VALIDATOR DOES MOVES WITH ANY OF IT, AND THE REASON IS THAT "
                "VERSION ONE WAS NOT EDITED. RunManifest is byte-identical: version two is a "
                "second class standing beside it rather than a rewrite of it, which is what "
                "keeps the manifest_sha256 on every intent and decision record in the "
                "lineage store computing to the value that record carries. The validator "
                "parses a submitted manifest through RunManifest exactly as before and "
                "recomputes exactly the same digest for it. Nothing constructs a "
                "RunManifestV2 yet, and no code path in the packaged half calls "
                "read_run_manifest."
                "\n\n"
                "ResultManifest.eval_metrics is the one addition that changes a canonical "
                "form, and it is worth saying why that is safe rather than asserting it. It "
                "is optional and defaulted, so a document written before it existed still "
                "parses; what it does change is the digest of any ResultManifest hashed "
                "today, because the canonical form now carries an explicit null. That costs "
                "nothing only because no stored record carries a ResultManifest digest -- "
                "every manifest_sha256 in the store is over a run manifest, which is the one "
                "this change deliberately did not touch. The validator does not construct a "
                "ResultManifest at all; the recorder does."
                "\n\n"
                "A SEVENTH EDIT MOVED IT AGAIN AND THIS ONE DOES CARRY A BEHAVIOUR, unlike the "
                "sixth above it. "
                "Registering edullm-p1 writes config/repositories.yaml and "
                "config/workload-catalog.yaml, and the validator packages both. Until the "
                "release is cut the deployed validator has never heard of the repository or "
                "of edullm-p1-check, so a submission naming either is refused by a principal "
                "reading the previous catalog while the form offers both. That is the same "
                "shape as the Phase 4 refusal this record's own file describes: correct for "
                "the bytes that produced it, wrong about the account, and naming the workload "
                "rather than the release. It is one record rather than two because a zip "
                "carries whatever the tree holds when it is built."
            ),
            cleared_by="uv run python tools/release_lambda.py --function validator",
            builds_to="a2f7c751245b8d25b691bb56b280e50c19c569e4f1063e5152f420faf7546ab5",
            released="d2c42173589e7c91ff20faeaa7b5b9f705f02e28214ad15fcf782964bf7bf3af",
            recorded_on=date(2026, 8, 5),
        ),
        # THE RECORDER, WHICH THE ROSTER EDITS OF 2026-08-05 DID NOT MOVE AND THIS ONE DOES.
        # The two zips were separated deliberately so that a config edit stopped touching
        # this one: measured two ways, the validator reads seven config files and the
        # recorder reads none, and the builders package exactly that. What moved here is not
        # config at all. It is contracts/bindings.py and contracts/inventory.py, which this
        # package imports through the manifest it validates on the way into a lineage record.
        PendingRelease(
            function="recorder",
            reason=(
                "contracts/bindings.py gained AwsRoleBinding, ExcludedRole and "
                "AwsIdentityTable, and contracts/inventory.py gained an aws_identities field "
                "on OrganizationInventory that defaults to an empty table. Both modules are "
                "in this package because the recorder validates the manifest it writes."
                "\n\n"
                "Nothing the recorder reads or writes changes. It never loads "
                "config/organization.yaml -- the split between the two builders exists "
                "precisely so a roster edit stops touching this zip -- and the new field is "
                "on a model this package imports rather than instantiates. Every lineage "
                "record it writes is byte-identical to one the deployed bytes would have "
                "written."
                "\n\n"
                "Recorded rather than left to be discovered because a stale recorder is the "
                "quiet one: a stale validator refuses a submission and somebody reads the "
                "refusal, and a stale recorder writes lineage that looks exactly like correct "
                "lineage into immutable records."
                "\n\n"
                "A SECOND CHANGE JOINED IT AND THIS ONE DOES CARRY A BEHAVIOUR, WHICH IS THE "
                "DIFFERENCE WORTH READING. The recorder learned to read the metrics document "
                "an eval run writes and to put the scores on the ResultManifest it was "
                "already writing. lifecycle_projection.py gained a MetricsReader protocol and "
                "an optional metrics_reader parameter on project_batch_state_change and "
                "project_batch_event; lifecycle_handler.py gained the _MetricsReader that "
                "adapts the boto3 client it already holds; eval_metrics.py is new and is "
                "packaged because the handler imports it; contracts/results.py gained the "
                "EvalMetrics the projection constructs."
                "\n\n"
                "The behaviour is additive in every direction. A delivery for a run that "
                "wrote no metrics.json records exactly what it recorded before. A delivery "
                "for a run that wrote one gains an eval_metrics block. A delivery whose read "
                "is refused -- which is every delivery until the lifecycle-lambda amendment "
                "recorded above is applied -- records no scores rather than failing, because "
                "_MetricsReader answers None on AccessDenied as well as on NoSuchKey."
                "\n\n"
                "That is why a record is open here and another against the role at the same "
                "time, and why neither ordering is a problem. Release this zip before the "
                "role is amended and the recorder tries the read, is refused, and writes the "
                "record it always wrote. Amend the role first and nothing reads the grant "
                "until the zip lands. Neither ordering loses a delivery, which is the "
                "property tests/test_eval_metrics.py holds it to in both directions."
            ),
            cleared_by="uv run python tools/release_lambda.py --function recorder",
            builds_to="46b44ae3bb921fd3a3bf48225e49eda5d1a8e1dda1bff4b61cee84708f7523e7",
            released="82d291969f3b7c3922ea4096387abb0cd121e78b036858a36bcfc54b775027e2",
            recorded_on=date(2026, 8, 5),
        ),
    )
    return one_record_per_function(releases)


def one_record_per_function(
    releases: Sequence[PendingRelease],
) -> tuple[PendingRelease, ...]:
    """The records, or a refusal if two of them describe the same zip.

    Its own function rather than four lines inside :func:`pending_releases`, so that the
    refusal can be exercised: the register above holds a literal, which is the right shape
    for a thing edited by hand in a reviewed diff and the wrong shape for a test to inject
    into.
    """
    functions = [release.function for release in releases]
    if len(set(functions)) != len(functions):
        raise PendingReleaseError(
            f"one function may carry one pending release; got {functions}. Two records for "
            "the same zip cannot both describe it, and whichever is read first would decide "
            "which difference counts as expected while the other sat describing nothing"
        )
    return tuple(releases)


PENDING_RELEASES: Final = pending_releases()


def pending_release_for(function: str) -> PendingRelease | None:
    """The release recorded for this function, or ``None`` if none is."""
    return next(
        (release for release in PENDING_RELEASES if release.function == function), None
    )


def releases_beyond_their_window(today: date | None = None) -> tuple[PendingRelease, ...]:
    """Every recorded pending release that has stood longer than it may.

    Separate from :func:`compare_release` and cheap on purpose. The comparison needs a zip,
    which costs a `uv pip install` and is therefore marked slow and skipped by `-m "not
    slow"`; this needs nothing but the clock. A forgotten release has to become visible on
    the run everybody makes, not on the one they opt into.
    """
    return tuple(release for release in PENDING_RELEASES if release.expired(today))


class ReleaseVerdict(StrEnum):
    """Whether the zip this tree builds is the zip the account is running."""

    #: The digests agree. Nothing is outstanding and nothing is recorded.
    MATCHES = "release_matches"
    #: They disagree, and a record says which difference this is, why, and what ends it.
    PENDING_RELEASE = "release_pending"
    #: They disagree and nothing here explains it -- or a record does not fit, or has stood
    #: too long. One member for all three, because the caller's move is the same in each:
    #: fail, loudly, with the detail saying which happened.
    SKEWED = "release_skewed"


@dataclass(frozen=True)
class ReleaseComparison:
    """One function's deployed bytes against this tree's, and what that state is called."""

    function: str
    built: str
    released: str
    verdict: ReleaseVerdict
    #: A sentence naming what a reader should do, whichever of the three states this is.
    detail: str

    @property
    def holds(self) -> bool:
        return self.verdict is ReleaseVerdict.MATCHES

    @property
    def waiting(self) -> bool:
        """Whether standing down is the recorded, explained, time-limited thing to do."""
        return self.verdict is ReleaseVerdict.PENDING_RELEASE


def compare_release(
    function: str,
    *,
    built: str,
    released: str,
    today: date | None = None,
) -> ReleaseComparison:
    """Compare a built digest against a recorded one, consulting the pending register.

    THE ONLY THING THAT MAY TURN A MISMATCH INTO A SKIP IS A RECORD THAT FITS IT EXACTLY,
    AND THE FIT IS CHECKED IN BOTH DIRECTIONS. That is what does the work
    :func:`~edullm_platform.phase1_capture.only_a_pending_deploy_stands_in_the_way` does for
    captures, where the caller asks a whole-tree question and one unexplained capture has to
    stop it standing down. The question here is per-function -- one zip, one record, one
    pair of digests -- and every way a second problem could arrive is already a change to
    one of those two digests:

    * something else packaged is edited while the record is open, and ``built`` moves;
    * somebody else cuts a release, and ``released`` moves;
    * the release this record was waiting for is cut, and the two become equal.

    Each of those makes the record stop explaining, and each is reported here as
    :attr:`ReleaseVerdict.SKEWED` rather than absorbed. So there is no set of other things
    to check for -- which is a property of the digests being a total description of the
    comparison, not a reason to check less.
    """
    releasable = RELEASABLE_FUNCTIONS.get(function)
    if releasable is None:
        raise PendingReleaseError(
            f"{function!r} is not a releasable function; "
            f"{sorted(RELEASABLE_FUNCTIONS)} are"
        )
    recorded = pending_release_for(function)

    def answer(verdict: ReleaseVerdict, detail: str) -> ReleaseComparison:
        return ReleaseComparison(
            function=function, built=built, released=released, verdict=verdict, detail=detail
        )

    if built == released:
        if recorded is None:
            return answer(
                ReleaseVerdict.MATCHES,
                f"The {releasable.display} zip this tree builds is the zip "
                f"{releasable.release_record} records as deployed.",
            )
        return answer(
            ReleaseVerdict.SKEWED,
            f"The release the pending record for the {function} was waiting on has been "
            f"cut: {releasable.release_record} now records {released}, which is what this "
            "tree builds. Delete the entry from "
            "edullm_platform.pending_amendments.pending_releases(). It is failing rather "
            "than lapsing quietly because a record left behind would absorb the next "
            "unexplained difference that happens to arrive on the same pair of digests.",
        )

    if recorded is None:
        return answer(
            ReleaseVerdict.SKEWED,
            "Nothing in edullm_platform.pending_amendments.pending_releases() records a "
            f"pending release of the {releasable.display}, so this difference is not one "
            "anybody has explained. If it is expected -- the change is on its way to main "
            "and deploy-phase2-admission.yml will not run from a branch -- record a "
            f"PendingRelease with function={function!r}, builds_to={built!r}, "
            f"released={released!r} and today's date. That turns this into a labelled skip "
            f"for {RELEASE_WINDOW.days} days and no longer, and it does not touch "
            f"{releasable.release_record}, which goes on describing what is actually "
            "deployed.",
        )

    if not recorded.explains(built=built, released=released):
        return answer(
            ReleaseVerdict.SKEWED,
            f"A pending release of the {releasable.display} is recorded, and it is not this "
            f"one: it is waiting for {recorded.builds_to} to replace {recorded.released}, "
            f"and this tree builds {built} against a recorded {released}. Something moved "
            "after the record was written -- another packaged file, or a release somebody "
            "else cut -- so the record no longer describes the difference and is not "
            "standing in for it. Rebuild, and either update the entry or remove it.",
        )

    if recorded.expired(today):
        return answer(
            ReleaseVerdict.SKEWED,
            f"The pending release of the {releasable.display} was recorded "
            f"{recorded.recorded_on.isoformat()} and stopped explaining anything after "
            f"{recorded.expires_on.isoformat()}, which is {RELEASE_WINDOW.days} days later. "
            "The difference it describes is still there, so the release was never cut: run "
            f"`{recorded.cleared_by.strip()}` from main. If it genuinely still cannot be "
            "cut, that is a fact worth a fresh record and a fresh reason rather than a "
            "window that renews itself.",
        )

    return answer(ReleaseVerdict.PENDING_RELEASE, recorded.describe())
