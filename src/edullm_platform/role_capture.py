"""Reading one registry of roles out of the account and comparing each to its template.

:mod:`edullm_platform.role_drift` holds the comparison and knows nothing about AWS;
:mod:`edullm_platform.capture_tooling` holds the CLI and knows nothing about roles. This is
the join, and it is here rather than in a tool because the third caller was about to be a
third copy: Phase 1 walks two roles, Phase 3 walks four and the dataset validator's one, and
Phase 4 walks five.

What a caller supplies is a registry -- a tuple of ``(role_name, template_path)`` -- and
what it gets back is one sanitized record and one drift report per role, in the layout every
phase already commits. The registries stay separate, one per unit of work, so a role added
by one phase drifting cannot fail another phase's capture. What is shared is the walk.

**What this establishes, and the one thing it cannot.** A report with no findings says the
deployed role grants what the committed template says it grants. It does not say the grant
works: a condition keyed on something the action never puts in the request context is
present in both documents, identical in both, and unsatisfiable in the account. Only calling
the action settles that, which is why the templates that carry such a condition record the
call that settled it.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, Final

from edullm_platform.capture_tooling import CaptureFailedError, account_identity, aws_json
from edullm_platform.phase1_evidence import DeployedRoleEvidence
from edullm_platform.role_drift import (
    PolicyNotComparableError,
    RoleDriftReport,
    compare_role_to_template,
    load_template_roles,
    project_deployed_role,
    split_arn_fields,
)

__all__ = [
    "REPO_ROOT",
    "account_and_partition",
    "capture_role",
    "capture_roles",
]

#: The checkout this module reads committed templates from. A default rather than a required
#: argument, because every caller means this checkout and passing one would be a fourth
#: spelling of the same path.
REPO_ROOT: Final = Path(__file__).resolve().parents[2]


def account_and_partition(*, profile: str, region: str) -> tuple[str, str]:
    """The account this is running against, and its partition.

    Neither is ever written to a file. The account ID is what tells a captured ARN naming
    *this* account from one naming another, and the partition is what the drift comparison
    is allowed to fold. The partition is read here rather than by
    :func:`~edullm_platform.capture_tooling.account_identity`, because which spellings of a
    partition may be folded together is the drift comparison's question.
    """
    identity = account_identity(profile=profile, region=region)
    fields = split_arn_fields(identity.arn)
    if fields is None:
        raise CaptureFailedError("caller_identity_unreadable")
    return identity.account_id, fields[1]


def capture_role(
    role_name: str,
    *,
    profile: str,
    region: str,
    account_id: str,
    observed_at: datetime,
) -> DeployedRoleEvidence:
    """One role as IAM describes it, masked and narrowed to what a template can be compared
    against.
    """
    role = aws_json(
        ["iam", "get-role", "--role-name", role_name], profile=profile, region=region
    )["Role"]
    listed = aws_json(
        ["iam", "list-role-policies", "--role-name", role_name], profile=profile, region=region
    )
    inline_documents = [
        aws_json(
            ["iam", "get-role-policy", "--role-name", role_name, "--policy-name", policy_name],
            profile=profile,
            region=region,
        )
        for policy_name in listed.get("PolicyNames", [])
    ]
    attached = aws_json(
        ["iam", "list-attached-role-policies", "--role-name", role_name],
        profile=profile,
        region=region,
    )
    return project_deployed_role(
        role,
        inline_documents,
        attached.get("AttachedPolicies", []),
        own_account=account_id,
        environment="sandbox",
        observed_at=observed_at,
    )


def capture_roles(
    *,
    role_templates: Sequence[tuple[str, str]],
    profile: str,
    region: str,
    observed_at: datetime,
    repo_root: Path = REPO_ROOT,
) -> list[tuple[str, Any]]:
    """Each role in one registry, followed by how it compares to the template that declares
    it.

    The paths are relative and are the layout every phase's capture already writes: the
    sanitized record is what a reviewer copies into ``fixtures/``, and the drift report stays
    in the working directory because it is derived from the record beside it.

    A template this cannot project raises rather than producing an empty comparison. A
    projection that silently dropped a statement would report a role as matching a template
    it does not describe, which is the one answer worse than no answer.
    """
    account_id, partition = account_and_partition(profile=profile, region=region)
    records: list[tuple[str, Any]] = []
    for role_name, relative_path in role_templates:
        evidence = capture_role(
            role_name,
            profile=profile,
            region=region,
            account_id=account_id,
            observed_at=observed_at,
        )
        try:
            declared = [
                role
                for role in load_template_roles(repo_root / relative_path)
                if role.role_name == role_name
            ]
        except PolicyNotComparableError as exc:
            raise CaptureFailedError(f"template_unreadable:{relative_path}") from exc
        if len(declared) != 1:
            raise CaptureFailedError(f"template_does_not_declare_the_role:{relative_path}")
        report: RoleDriftReport = compare_role_to_template(
            evidence,
            declared[0],
            template_path=relative_path,
            partition=partition,
            region=region,
        )
        records.append((f"sanitized/roles/{role_name}.sanitized.json", evidence))
        records.append((f"drift/{role_name}.json", report))
    return records
