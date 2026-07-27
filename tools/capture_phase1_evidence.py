"""Read the Phase 1 account and write down what is actually there.

This needs credentials, so it is a laptop or CI command rather than something the
acceptance gate runs. It captures the two roles Phase 1 depends on and the repository
they publish to, and — because a captured role is only interesting next to the template
that claims to describe it — it compares each role to its committed template and refuses
to report success when the two disagree.

**Selected fields, not dumped responses.** Phase 0's capture writes the raw API answers
beside the sanitized records. This one does not, and the reason is worth stating: the
records Phase 1 needs come from calls whose raw answers carry credentials. A CloudTrail
record of a role session contains the ``ASIA`` access key id the session was issued, and
``scan_for_secrets`` refuses it — correctly, because a working directory is exactly where
a file like that gets committed by accident. So there is no raw tier at all. What is
written is the projection, field by chosen field, and everything else is read and
dropped.

**Two placeholders for accounts, not one.** Everything captured goes through
``redact_account_ids_in_document`` before a contract sees it, which masks this account
and any other account differently. One placeholder for both would be simpler and would
make a resource ARN pointing at somebody else's account normalise away against the
template's ``${AWS::AccountId}``, which is the widening most worth catching.

**Adding a target.** :data:`CAPTURE_TARGETS` is the registry and the extension point.
Each entry is a name and a function from the capture context to the records it produces,
and the command iterates the registry rather than naming targets of its own.

**Two kinds of target, and the difference decides what a capture has to establish.**
``roles`` and ``repository`` read standing facts, and each has a committed template to be
compared against; a capture that disagrees with the template is the finding. The four run
targets — ``image``, ``scan``, ``session`` and ``denials`` — read what one publish run
left behind, and none of them has a template. What stands in for the comparison is the
*join*, and every one of them is joined to the image the run produced rather than found
by recency:

``image``
    ``EcrImageEvidence``, from ``ecr describe-images --image-ids imageTag=…`` for the
    digest and push time, joined to the commit the caller names and to the base image
    digest the platform registered. The tag is the commit's first twelve characters and
    the contract re-checks that, so a tag and a commit that do not belong together fail.
``scan``
    ``ImageScanEvidence``, from ``ecr describe-image-scan-findings`` for that digest —
    the digest, never the tag, and the answer's own ``imageId`` is checked back against
    it. The repository is created with ``ScanOnPush``, so the scan exists as soon as the
    image does. Severities ECR omits because their count is zero are recorded as zero,
    because a record that could drop one could not say whether the scan found none.
``session``
    ``OidcSessionEvidence``, found by the access key that pushed the image: the
    ``PutImage`` event for this tag names it, and the ``AssumeRoleWithWebIdentity`` event
    that issued it is the session that did the push. The alternative — the most recent
    session anybody held — is a different fact, and on the afternoon this was first run
    it would have been the wrong one. This is the call whose raw record carries the
    ``ASIA`` key id; it is used as a lookup key inside this process and never written.
``denials``
    ``DenialEvidence``, one per matrix action, completing the ``PublisherDenialMatrix``
    the publish workflow's deny job wrote. That record has everything except the
    CloudTrail identity of each refusal, because the publisher session cannot read
    CloudTrail; this is the capture with credentials that can, and the join is by
    operation, role session and a bounded time window around the attempt.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final

from pydantic import ValidationError

from edullm_platform.build_tooling import RegistryUnreadableError, load_registry
from edullm_platform.contracts.base import ContractModel
from edullm_platform.contracts.repository_registry import UnknownRepositoryError
from edullm_platform.evidence import (
    EvidenceEnvironment,
    redact_aws_account_ids,
    redact_content_digests,
)
from edullm_platform.phase1_evidence import (
    UNDECLARED_IDENTITY_PLACEHOLDER,
    DeployedRoleEvidence,
    EcrImageEvidence,
    EcrLifecycleRule,
    EcrRepositoryEvidence,
    ImageScanEvidence,
    ImmutableTagRefusalEvidence,
    OidcSessionEvidence,
)
from edullm_platform.publisher_denials import (
    AUTHORIZATION_ERROR_CODES,
    AttemptedDenial,
    PublisherDenialMatrix,
    assumed_role_identity,
    denial_evidence,
    parse_aws_cli_error,
)
from edullm_platform.role_drift import (
    COMMITTED_ROLE_TEMPLATES,
    PUBLISHER_ROLE_NAME,
    PolicyNotComparableError,
    RoleDriftReport,
    compare_role_to_template,
    load_template_roles,
    project_deployed_role,
    split_arn_fields,
)

#: Captured evidence is local-only until somebody has read it and copied the part they
#: want into ``fixtures/``. Writing anywhere else is refused rather than discouraged.
ALLOWED_OUTPUT_SUFFIX: Final = Path("docs-frank/working/phase-1-evidence")
DEFAULT_REGISTRY: Final = Path("config/repositories.yaml")

#: How long one AWS call may take before the answer stops being worth waiting for.
AWS_CALL_TIMEOUT_SECONDS: Final = 60

#: How far back a CloudTrail lookup reaches. A management event stays in the trail's
#: own ninety-day history, and the run being captured is normally the same day; the
#: window is here so a lookup returns the run being captured rather than every session
#: the role has ever held.
DEFAULT_LOOKUP_WINDOW: Final = timedelta(days=7)

#: How far a CloudTrail event may sit from the attempt it is joined to. CloudTrail
#: timestamps have one-second resolution and are recorded by the service rather than by
#: the caller, so exact equality would fail on rounding alone. Two minutes is wide
#: enough for that and far narrower than the gap between two runs of the matrix, which
#: is what the window has to keep apart.
DENIAL_JOIN_TOLERANCE: Final = timedelta(minutes=2)

#: How many pages of one CloudTrail lookup are read before the capture gives up.
#: CloudTrail answers fifty events a page, so this is five thousand events of one
#: operation inside the window — far more than one run produces and far less than an
#: unbounded loop against a shared account.
MAXIMUM_LOOKUP_PAGES: Final = 100

#: What ECR answers a push aimed at a tag an immutable repository already holds.
IMMUTABLE_TAG_ERROR_CODE: Final = "ImageTagAlreadyExistsException"

#: The image tag the publish workflow writes: the commit's first twelve characters.
IMAGE_TAG_LENGTH: Final = 12

#: The prefix an OIDC provider ARN carries before the issuer's host name. The host is
#: what the record keeps; the ARN in front of it is the account ID with a name attached.
OIDC_PROVIDER_ARN_MARKER: Final = ":oidc-provider/"


class CaptureFailedError(RuntimeError):
    """The account could not be read, so there is nothing honest to write down.

    Carries a machine-readable reason and, where AWS gave one, the operation and error
    code. Never the service's message: it names the account, and this is raised from a
    process whose output a reader may well paste somewhere.
    """

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class OutputDirectoryRefusedError(CaptureFailedError, ValueError):
    """The capture was aimed somewhere it is not allowed to write.

    Deliberate, and worth saying out loud rather than reporting as a failed write: a
    capture is local-only until somebody has read it and copied the part they want into
    ``fixtures/``, and a tool that could write anywhere would make that a step nobody had
    to take. What the operator needs back is *which* constraint refused, because the path
    that gets typed is usually absolute — and an absolute path is fine. What is not fine
    is where it lands, which for an absolute path into another checkout of this
    repository is somewhere this one does not own.
    """

    def __init__(self, *, requested: Path, resolved: Path, allowed_root: Path) -> None:
        super().__init__(
            "output_dir_outside_working_directory\n"
            f"  asked for: {requested}\n"
            f"  resolves to: {resolved}\n"
            f"  must be under: {allowed_root}\n"
            "A capture is local-only until somebody reads it and copies what they want into "
            f"fixtures/, so this tool writes only under {ALLOWED_OUTPUT_SUFFIX}/ and refuses "
            "anywhere else. Absolute paths are accepted; they are resolved against this "
            "checkout, so one naming a different worktree of this repository lands outside it."
        )


@dataclass(frozen=True)
class AccountIdentity:
    """Who the capture is running as. Neither field is ever written to a file.

    The account ID is here so a captured ARN naming *this* account can be told from one
    naming another; the partition is here because the normalisation the drift comparison
    applies is allowed to fold only the partition it was told to expect.
    """

    account_id: str
    partition: str


@dataclass(frozen=True)
class CaptureContext:
    aws_profile: str
    aws_region: str
    environment: EvidenceEnvironment
    ecr_repository: str
    identity: AccountIdentity
    observed_at: datetime
    repo_root: Path
    #: What the run targets need and the standing-fact targets do not. Each is optional
    #: here and required by the target that reads it, so a capture of the roles alone
    #: does not have to name a commit that has nothing to do with them.
    commit_sha: str | None = None
    base_image_digest: str | None = None
    denials_matrix: PublisherDenialMatrix | None = None
    lookup_since: datetime | None = None

    @property
    def image_tag(self) -> str:
        assert self.commit_sha is not None
        return self.commit_sha[:IMAGE_TAG_LENGTH]

    @property
    def lookup_start(self) -> datetime:
        if self.lookup_since is not None:
            return self.lookup_since
        return self.observed_at - DEFAULT_LOOKUP_WINDOW


#: One record and where it goes, relative to the output directory.
CapturedRecord = tuple[str, ContractModel]
CaptureFunction = Callable[[CaptureContext], tuple[CapturedRecord, ...]]


@dataclass(frozen=True)
class CaptureTarget:
    name: str
    capture: CaptureFunction


@dataclass(frozen=True)
class CapturedEvidence:
    """Everything one capture produced, split by what it describes."""

    roles: tuple[DeployedRoleEvidence, ...]
    repository: EcrRepositoryEvidence | None
    drift: tuple[RoleDriftReport, ...]
    written: tuple[Path, ...]

    @property
    def drift_findings(self) -> int:
        return sum(len(report.findings) for report in self.drift)


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def allowed_output_root(base_dir: Path | None = None) -> Path:
    root = base_dir if base_dir is not None else project_root()
    return (root / ALLOWED_OUTPUT_SUFFIX).resolve()


def resolve_output_dir(output_dir: Path, *, base_dir: Path | None = None) -> Path:
    root = base_dir if base_dir is not None else project_root()
    candidate = output_dir if output_dir.is_absolute() else (root / output_dir)
    resolved = candidate.resolve()
    allowed = allowed_output_root(root)
    try:
        resolved.relative_to(allowed)
    except ValueError as exc:
        raise OutputDirectoryRefusedError(
            requested=output_dir, resolved=resolved, allowed_root=allowed
        ) from exc
    return resolved


# --------------------------------------------------------------------------------------
# Talking to the account
# --------------------------------------------------------------------------------------


def aws_json(
    context: CaptureContext,
    arguments: Sequence[str],
    *,
    absent_error_codes: Sequence[str] = (),
) -> dict[str, Any] | None:
    """Run one AWS call and return its JSON, or ``None`` for an expected absence.

    ``absent_error_codes`` is for the one case where a service says "there is no such
    thing" and that is an answer rather than a failure: a repository with no lifecycle
    policy. Every other non-zero exit stops the capture.
    """
    command = [
        "aws",
        *arguments,
        "--profile",
        context.aws_profile,
        "--region",
        context.aws_region,
        "--output",
        "json",
    ]
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=AWS_CALL_TIMEOUT_SECONDS,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise CaptureFailedError(f"aws_call_timed_out:{arguments[0]} {arguments[1]}") from exc
    except OSError as exc:
        raise CaptureFailedError("aws_cli_unavailable") from exc
    if completed.returncode == 0:
        payload = json.loads(completed.stdout)
        if not isinstance(payload, dict):
            raise CaptureFailedError(f"aws_answer_was_not_an_object:{arguments[0]} {arguments[1]}")
        return payload
    error = parse_aws_cli_error(completed.stderr)
    if error is None:
        raise CaptureFailedError(f"aws_call_failed:{arguments[0]} {arguments[1]}")
    if error.code in absent_error_codes:
        return None
    raise CaptureFailedError(f"aws_call_failed:{error.operation}:{error.code}")


def required_json(context: CaptureContext, arguments: Sequence[str]) -> dict[str, Any]:
    answer = aws_json(context, arguments)
    assert answer is not None
    return answer


def read_identity(*, aws_profile: str, aws_region: str) -> AccountIdentity:
    """Ask STS who this is, and read the partition out of the ARN it answers with.

    ``GetCallerIdentity`` needs no permission, so it says nothing about what the caller
    can do — which is the point: it is the one call here that cannot fail for a reason
    worth reporting as a finding.
    """
    context = CaptureContext(
        aws_profile=aws_profile,
        aws_region=aws_region,
        environment="sandbox",
        ecr_repository="",
        identity=AccountIdentity(account_id="", partition="aws"),
        observed_at=datetime.now(tz=UTC),
        repo_root=project_root(),
    )
    answer = required_json(context, ("sts", "get-caller-identity"))
    account_id = answer.get("Account")
    fields = split_arn_fields(str(answer.get("Arn", "")))
    if not isinstance(account_id, str) or not account_id or fields is None:
        raise CaptureFailedError("caller_identity_unreadable")
    return AccountIdentity(account_id=account_id, partition=fields[1])


# --------------------------------------------------------------------------------------
# Projecting what the account returned
# --------------------------------------------------------------------------------------


def project_lifecycle_rule(rule: Mapping[str, Any]) -> EcrLifecycleRule:
    selection = rule.get("selection")
    action = rule.get("action")
    if not isinstance(selection, dict) or not isinstance(action, dict):
        raise CaptureFailedError("lifecycle_rule_unreadable")
    return EcrLifecycleRule.model_validate(
        {
            "rule_priority": rule.get("rulePriority"),
            "description": rule.get("description"),
            "tag_status": selection.get("tagStatus"),
            "tag_patterns": selection.get("tagPatternList", []),
            "tag_prefixes": selection.get("tagPrefixList", []),
            "count_type": selection.get("countType"),
            "count_number": selection.get("countNumber"),
            "count_unit": selection.get("countUnit"),
            "action_type": action.get("type"),
        }
    )


def project_repository(
    described: Mapping[str, Any],
    lifecycle_policy_text: str | None,
    *,
    context: CaptureContext,
) -> EcrRepositoryEvidence:
    """The repository's configuration, without the two fields that are the account.

    ``registryId`` is the account ID and ``repositoryUri`` carries it in the host name.
    Both are dropped: the repository name and the region identify the repository.
    """
    scanning = described.get("imageScanningConfiguration")
    encryption = described.get("encryptionConfiguration")
    if not isinstance(scanning, dict) or not isinstance(encryption, dict):
        raise CaptureFailedError("repository_description_unreadable")
    rules: list[EcrLifecycleRule] | None = None
    if lifecycle_policy_text is not None:
        policy = json.loads(lifecycle_policy_text)
        if not isinstance(policy, dict) or not isinstance(policy.get("rules"), list):
            raise CaptureFailedError("lifecycle_policy_unreadable")
        rules = [project_lifecycle_rule(rule) for rule in policy["rules"]]
    return EcrRepositoryEvidence.model_validate(
        {
            "source": "aws",
            "environment": context.environment,
            "status": "ok",
            "observed_at": context.observed_at,
            "region": context.aws_region,
            "repository_name": described.get("repositoryName"),
            "image_tag_mutability": described.get("imageTagMutability"),
            "scan_on_push": scanning.get("scanOnPush"),
            "encryption_type": encryption.get("encryptionType"),
            "lifecycle_rules": rules,
        }
    )


# --------------------------------------------------------------------------------------
# The capture targets
# --------------------------------------------------------------------------------------


def capture_role(context: CaptureContext, *, role_name: str) -> DeployedRoleEvidence:
    role = required_json(context, ("iam", "get-role", "--role-name", role_name))["Role"]
    listed = required_json(context, ("iam", "list-role-policies", "--role-name", role_name))
    inline_documents = [
        required_json(
            context,
            ("iam", "get-role-policy", "--role-name", role_name, "--policy-name", policy_name),
        )
        for policy_name in listed.get("PolicyNames", [])
    ]
    attached = required_json(
        context, ("iam", "list-attached-role-policies", "--role-name", role_name)
    )
    return project_deployed_role(
        role,
        inline_documents,
        attached.get("AttachedPolicies", []),
        own_account=context.identity.account_id,
        environment=context.environment,
        observed_at=context.observed_at,
    )


def template_role_for(context: CaptureContext, *, role_name: str, relative_path: str) -> Any:
    roles = load_template_roles(context.repo_root / relative_path)
    matching = [role for role in roles if role.role_name == role_name]
    if len(matching) != 1:
        raise CaptureFailedError(f"template_does_not_declare_the_role:{relative_path}")
    return matching[0]


def capture_roles(context: CaptureContext) -> tuple[CapturedRecord, ...]:
    """Both roles, each followed by how it compares to the template that declares it."""
    records: list[CapturedRecord] = []
    for role_name, relative_path in COMMITTED_ROLE_TEMPLATES:
        evidence = capture_role(context, role_name=role_name)
        report = compare_role_to_template(
            evidence,
            template_role_for(context, role_name=role_name, relative_path=relative_path),
            template_path=relative_path,
            partition=context.identity.partition,
            region=context.aws_region,
        )
        records.append((f"sanitized/roles/{role_name}.sanitized.json", evidence))
        records.append((f"drift/{role_name}.json", report))
    return tuple(records)


def capture_repository(context: CaptureContext) -> tuple[CapturedRecord, ...]:
    described = required_json(
        context,
        ("ecr", "describe-repositories", "--repository-names", context.ecr_repository),
    )
    repositories = described.get("repositories")
    if not isinstance(repositories, list) or len(repositories) != 1:
        raise CaptureFailedError("repository_description_unreadable")
    lifecycle = aws_json(
        context,
        ("ecr", "get-lifecycle-policy", "--repository-name", context.ecr_repository),
        absent_error_codes=("LifecyclePolicyNotFoundException",),
    )
    policy_text = None if lifecycle is None else lifecycle.get("lifecyclePolicyText")
    return (
        (
            "sanitized/ecr-repository.sanitized.json",
            project_repository(repositories[0], policy_text, context=context),
        ),
    )


# --------------------------------------------------------------------------------------
# The run targets: what one completed publish left behind
# --------------------------------------------------------------------------------------


def describe_image(context: CaptureContext) -> Mapping[str, Any]:
    """The registry's own description of the image this commit was published as."""
    described = required_json(
        context,
        (
            "ecr",
            "describe-images",
            "--repository-name",
            context.ecr_repository,
            "--image-ids",
            f"imageTag={context.image_tag}",
        ),
    )
    details = described.get("imageDetails")
    if not isinstance(details, list) or len(details) != 1:
        raise CaptureFailedError("image_description_unreadable")
    detail = details[0]
    if not isinstance(detail, dict):
        raise CaptureFailedError("image_description_unreadable")
    return detail


