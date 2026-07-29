"""How IAM spells a policy document, and the names it allows inside one.

These types describe a trust policy, an inline policy and an attachment in a shape two
documents can be compared element by element. That comparison is
:mod:`edullm_platform.role_drift`, which is the only thing that reasons about them; what
they describe is an account fact rather than a phase's evidence, and Phase 3 compares
four more roles with the same vocabulary Phase 1 wrote it for.

They lived in :mod:`edullm_platform.phase1_evidence` because Phase 1 needed them first,
which put a vocabulary four modules read behind the name of the phase that happened to
write it. They are here rather than inside ``role_drift`` itself because
:class:`~edullm_platform.phase1_evidence.DeployedRoleEvidence` is built out of them and
``role_drift`` reads that record: one module cannot hold both halves without the two
importing each other.

**Flattened, not nested.** IAM writes conditions as a map of maps and a document as
nested objects. Every type here is the flat projection of one of those, because a
comparison over flat elements can say which condition went missing, and a comparison over
nested dictionaries can only say that two dictionaries differ.

**Able to describe a role nobody meant to deploy.** Actions accept wildcards, statements
may select by exclusion, and a trust statement may carry no conditions at all. None of
that appears in a template this repository commits, which is exactly why it is admitted:
a record that could only spell the intended shape would crash on the account state most
worth reporting instead of writing it down.

Strings are ``SecretFreeStr`` throughout. A condition value, a resource and a principal
identifier all hold whatever a policy author typed, and a policy author may have typed an
account ID.
"""

from __future__ import annotations

import json
import re
from typing import Annotated, Final, Literal

from pydantic import AfterValidator, BeforeValidator, Field

from edullm_platform.contracts.base import ContractModel, require_ordered_sequence
from edullm_platform.evidence import SecretFreeStr, scan_for_secrets

__all__ = [
    "IAM_CONDITION_OPERATOR_PATTERN",
    "IAM_NAME_PATTERN",
    "IAM_POLICY_ACTION_PATTERN",
    "IAM_POLICY_VERSION_PATTERN",
    "IamActionMatch",
    "IamAttachedPolicy",
    "IamConditionEntry",
    "IamConditionValue",
    "IamEffect",
    "IamInlinePolicy",
    "IamPermissionStatement",
    "IamPolicyAction",
    "IamPrincipal",
    "IamPrincipalMatch",
    "IamPrincipalType",
    "IamResourceMatch",
    "IamRoleName",
    "IamSessionName",
    "IamTrustStatement",
    "ManagedPolicyScope",
    "parse_condition_value",
    "validate_policy_action",
]

#: The characters IAM allows in a role name, and separately in a session name.
IAM_NAME_PATTERN: Final = r"^[A-Za-z0-9+=,.@_-]+$"
#: An action as a deployed policy spells it, wildcards included. A record that could
#: not hold ``ecr:*`` or ``*`` could not report the drift most worth reporting.
IAM_POLICY_ACTION_PATTERN: Final = r"^(?:\*|[a-z0-9-]{2,64}:[A-Za-z0-9*?]{1,128})$"
IAM_POLICY_VERSION_PATTERN: Final = r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$"
IAM_CONDITION_OPERATOR_PATTERN: Final = r"^(?:ForAllValues:|ForAnyValue:)?[A-Za-z]{2,48}$"

IamEffect = Literal["Allow", "Deny"]
IamPrincipalType = Literal["*", "AWS", "CanonicalUser", "Federated", "Service"]
#: Who manages an attached policy, in AWS's own terms: an AWS managed policy or a
#: customer managed one. Recorded because the two can share a name.
ManagedPolicyScope = Literal["aws", "customer"]

IamRoleName = Annotated[
    str,
    Field(min_length=1, max_length=64, pattern=IAM_NAME_PATTERN),
    AfterValidator(scan_for_secrets),
]
IamSessionName = Annotated[
    str,
    Field(min_length=2, max_length=64, pattern=IAM_NAME_PATTERN),
    AfterValidator(scan_for_secrets),
]


def validate_policy_action(value: str) -> str:
    if re.fullmatch(IAM_POLICY_ACTION_PATTERN, value) is None:
        raise ValueError("a policy action must be a service action or a wildcard")
    return value


#: An action as a deployed policy spells it. Applied per element rather than as a field
#: pattern so a failure names the action rather than the list it came from.
IamPolicyAction = Annotated[SecretFreeStr, AfterValidator(validate_policy_action)]


def parse_condition_value(value: object) -> object:
    """Spell a condition value the way a JSON policy document does.

    IAM's grammar makes quotation marks optional around numbers and booleans, so
    ``{"Bool": {"aws:SecureTransport": true}}`` is a policy IAM accepts and returns
    unquoted. The quoted and unquoted spellings mean the same thing to IAM, so the
    unquoted one is normalised rather than refused: refusing it would fail capture on a
    valid policy, and comparing punctuation against the template would report drift that
    is not there. A value that is not a JSON scalar is left alone for the field to refuse.
    """
    if isinstance(value, bool | int | float):
        return json.dumps(value, allow_nan=False)
    return value


#: One value of one condition, as the document spells it once numbers and booleans are
#: quoted. Scanned, because a condition value can hold anything a policy author typed.
IamConditionValue = Annotated[SecretFreeStr, BeforeValidator(parse_condition_value)]


