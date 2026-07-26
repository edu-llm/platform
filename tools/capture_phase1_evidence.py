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
and the command iterates the registry rather than naming targets of its own. The three
records Phase 1 still needs are captured by adding entries to it:

``image``
    ``EcrImageEvidence``, from ``ecr describe-images --image-ids imageDigest=…`` for the
    digest, tag and push time, joined to the base image digest that
    ``tools/read_image_config_digest.py`` already reads. Needs a completed publish run,
    because there is no digest to ask about until one exists.
``scan``
    ``ImageScanEvidence``, from ``ecr describe-image-scan-findings`` for the same digest.
    The repository is created with ``ScanOnPush``, so the scan exists as soon as the
    image does.
``session``
    ``OidcSessionEvidence``, from ``cloudtrail lookup-events`` filtered to
    ``AssumeRoleWithWebIdentity``. This is the call whose raw record carries the ``ASIA``
    key id, and the fields to select are the event id, the role and session names, the
    three OIDC claims, and the two instants — nothing else.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from pydantic import ValidationError

from edullm_platform.build_tooling import RegistryUnreadableError, load_registry
from edullm_platform.contracts.base import ContractModel
from edullm_platform.contracts.repository_registry import UnknownRepositoryError
from edullm_platform.evidence import EvidenceEnvironment
from edullm_platform.phase1_evidence import (
    DeployedRoleEvidence,
    EcrLifecycleRule,
    EcrRepositoryEvidence,
)
from edullm_platform.publisher_denials import parse_aws_cli_error
from edullm_platform.role_drift import (
    COMMITTED_ROLE_TEMPLATES,
    PolicyNotComparableError,
    RoleDriftReport,
    compare_role_to_template,
    iam_policy_from_arn,
    load_template_roles,
    read_inline_policy,
    read_trust_statements,
    redact_account_ids_in_document,
    split_arn_fields,
)

#: Captured evidence is local-only until somebody has read it and copied the part they
#: want into ``fixtures/``. Writing anywhere else is refused rather than discouraged.
ALLOWED_OUTPUT_SUFFIX: Final = Path("docs-frank/working/phase-1-evidence")
DEFAULT_REGISTRY: Final = Path("config/repositories.yaml")

#: How long one AWS call may take before the answer stops being worth waiting for.
AWS_CALL_TIMEOUT_SECONDS: Final = 60


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


def project_deployed_role(
    role: Mapping[str, Any],
    inline_documents: Sequence[Mapping[str, Any]],
    attached: Sequence[Mapping[str, Any]],
    *,
    context: CaptureContext,
) -> DeployedRoleEvidence:
    """One role as IAM described it, masked and narrowed to what a template can be
    compared against.

    Read and dropped on purpose: the role's own ARN, its path, its role ID, its tags, its
    description, and its creation and last-used dates. None of them is comparable to
    anything this repository commits, and the ARN is the account ID with a name attached.
    """
    masked = redact_account_ids_in_document(dict(role), own_account=context.identity.account_id)
    boundary = masked.get("PermissionsBoundary")
    boundary_name: str | None = None
    if isinstance(boundary, dict):
        boundary_name, _scope = iam_policy_from_arn(
            str(boundary.get("PermissionsBoundaryArn")), what="PermissionsBoundary"
        )
    trust_document = masked.get("AssumeRolePolicyDocument")
    attached_policies = redact_account_ids_in_document(
        list(attached), own_account=context.identity.account_id
    )
    return DeployedRoleEvidence.model_validate(
        {
            "source": "aws",
            "environment": context.environment,
            "status": "ok",
            "observed_at": context.observed_at,
            "role_name": masked.get("RoleName"),
            "permissions_boundary_policy_name": boundary_name,
            "max_session_duration_seconds": masked.get("MaxSessionDuration"),
            "trust_policy_version": (
                trust_document.get("Version") if isinstance(trust_document, dict) else None
            ),
            "trust_statements": read_trust_statements(trust_document),
            "inline_policies": [
                read_inline_policy(
                    redact_account_ids_in_document(
                        dict(document), own_account=context.identity.account_id
                    )
                )
                for document in inline_documents
            ],
            "attached_managed_policies": [
                {
                    "policy_name": policy.get("PolicyName"),
                    # The name comes from IAM's own field and the scope from the ARN,
                    # because the ARN's name may carry a path and its owner field is the
                    # only thing that says who manages the policy.
                    "scope": iam_policy_from_arn(
                        str(policy.get("PolicyArn")), what="AttachedPolicies entry"
                    )[1],
                }
                for policy in attached_policies
            ],
        }
    )


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
        context=context,
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


CAPTURE_TARGETS: Final[tuple[CaptureTarget, ...]] = (
    CaptureTarget(name="roles", capture=capture_roles),
    CaptureTarget(name="repository", capture=capture_repository),
)

CAPTURE_TARGET_NAMES: Final = tuple(target.name for target in CAPTURE_TARGETS)


# --------------------------------------------------------------------------------------
# Running one capture
# --------------------------------------------------------------------------------------


def write_record(path: Path, record: ContractModel) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = record.model_dump(mode="json", by_alias=True, exclude_none=False)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def capture_phase1_evidence(
    *,
    aws_profile: str,
    aws_region: str,
    environment: EvidenceEnvironment,
    ecr_repository: str,
    targets: Sequence[str],
    output_dir: Path,
    base_dir: Path | None = None,
) -> CapturedEvidence:
    resolved = resolve_output_dir(output_dir, base_dir=base_dir)
    identity = read_identity(aws_profile=aws_profile, aws_region=aws_region)
    context = CaptureContext(
        aws_profile=aws_profile,
        aws_region=aws_region,
        environment=environment,
        ecr_repository=ecr_repository,
        identity=identity,
        observed_at=datetime.now(tz=UTC).replace(microsecond=0),
        repo_root=project_root(),
    )
    records: list[CapturedRecord] = []
    for target in CAPTURE_TARGETS:
        if target.name in targets:
            records.extend(target.capture(context))
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
        help="capture only this target; may be repeated. Defaults to every target.",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args(argv)


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
            targets=arguments.target or CAPTURE_TARGET_NAMES,
            output_dir=arguments.output_dir,
            base_dir=base_dir,
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
                "targets": list(arguments.target or CAPTURE_TARGET_NAMES),
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