def capture_image(context: CaptureContext) -> tuple[CapturedRecord, ...]:
    detail = describe_image(context)
    return (
        (
            "sanitized/ecr-image.sanitized.json",
            EcrImageEvidence.model_validate(
                {
                    "source": "aws",
                    "environment": context.environment,
                    "status": "ok",
                    "observed_at": context.observed_at,
                    "region": context.aws_region,
                    "repository_name": detail.get("repositoryName"),
                    "image_digest": detail.get("imageDigest"),
                    "image_tag": context.image_tag,
                    "source_commit_sha": context.commit_sha,
                    "base_image_digest": context.base_image_digest,
                    "image_pushed_at": detail.get("imagePushedAt"),
                }
            ),
        ),
    )


def capture_scan(context: CaptureContext) -> tuple[CapturedRecord, ...]:
    """The scan of the image this commit produced, looked up by digest.

    By digest rather than by tag, and the answer's own ``imageId`` is checked back
    against the digest that was asked for. A scan record filed under the wrong image
    would read as a statement about this one, and a tag can be pointed somewhere else in
    a repository that is not immutable.
    """
    digest = describe_image(context).get("imageDigest")
    answer = required_json(
        context,
        (
            "ecr",
            "describe-image-scan-findings",
            "--repository-name",
            context.ecr_repository,
            # Singular, unlike describe-images. One scan is reported for one image, and
            # the flag names are not interchangeable: the plural is rejected outright.
            "--image-id",
            f"imageDigest={digest}",
        ),
    )
    answered_for = answer.get("imageId")
    if not isinstance(answered_for, dict) or answered_for.get("imageDigest") != digest:
        raise CaptureFailedError("scan_describes_another_image")
    scan_status = answer.get("imageScanStatus")
    if not isinstance(scan_status, dict):
        raise CaptureFailedError("scan_status_unreadable")
    findings = answer.get("imageScanFindings")
    findings = findings if isinstance(findings, dict) else {}
    counts = findings.get("findingSeverityCounts")
    counts = counts if isinstance(counts, dict) else {}
    reported = scan_status.get("status") in ("ACTIVE", "COMPLETE")
    return (
        (
            "sanitized/image-scan.sanitized.json",
            ImageScanEvidence.model_validate(
                {
                    "source": "aws",
                    "environment": context.environment,
                    "status": "ok",
                    "observed_at": context.observed_at,
                    "region": context.aws_region,
                    "repository_name": answer.get("repositoryName"),
                    "image_digest": digest,
                    "scan_status": scan_status.get("status"),
                    "scan_status_description": scan_status.get("description"),
                    "scan_completed_at": findings.get("imageScanCompletedAt"),
                    # ECR omits a severity whose count is zero, so absent is read as zero
                    # here and only here. A record that let a severity be absent could
                    # not say whether the scan found none or the capture dropped it.
                    "finding_counts": (
                        {
                            name: counts.get(name.upper(), 0)
                            for name in (
                                "critical",
                                "high",
                                "medium",
                                "low",
                                "informational",
                                "undefined",
                            )
                        }
                        if reported
                        else None
                    ),
                }
            ),
        ),
    )