class IamConditionEntry(ContractModel):
    """One condition an IAM statement carries, flattened to operator, key and values.

    IAM writes conditions as a map of maps. Flattened they compare element by element,
    so a comparison against the template can say which condition went missing rather
    than that two nested dictionaries differ.

    The operator is patterned, not enumerated. IAM has around thirty operators, each with
    an optional ``IfExists`` suffix and an optional ``ForAllValues:`` or ``ForAnyValue:``
    prefix, and a list of the ones these templates use would refuse the rest. IAM will
    not store an operator it does not recognise, so the pattern is the useful bound.
    """

    operator: SecretFreeStr = Field(pattern=IAM_CONDITION_OPERATOR_PATTERN)
    condition_key: SecretFreeStr = Field(min_length=1, max_length=256)
    values: Annotated[tuple[IamConditionValue, ...], BeforeValidator(require_ordered_sequence)] = (
        Field(min_length=1, strict=False)
    )


class IamActionMatch(ContractModel):
    """The actions a statement selects, and whether it selects them by exclusion.

    IAM's grammar offers ``Action`` or ``NotAction`` and never both or neither, and the
    two mean opposite things: ``NotAction`` with ``Allow`` permits every action that is
    not listed. Naming the element beside the list is what stops a reader or a comparison
    from taking one for the other, and the list is unreachable without passing the name.

    This exists because refusing the negated form would have made the record useless in
    the case it was built for. Neither committed template uses ``NotAction``, so a
    statement that has one is drift by construction, and drift is what this must describe.
    """

    element: Literal["Action", "NotAction"]
    actions: Annotated[tuple[IamPolicyAction, ...], BeforeValidator(require_ordered_sequence)] = (
        Field(min_length=1, strict=False)
    )


class IamResourceMatch(ContractModel):
    """The resources a statement selects, negated or not, on the terms above.

    ``NotResource`` with ``Allow`` reaches every resource except those listed, which is
    a wider grant than any spelling of ``Resource`` in either template.
    """

    element: Literal["Resource", "NotResource"]
    resources: Annotated[tuple[SecretFreeStr, ...], BeforeValidator(require_ordered_sequence)] = (
        Field(min_length=1, strict=False)
    )


class IamPrincipal(ContractModel):
    """Who a trust statement names.

    ``identifier`` is a name wherever one exists: the provider host for a federated
    principal, the service principal for a service. Where only an ARN exists it is
    recorded with its account ID redacted, which is also how the template spells it.
    """

    principal_type: IamPrincipalType
    identifier: SecretFreeStr = Field(min_length=1, max_length=2048)


class IamPrincipalMatch(ContractModel):
    """Who a trust statement admits, negated or not, on the terms above.

    ``NotPrincipal`` with ``Allow`` is the form IAM Access Analyzer reports as
    ``ALLOW_WITH_NOT_PRINCIPAL``, because it can admit anonymous callers. A trust policy
    edited by hand is where it would appear, and this record has to be able to say so.
    """

    element: Literal["Principal", "NotPrincipal"]
    principals: Annotated[tuple[IamPrincipal, ...], BeforeValidator(require_ordered_sequence)] = (
        Field(min_length=1, strict=False)
    )


class IamTrustStatement(ContractModel):
    """One statement of a deployed role's trust policy.

    ``conditions`` may be empty, and an empty tuple is a finding rather than a gap in
    the capture: a trust statement with no conditions admits its principal outright.

    No resource element: IAM refuses one in a role's trust policy.
    """

    sid: SecretFreeStr | None = Field(min_length=1, max_length=128)
    effect: IamEffect
    action_match: IamActionMatch
    principal_match: IamPrincipalMatch
    conditions: Annotated[
        tuple[IamConditionEntry, ...], BeforeValidator(require_ordered_sequence)
    ] = Field(strict=False)


class IamPermissionStatement(ContractModel):
    """One statement of a deployed role's inline policy.

    Both elements IAM requires are present and each names at least one value, so a
    capture that dropped a list fails here rather than recording a statement that grants
    less than the role does.
    """

    sid: SecretFreeStr | None = Field(min_length=1, max_length=128)
    effect: IamEffect
    action_match: IamActionMatch
    resource_match: IamResourceMatch
    conditions: Annotated[
        tuple[IamConditionEntry, ...], BeforeValidator(require_ordered_sequence)
    ] = Field(strict=False)


class IamInlinePolicy(ContractModel):
    """One inline policy on the role, by name and statement.

    ``policy_version`` is the document's ``Version`` element, which IAM's grammar makes
    optional; a document without one is evaluated as ``2008-10-17``. It is nullable so
    that absence can be written down, and required so an uncaptured version cannot be
    read as an absent one. Recording the default IAM would have applied would put a fact
    in the record that the account never returned.
    """

    policy_name: SecretFreeStr = Field(min_length=1, max_length=128, pattern=IAM_NAME_PATTERN)
    policy_version: SecretFreeStr | None = Field(pattern=IAM_POLICY_VERSION_PATTERN)
    statements: Annotated[
        tuple[IamPermissionStatement, ...], BeforeValidator(require_ordered_sequence)
    ] = Field(min_length=1, strict=False)


class IamAttachedPolicy(ContractModel):
    """One managed policy attached to the role, by name and by who manages it.

    A name alone does not identify a managed policy. ``arn:aws:iam::aws:policy/X`` and
    ``arn:aws:iam::<account>:policy/X`` are different policies with the same name, and a
    role may carry both, so a record of names alone refused that role outright and lost
    the distinction that matters most: the AWS-managed ``AdministratorAccess`` is the
    attachment worth noticing. ``scope`` is that distinction, and it is a name for the
    policy's owner rather than the account ID the ARN would carry.
    """

    policy_name: SecretFreeStr = Field(min_length=1, max_length=128, pattern=IAM_NAME_PATTERN)
    scope: ManagedPolicyScope