def cloudtrail_events(
    context: CaptureContext,
    *,
    event_name: str,
) -> tuple[Mapping[str, Any], ...]:
    """Every CloudTrail record of one operation inside the lookup window.

    ``lookup-events`` is a read of the trail's own history and is the only CloudTrail
    call this tool makes. What comes back is parsed and projected; the raw record is a
    credential-carrying document and never leaves this function whole.

    **Paginated, and it has to be.** CloudTrail answers fifty events at a time in reverse
    time order, and this account is shared: another team's build pushes images all day,
    so the ``PutImage`` page covering one publish run is not the first page. A reader that
    took page one would find nothing and report the event as absent, which is the same
    answer it gives when the run never happened. Every page is read, and a trail with
    more pages than :data:`MAXIMUM_LOOKUP_PAGES` stops the capture instead of being
    silently truncated to the part that fit.
    """
    records: list[Mapping[str, Any]] = []
    next_token: str | None = None
    for _page in range(MAXIMUM_LOOKUP_PAGES):
        arguments = [
            "cloudtrail",
            "lookup-events",
            "--lookup-attributes",
            f"AttributeKey=EventName,AttributeValue={event_name}",
            "--start-time",
            context.lookup_start.isoformat(),
        ]
        if next_token is not None:
            arguments += ["--next-token", next_token]
        answer = required_json(context, tuple(arguments))
        events = answer.get("Events")
        if not isinstance(events, list):
            raise CaptureFailedError(f"cloudtrail_lookup_unreadable:{event_name}")
        for event in events:
            if not isinstance(event, dict):
                raise CaptureFailedError(f"cloudtrail_lookup_unreadable:{event_name}")
            try:
                record = json.loads(str(event.get("CloudTrailEvent")))
            except ValueError as exc:
                raise CaptureFailedError(f"cloudtrail_event_unreadable:{event_name}") from exc
            if not isinstance(record, dict):
                raise CaptureFailedError(f"cloudtrail_event_unreadable:{event_name}")
            records.append(record)
        token = answer.get("NextToken")
        if not isinstance(token, str) or not token:
            return tuple(records)
        next_token = token
    raise CaptureFailedError(f"cloudtrail_lookup_too_long:{event_name}")


def event_instant(record: Mapping[str, Any]) -> datetime:
    value = str(record.get("eventTime"))
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise CaptureFailedError("cloudtrail_event_time_unreadable") from exc
    if parsed.tzinfo is None:
        raise CaptureFailedError("cloudtrail_event_time_unreadable")
    return parsed.astimezone(UTC)


def event_session(record: Mapping[str, Any]) -> tuple[str, str] | None:
    """The role and session names of the caller, or ``None`` for anything else."""
    identity = record.get("userIdentity")
    if not isinstance(identity, dict):
        return None
    try:
        return assumed_role_identity(str(identity.get("arn")))
    except ValueError:
        return None


def session_created_at(record: Mapping[str, Any]) -> str | None:
    """When the role session behind a call was issued, as CloudTrail records it.

    This is the join key, and it is the only one that works. The obvious candidate — the
    access key the call was made with — is not the session's key for a ``docker push``:
    the caller exchanges its session for an ECR authorization token, and every registry
    operation after that is logged ``invokedBy: ecr.amazonaws.com`` under a key STS never
    issued to anybody. What survives the exchange is ``sessionContext``, whose
    ``creationDate`` is the instant of the ``AssumeRoleWithWebIdentity`` that started it,
    to the second.
    """
    identity = record.get("userIdentity")
    if not isinstance(identity, dict):
        return None
    session_context = identity.get("sessionContext")
    if not isinstance(session_context, dict):
        return None
    attributes = session_context.get("attributes")
    if not isinstance(attributes, dict):
        return None
    created = attributes.get("creationDate")
    return created if isinstance(created, str) else None


def capture_session(context: CaptureContext) -> tuple[CapturedRecord, ...]:
    """The session that pushed this image, found by when that session was issued.

    Two publisher sessions exist per run — the deny job holds one and the publish job
    holds another, twenty-five seconds apart and overlapping — and a re-run adds two
    more. Recording the most recent, or the one whose window contains the push, would
    name a session that had nothing to do with the image beside it. The ``PutImage``
    event for this tag carries the creation instant of the session that made it, and
    exactly one ``AssumeRoleWithWebIdentity`` event has that instant.
    """
    pushes = [
        record
        for record in cloudtrail_events(context, event_name="PutImage")
        if record.get("errorCode") is None
        and isinstance(record.get("requestParameters"), dict)
        and record["requestParameters"].get("imageTag") == context.image_tag
        and record["requestParameters"].get("repositoryName") == context.ecr_repository
    ]
    if len(pushes) != 1:
        raise CaptureFailedError("push_event_not_found")
    pushed_under = session_created_at(pushes[0])
    pushed_by = event_session(pushes[0])
    sessions = [
        record
        for record in cloudtrail_events(context, event_name="AssumeRoleWithWebIdentity")
        if pushed_under is not None
        and event_instant(record) == datetime.fromisoformat(pushed_under)
        and pushed_by is not None
        and issued_role(record) == pushed_by[0]
    ]
    if len(sessions) != 1:
        raise CaptureFailedError("session_for_the_push_not_found")
    return (("sanitized/publisher-session.sanitized.json", project_session(sessions[0], context)),)


def issued_role(record: Mapping[str, Any]) -> str | None:
    """The role name an ``AssumeRole*`` event issued a session for."""
    response = record.get("responseElements")
    if not isinstance(response, dict):
        return None
    assumed = response.get("assumedRoleUser")
    if not isinstance(assumed, dict):
        return None
    try:
        return assumed_role_identity(str(assumed.get("arn")))[0]
    except ValueError:
        return None


def project_session(
    record: Mapping[str, Any],
    context: CaptureContext,
) -> OidcSessionEvidence:
    response = record.get("responseElements")
    if not isinstance(response, dict):
        raise CaptureFailedError("session_response_unreadable")
    assumed_role_user = response.get("assumedRoleUser")
    if not isinstance(assumed_role_user, dict):
        raise CaptureFailedError("session_response_unreadable")
    try:
        role_name, session_name = assumed_role_identity(str(assumed_role_user.get("arn")))
    except ValueError as exc:
        raise CaptureFailedError("session_caller_is_not_an_assumed_role") from exc
    provider = str(response.get("provider"))
    if OIDC_PROVIDER_ARN_MARKER not in provider:
        raise CaptureFailedError("session_provider_unreadable")
    credentials = response.get("credentials")
    if not isinstance(credentials, dict):
        raise CaptureFailedError("session_response_unreadable")
    return OidcSessionEvidence.model_validate(
        {
            "source": "aws",
            "environment": context.environment,
            "status": "ok",
            "observed_at": context.observed_at,
            "region": context.aws_region,
            "event_id": record.get("eventID"),
            "event_name": record.get("eventName"),
            "event_source": record.get("eventSource"),
            "role_name": role_name,
            "session_name": session_name,
            # The provider's host name, not the ARN in front of it: the ARN is the
            # account ID, and the host is what the trust policy's conditions are about.
            "oidc_issuer": provider.split(OIDC_PROVIDER_ARN_MARKER, 1)[1],
            "oidc_audience": response.get("audience"),
            "oidc_subject": response.get("subjectFromWebIdentityToken"),
            "assumed_at": record.get("eventTime"),
            "expires_at": credentials.get("expiration"),
        }
    )


def capture_tag_refusal(context: CaptureContext) -> tuple[CapturedRecord, ...]:
    """A second push under this tag that ECR turned away, and the digest that survived.

    The refusal and the survival are two claims and this records both, because a refusal
    on its own does not say the original image is still there. The digest is read back
    from the registry now rather than taken from the earlier capture: the point is what
    the tag resolves to after somebody tried to move it.

    Nothing here requires the attempt to have been made by the publisher role, and the
    record says whether it was. Tag immutability is a property of the repository, so a
    refusal is a refusal whoever met it; pretending otherwise would mean either
    discarding the observation or implying an identity nobody observed. A role this
    repository does not declare is not named, because in a shared sandbox account those
    roles are people.
    """
    refusals = [
        record
        for record in cloudtrail_events(context, event_name="PutImage")
        if record.get("errorCode") == IMMUTABLE_TAG_ERROR_CODE
        and isinstance(record.get("requestParameters"), dict)
        and record["requestParameters"].get("imageTag") == context.image_tag
        and record["requestParameters"].get("repositoryName") == context.ecr_repository
    ]
    if len(refusals) != 1:
        raise CaptureFailedError("tag_refusal_event_not_found")
    refusal = refusals[0]
    attempted_by = event_session(refusal)
    if attempted_by is None:
        raise CaptureFailedError("tag_refusal_caller_is_not_an_assumed_role")
    return (
        (
            "sanitized/immutable-tag-refusal.sanitized.json",
            ImmutableTagRefusalEvidence.model_validate(
                {
                    "source": "aws",
                    "environment": context.environment,
                    "status": "ok",
                    "observed_at": context.observed_at,
                    "region": context.aws_region,
                    "repository_name": context.ecr_repository,
                    "image_tag": context.image_tag,
                    "source_commit_sha": context.commit_sha,
                    "image_digest": describe_image(context).get("imageDigest"),
                    "attempted_by": declared_identity(attempted_by[0]),
                    "attempted_by_publisher_role": attempted_by[0] == PUBLISHER_ROLE_NAME,
                    "attempted_at": refusal.get("eventTime"),
                    "outcome": "refused",
                    "error_code": refusal.get("errorCode"),
                    "error_message": sanitize_service_message(str(refusal.get("errorMessage"))),
                    "event_id": refusal.get("eventID"),
                    "event_name": refusal.get("eventName"),
                    "event_source": refusal.get("eventSource"),
                }
            ),
        ),
    )


def declared_identity(role_name: str) -> str:
    """The role's own name if a committed template declares it, or a placeholder.

    The sandbox account is shared, and every identity in it this repository does not own
    is somebody's personal role. Their name is not this project's to publish, and it is
    not what the record is for.
    """
    declared = {name for name, _template in COMMITTED_ROLE_TEMPLATES}
    return role_name if role_name in declared else UNDECLARED_IDENTITY_PLACEHOLDER


def sanitize_service_message(message: str) -> str:
    """Mask what a service message says about the account before a record can hold it.

    Account IDs first, then content digests, which is the order
    ``publisher_denials.sanitize_denial_message`` uses and the only order that works:
    masking a digest first would leave twelve of its digits looking like an account ID.
    """
    try:
        without_account = redact_aws_account_ids(message)
    except ValueError as exc:
        raise CaptureFailedError("service_message_holds_a_credential") from exc
    return redact_content_digests(without_account)


def denial_event_for(context: CaptureContext, attempt: AttemptedDenial) -> Mapping[str, Any]:
    """The one CloudTrail record of this refusal, or a refusal to guess between several.

    Four things have to agree before a record is this attempt's: the service, an
    authorization error code, the role session that made the call, and a time within
    :data:`DENIAL_JOIN_TOLERANCE` of when the attempt says it was made. The matrix has
    been run more than once against the same role with the same probes, so the window is
    what keeps two runs apart, and more than one candidate inside it is reported rather
    than resolved by taking the nearest.
    """
    candidates = [
        record
        for record in cloudtrail_events(context, event_name=attempt.event_name)
        if record.get("eventSource") == attempt.event_source
        and record.get("errorCode") in AUTHORIZATION_ERROR_CODES
        and event_session(record) == (attempt.role_name, attempt.session_name)
        and abs(event_instant(record) - attempt.attempted_at) <= DENIAL_JOIN_TOLERANCE
    ]
    if not candidates:
        raise CaptureFailedError(f"denial_event_not_found:{attempt.attempted_action}")
    if len(candidates) > 1:
        raise CaptureFailedError(f"denial_event_ambiguous:{attempt.attempted_action}")
    return candidates[0]


def capture_denials(context: CaptureContext) -> tuple[CapturedRecord, ...]:
    matrix = context.denials_matrix
    assert matrix is not None
    records: list[CapturedRecord] = []
    for attempt in matrix.attempts:
        event = denial_event_for(context, attempt)
        records.append(
            (
                f"sanitized/denials/{attempt.attempted_action.replace(':', '-')}.sanitized.json",
                denial_evidence(
                    attempt,
                    event_id=str(event.get("eventID")),
                    observed_at=context.observed_at,
                ),
            )
        )
    return tuple(records)


CAPTURE_TARGETS: Final[tuple[CaptureTarget, ...]] = (
    CaptureTarget(name="roles", capture=capture_roles),
    CaptureTarget(name="repository", capture=capture_repository),
    CaptureTarget(name="image", capture=capture_image),
    CaptureTarget(name="scan", capture=capture_scan),
    CaptureTarget(name="session", capture=capture_session),
    CaptureTarget(name="tag-refusal", capture=capture_tag_refusal),
    CaptureTarget(name="denials", capture=capture_denials),
)

#: Which targets cannot run without which argument, and the reason token each refusal
#: prints. Checked before the first call, so a capture that could not finish costs
#: nothing and reports why rather than failing partway through with records on disk.
TARGET_REQUIREMENTS: Final = (
    ("commit_sha_required_for", "commit_sha", ("image", "scan", "session", "tag-refusal")),
    ("denials_matrix_required_for", "denials_matrix", ("denials",)),
)

CAPTURE_TARGET_NAMES: Final = tuple(target.name for target in CAPTURE_TARGETS)

#: What ``--target`` defaults to: the standing-fact targets and not the run ones. A run
#: target needs a commit, and the commit is not something a default can pick — asking
#: for "everything" would otherwise mean asking for the last run somebody happened to
#: mention. Naming a run target is how a capture says which run it is about.
DEFAULT_CAPTURE_TARGET_NAMES: Final = ("roles", "repository")


# --------------------------------------------------------------------------------------
# Running one capture
# --------------------------------------------------------------------------------------


def write_record(path: Path, record: ContractModel) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = record.model_dump(mode="json", by_alias=True, exclude_none=False)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def require_target_arguments(targets: Sequence[str], context: CaptureContext) -> None:
    """Refuse a target whose input is missing, before anything reaches the account."""
    for reason, attribute, needing in TARGET_REQUIREMENTS:
        wanted = sorted(set(targets) & set(needing))
        if wanted and getattr(context, attribute) is None:
            raise CaptureFailedError(f"{reason}:{','.join(wanted)}")


def capture_phase1_evidence(
    *,
    aws_profile: str,
    aws_region: str,
    environment: EvidenceEnvironment,
    ecr_repository: str,
    targets: Sequence[str],
    output_dir: Path,
    base_dir: Path | None = None,
    commit_sha: str | None = None,
    base_image_digest: str | None = None,
    denials_matrix: PublisherDenialMatrix | None = None,
    lookup_since: datetime | None = None,
) -> CapturedEvidence:
    resolved = resolve_output_dir(output_dir, base_dir=base_dir)
    observed_at = datetime.now(tz=UTC).replace(microsecond=0)
    requirements = CaptureContext(
        aws_profile=aws_profile,
        aws_region=aws_region,
        environment=environment,
        ecr_repository=ecr_repository,
        identity=AccountIdentity(account_id="", partition="aws"),
        observed_at=observed_at,
        repo_root=project_root(),
        commit_sha=commit_sha,
        base_image_digest=base_image_digest,
        denials_matrix=denials_matrix,
        lookup_since=lookup_since,
    )
    require_target_arguments(targets, requirements)
    identity = read_identity(aws_profile=aws_profile, aws_region=aws_region)
    context = replace(requirements, identity=identity)
    records: list[CapturedRecord] = []
    for target in CAPTURE_TARGETS:
        if target.name in targets:
            records.extend(target.capture(context))
    # Written only once every target has answered. A capture that stopped halfway would
    # otherwise leave a directory that looks like a run and describes part of one.
    written: list[Path] = []
    for relative, record in records:
        path = resolved / relative
        write_record(path, record)
        written.append(path)
    return CapturedEvidence(
        roles=tuple(one for _, one in records if isinstance(one, DeployedRoleEvidence)),
        repository=next(
            (one for _, one in records if isinstance(one, EcrRepositoryEvidence)), None
        ),
        drift=tuple(one for _, one in records if isinstance(one, RoleDriftReport)),
        written=tuple(written),
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Capture Phase 1 AWS evidence.")
    parser.add_argument("--aws-profile", required=True)
    parser.add_argument("--aws-region", required=True)
    parser.add_argument("--environment", choices=["sandbox"], required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--registry", type=Path, default=None)
    parser.add_argument(
        "--target",
        action="append",
        choices=list(CAPTURE_TARGET_NAMES),
        default=None,
        help=(
            "capture only this target; may be repeated. Defaults to the standing-fact "
            "targets, because a run target has to be told which run it is about."
        ),
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--commit-sha",
        default=None,
        help="the published commit, for the image, scan and session targets.",
    )
    parser.add_argument(
        "--denials",
        type=Path,
        default=None,
        help=(
            "the publisher denial matrix the deny job wrote, for the denials target. "
            "This capture supplies the CloudTrail event id each attempt is missing."
        ),
    )
    parser.add_argument(
        "--lookup-since",
        default=None,
        help=(
            "how far back the CloudTrail lookups reach, as an ISO 8601 instant. "
            f"Defaults to {DEFAULT_LOOKUP_WINDOW.days} days before the capture."
        ),
    )
    return parser.parse_args(argv)


def read_denials_matrix(path: Path | None) -> PublisherDenialMatrix | None:
    """Load the matrix the deny job wrote, or report that it is not one.

    The contract requires every action in the matrix, in matrix order, so a run that
    refused four of the five is refused here rather than captured as though it had
    refused all five.
    """
    if path is None:
        return None
    try:
        return PublisherDenialMatrix.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise CaptureFailedError("denials_matrix_unreadable") from exc


def read_lookup_since(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise CaptureFailedError("lookup_since_unreadable") from exc
    if parsed.tzinfo is None:
        raise CaptureFailedError("lookup_since_unreadable")
    return parsed.astimezone(UTC)


def main(argv: Sequence[str] | None = None, *, base_dir: Path | None = None) -> int:
    arguments = parse_args(argv)
    registry_path = arguments.registry or (project_root() / DEFAULT_REGISTRY)
    try:
        registry = load_registry(registry_path)
    except RegistryUnreadableError as exc:
        print(exc.reason, file=sys.stderr)
        return 2
    try:
        registered = registry.repository_by_name(arguments.repository)
    except UnknownRepositoryError:
        print("unregistered_repository", file=sys.stderr)
        return 2

    try:
        captured = capture_phase1_evidence(
            aws_profile=arguments.aws_profile,
            aws_region=arguments.aws_region,
            environment=arguments.environment,
            ecr_repository=registered.ecr_repository,
            targets=arguments.target or DEFAULT_CAPTURE_TARGET_NAMES,
            output_dir=arguments.output_dir,
            base_dir=base_dir,
            commit_sha=arguments.commit_sha,
            # The base an image was built from is not something a registry API can be
            # asked. It is what this repository registered, and reading it here is what
            # ties the captured image to the base the build was required to use.
            base_image_digest=registered.base_image_digest,
            denials_matrix=read_denials_matrix(arguments.denials),
            lookup_since=read_lookup_since(arguments.lookup_since),
        )
    except (CaptureFailedError, PolicyNotComparableError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except (OSError, ValueError, ValidationError) as exc:
        # A capture that cannot be written down, or that produced something a contract
        # refuses, has established nothing. The message is this module's own text or a
        # contract's, neither of which quotes the account back.
        print(f"capture_unwritable:{type(exc).__name__}", file=sys.stderr)
        return 2

    for report in captured.drift:
        for finding in report.findings:
            print(
                f"role_drift:{report.role_name}:{finding.direction.value}:{finding.element}",
                file=sys.stderr,
            )
    findings = captured.drift_findings
    # The records are written either way — what drifted is the account, not the capture —
    # so the summary is printed for a failed comparison too. It carries the verdict so
    # that a reader of the summary alone is told, rather than being left to notice a
    # count and infer what it meant from the exit code.
    print(
        json.dumps(
            {
                "targets": list(arguments.target or DEFAULT_CAPTURE_TARGET_NAMES),
                "written": sorted(path.name for path in captured.written),
                "roles_compared": len(captured.drift),
                "drift_findings": findings,
                "verdict": "role_drift" if findings else "ok",
            },
            indent=2,
            sort_keys=True,
        )
    )
    if not findings:
        return 0
    print(
        f"capture_not_clean: {findings} finding{'' if findings == 1 else 's'} across "
        f"{len(captured.drift)} roles compared; the committed templates do not describe "
        "the account",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
