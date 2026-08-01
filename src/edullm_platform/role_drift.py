"""Comparing a role the account holds against the CloudFormation template for it.

Both Phase 1 roles were created once from a laptop, and neither is redeployed by CI.
``tests/test_phase1_infrastructure.py`` pins what the templates say, and nothing until
now read the account, so a policy widened in the console left every test in this
repository green. This module is the missing half: it projects a committed template into
the same shape :class:`~edullm_platform.phase1_evidence.DeployedRoleEvidence` records,
and reports every way the two disagree.

**Direction, and why both directions are reported.** A deployed role that grants more
than its template is a security finding. One that grants less is not — it is a role that
will fail a push nobody expected it to fail. Only the first is dangerous, but both mean
the committed template has stopped describing the account, so every finding carries a
:class:`DriftDirection` and none of them passes silently. ``CHANGED`` is the third
answer, for a difference with no direction: an edited condition value, a renamed
boundary, a statement that selects by exclusion where the template selects by inclusion.

**The normalisation, which is the part worth reading twice.** A template spells a
resource ``arn:${AWS::Partition}:ecr:${AWS::Region}:${AWS::AccountId}:repository/x`` and
the account returns ``arn:aws:ecr:us-east-1:123456789012:repository/x``, which capture
then redacts. Something has to reconcile the two, and a normalisation that is too eager
would make a role that reaches every repository in another account compare equal to one
that reaches a single repository in this one.

So the folding is deliberately mean. It is positional: an ARN is split into its six
fields and only the partition, region and account fields are ever touched. It is exact:
a field is folded only when it holds precisely the pseudo-parameter, or precisely the
partition and region the caller named. Anything else — another region, another partition,
another account, any wildcard, and every character of the resource — survives untouched
and is therefore still visible to the comparison. A substitution the folding does not
recognise raises :class:`PolicyNotComparableError` rather than being guessed at or
compared literally.

The account is the one field that cannot be compared on its own terms, because the
secret scan refuses a raw account ID and capture has to mask it before a record can hold
it. A single mask would make a cross-account grant indistinguishable from a local one,
which is the widening most worth catching. :func:`redact_account_in_arn` is what stops
that: it masks this account and any other account to *different* placeholders, and only
the former is folded.

**Statement order is not reported.** IAM evaluates the statements of a document as a set,
so a reordered document grants exactly what the template grants. A reorder alone produces
no finding; a reorder that also changes a statement produces the finding it deserves.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Final

import yaml
from pydantic import BeforeValidator, Field, computed_field

from edullm_platform.contracts.base import (
    ContractModel,
    parse_str_enum,
    require_ordered_sequence,
)
from edullm_platform.evidence import (
    AWS_ACCOUNT_ID_PLACEHOLDER,
    SecretFreeStr,
    redact_aws_account_ids,
)
from edullm_platform.iam_documents import (
    IAM_NAME_PATTERN,
    IamActionMatch,
    IamAttachedPolicy,
    IamConditionEntry,
    IamEffect,
    IamInlinePolicy,
    IamPermissionStatement,
    IamPrincipal,
    IamPrincipalMatch,
    IamResourceMatch,
    IamRoleName,
    IamTrustStatement,
    ManagedPolicyScope,
    parse_condition_value,
)
from edullm_platform.phase1_evidence import DeployedRoleEvidence

__all__ = [
    "COMMITTED_ROLE_TEMPLATES",
    "DATASET_VALIDATOR_ROLE_TEMPLATES",
    "EVIDENCE_ONLY_ROLE_FIELDS",
    "FOREIGN_ACCOUNT_PLACEHOLDER",
    "INFRA_DEPLOYER_ROLE_NAME",
    "PHASE3_ROLE_TEMPLATES",
    "PHASE5_ROLE_TEMPLATES",
    "PUBLISHER_ROLE_NAME",
    "DriftDirection",
    "PolicyNotComparableError",
    "RoleDriftFinding",
    "RoleDriftReport",
    "TemplateRole",
    "compare_role_to_template",
    "iam_policy_from_arn",
    "load_template_roles",
    "normalize_policy_string",
    "project_deployed_role",
    "project_template_role",
    "read_inline_policy",
    "read_trust_statements",
    "redact_account_ids_in_document",
    "redact_account_in_arn",
    "split_arn_fields",
]

#: The two roles by name. Named separately from the table below because several callers
#: ask whether some identity is one of them, and an index into a tuple is a different
#: bug every time the tuple is reordered.
PUBLISHER_ROLE_NAME: Final = "sbsandbox-intern-edullm-ecr-publisher"
INFRA_DEPLOYER_ROLE_NAME: Final = "sbsandbox-intern-edullm-infra-deployer"

#: Every role this repository commits a template for, and the template that declares it.
#: The capture tool and the drift report iterate over this, so a role added to
#: ``infra/iam/`` and not added here would be captured and never compared to anything.
COMMITTED_ROLE_TEMPLATES: Final[tuple[tuple[str, str], ...]] = (
    (PUBLISHER_ROLE_NAME, "infra/iam/ecr-publisher-role.yaml"),
    (INFRA_DEPLOYER_ROLE_NAME, "infra/iam/infra-deployer-role.yaml"),
)

#: The four roles Phase 3 adds, and the committed templates that declare them.
#:
#: **Separate from the tuple above, and Phase 3 nearly got this wrong.** The obvious move is
#: to append to ``COMMITTED_ROLE_TEMPLATES``, which reads as "one registry, everything in
#: it". ``phase2_evidence.PHASE2_ROLE_TEMPLATES`` already declined to, with the reason
#: written beside it: those two names belong to a different phase's evidence and a different
#: phase's freshness window, and folding a later phase's roles into them would make a Phase
#: 1 capture fail because a Phase 3 role drifted. The Phase 1 proof bundle also counts that
#: tuple -- "roles compared to their template" is a number in its README -- so appending
#: would change a committed golden for a reason that has nothing to do with Phase 1.
#:
#: So the shape is one registry per phase and one drift comparison per phase, which is what
#: the two phases before this one already do. ``phase3_evidence`` is where the Phase 3
#: capture reads this from; it lives here rather than there because the comparison machinery
#: is here and because this is the file a reader checks when asking "is this role compared
#: to anything at all".
#:
#: The two roles Phase 3 *amends* rather than creates are not repeated here. The deployer is
#: in the tuple above and the admission states role is in ``PHASE2_ROLE_TEMPLATES``; both are
#: compared where they already were, which is the point of amending a template rather than
#: writing a new one.
PHASE3_ROLE_TEMPLATES: Final[tuple[tuple[str, str], ...]] = (
    ("sbsandbox-intern-edullm-batch-execution", "infra/iam/batch-roles.yaml"),
    ("sbsandbox-intern-edullm-batch-workload", "infra/iam/batch-roles.yaml"),
    ("sbsandbox-intern-edullm-batch-instance", "infra/iam/batch-roles.yaml"),
    ("sbsandbox-intern-edullm-lifecycle-lambda", "infra/iam/lifecycle-lambda-role.yaml"),
)

#: The role Phase 5 adds so a submission can read which image a commit published, and the
#: committed template that declares it.
#:
#: A tuple of its own for the reason written above ``PHASE3_ROLE_TEMPLATES``, which is worth
#: reading rather than restating: one registry per phase, because the Phase 1 proof bundle
#: counts ``COMMITTED_ROLE_TEMPLATES`` in its README, and appending here would change a
#: committed golden for a reason that has nothing to do with Phase 1.
PHASE5_ROLE_TEMPLATES: Final[tuple[tuple[str, str], ...]] = (
    ("sbsandbox-intern-edullm-image-resolver", "infra/iam/image-resolver-role.yaml"),
)

#: The role a dataset owner's own validator runs as, instead of our shared CPU workload
#: role, and the committed template that declares it.
#:
#: A tuple of its own, for the reason written above ``PHASE3_ROLE_TEMPLATES``: one registry
#: per unit of work, because the Phase 1 proof bundle counts ``COMMITTED_ROLE_TEMPLATES`` in
#: its README and appending here would move a committed golden for a reason that has nothing
#: to do with Phase 1.
#:
#: Deliberately NOT in ``team_isolation.WORKLOAD_ROLE_TEMPLATES``. That registry is the set
#: of roles an untrusted command of *ours* runs as, and every check over it is about the
#: ``teams/{team}/runs/`` prefix shape -- which this role exists to reach outside of. The
#: role's name not ending in ``-workload`` is what keeps the glob in
#: ``tests/test_phase5_team_isolation.py`` from conscripting it.
DATASET_VALIDATOR_ROLE_TEMPLATES: Final[tuple[tuple[str, str], ...]] = (
    ("sbsandbox-intern-edullm-dataset-validator", "infra/iam/dataset-validator-role.yaml"),
)

#: What ``DeployedRoleEvidence`` carries that a template projection cannot: the evidence
#: envelope. Derived rather than restated, so a field added to the evidence record has to
#: be either comparable or explicitly excluded here.
EVIDENCE_ONLY_ROLE_FIELDS: Final = frozenset({"source", "environment", "status", "observed_at"})

#: An account that is not this one, masked so it stays distinguishable from this one.
#: ``scan_for_secrets`` accepts it because it holds no digits, and the folding below
#: deliberately does not recognise it, so a cross-account grant survives to be reported.
FOREIGN_ACCOUNT_PLACEHOLDER: Final = "<other-aws-account-id>"

#: What the three folded ARN fields become. Distinct words rather than a shared token, so
#: a partition that ended up in the region field could not compare equal to a region.
PARTITION_PLACEHOLDER: Final = "<partition>"
REGION_PLACEHOLDER: Final = "<region>"
ACCOUNT_PLACEHOLDER: Final = "<account>"

#: What CloudFormation applies when a role template does not ask for a session length.
#: Recorded on the projection because it is what the service would deploy; the captured
#: record never infers it, because IAM always returns a value.
DEFAULT_MAX_SESSION_DURATION_SECONDS: Final = 3600

TEMPLATE_PARTITION: Final = "${AWS::Partition}"
TEMPLATE_REGION: Final = "${AWS::Region}"
TEMPLATE_ACCOUNT: Final = "${AWS::AccountId}"

SUBSTITUTION_OPEN: Final = "${"
SUBSTITUTION_CLOSE: Final = "}"
ARN_FIELD_COUNT: Final = 6
AWS_ACCOUNT_ID: Final = re.compile(r"^[0-9]{12}$")
IAM_POLICY_RESOURCE: Final = re.compile(r"^policy/(?P<name>.+)$")


class PolicyNotComparableError(ValueError):
    """The template cannot be projected into something a deployed role compares against.

    Raised rather than guessed at. Every alternative — resolving a ``Ref`` to whatever it
    probably means, comparing an unresolved ``${AWS::URLSuffix}`` as a literal — produces
    a projection that either compares clean against a role it does not describe, or
    reports drift that is not there. Both are worse than saying so.
    """


class DriftDirection(StrEnum):
    """Which way a deployed role differs from the template that should describe it."""

    #: The deployed role grants something the template does not. A security finding.
    WIDER = "wider"
    #: The deployed role grants less than the template. Not a security finding, and still
    #: a finding: the committed template has stopped describing the account.
    NARROWER = "narrower"
    #: A difference with no direction, such as an edited condition value or a renamed
    #: boundary. Neither side is a superset of the other.
    CHANGED = "changed"


DriftDirectionValue = Annotated[DriftDirection, BeforeValidator(parse_str_enum(DriftDirection))]


class RoleDriftFinding(ContractModel):
    """One way a deployed role and its template disagree.

    ``element`` names the part of the role, precisely enough to find by hand: an inline
    policy by name, a statement by its position within it. ``detail`` says what differs.
    Neither ever carries an account ID: the values compared here have already been
    through :func:`redact_account_in_arn`, and the field type refuses them regardless.
    """

    direction: DriftDirectionValue
    element: SecretFreeStr = Field(min_length=1, max_length=256)
    detail: SecretFreeStr = Field(min_length=1, max_length=2048)


class RoleDriftReport(ContractModel):
    """Everything one comparison found, or that it found nothing.

    ``matches`` is computed rather than recorded, so a report cannot claim agreement and
    carry findings at the same time.
    """

    role_name: IamRoleName
    template_path: SecretFreeStr = Field(min_length=1, max_length=512)
    findings: Annotated[tuple[RoleDriftFinding, ...], BeforeValidator(require_ordered_sequence)] = (
        Field(strict=False)
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def matches(self) -> bool:
        return not self.findings


class TemplateRole(ContractModel):
    """A committed ``AWS::IAM::Role`` in the shape a captured role is recorded in.

    Field for field the comparable half of ``DeployedRoleEvidence``, which is checked by
    a test rather than by eye: the two field sets differ by exactly
    :data:`EVIDENCE_ONLY_ROLE_FIELDS`. The nested contracts are the evidence module's own,
    so a statement cannot mean one thing on one side of the comparison and another on the
    other.

    Strings are kept as the template spells them, ``${AWS::Partition}`` and all. The
    folding happens during comparison, to both sides, through one function.
    """

    role_name: IamRoleName
    permissions_boundary_policy_name: SecretFreeStr | None = Field(
        min_length=1,
        max_length=128,
        pattern=IAM_NAME_PATTERN,
    )
    max_session_duration_seconds: int = Field(ge=0)
    trust_policy_version: SecretFreeStr | None = Field(min_length=1, max_length=32)
    trust_statements: Annotated[
        tuple[IamTrustStatement, ...], BeforeValidator(require_ordered_sequence)
    ] = Field(min_length=1, strict=False)
    inline_policies: Annotated[
        tuple[IamInlinePolicy, ...], BeforeValidator(require_ordered_sequence)
    ] = Field(strict=False)
    attached_managed_policies: Annotated[
        tuple[IamAttachedPolicy, ...], BeforeValidator(require_ordered_sequence)
    ] = Field(strict=False)


# --------------------------------------------------------------------------------------
# Reading an ARN written either way round
# --------------------------------------------------------------------------------------


def split_arn_fields(value: str) -> list[str] | None:
    """The six fields of an ARN, or ``None`` if this is not one.

    Colons cannot simply be split on, because ``${AWS::Partition}`` contains two of them
    and splitting there would put the partition's second half in the service field. So
    the scan tracks whether it is inside a substitution and only breaks outside one,
    which is what lets the template's spelling and the account's spelling be read by the
    same positional rules.
    """
    fields: list[str] = []
    current: list[str] = []
    depth = 0
    index = 0
    while index < len(value):
        if value.startswith(SUBSTITUTION_OPEN, index):
            depth += 1
            current.append(SUBSTITUTION_OPEN)
            index += len(SUBSTITUTION_OPEN)
            continue
        character = value[index]
        if character == SUBSTITUTION_CLOSE and depth:
            depth -= 1
        elif character == ":" and depth == 0 and len(fields) < ARN_FIELD_COUNT - 1:
            fields.append("".join(current))
            current = []
            index += 1
            continue
        current.append(character)
        index += 1
    fields.append("".join(current))
    if depth or len(fields) != ARN_FIELD_COUNT or fields[0] != "arn":
        return None
    return fields


# --------------------------------------------------------------------------------------
# Masking an account before an ARN can be recorded
# --------------------------------------------------------------------------------------


def redact_account_in_arn(value: str, *, own_account: str) -> str:
    """Mask account IDs in a captured string, keeping this account distinguishable.

    The generic :func:`~edullm_platform.evidence.redact_aws_account_ids` masks every
    twelve-digit run to one placeholder, which is right for free text and wrong here: a
    resource ARN naming somebody else's account would come out looking exactly like one
    naming ours, and the comparison would fold it away as though it matched the template.

    So the account field of an ARN is masked by whether it is ours, and everything else —
    including any account ID buried in the resource portion, and any string that is not an
    ARN at all — goes through the generic mask afterwards as a backstop.
    """
    fields = split_arn_fields(value)
    if fields is not None and AWS_ACCOUNT_ID.fullmatch(fields[4]):
        fields[4] = (
            AWS_ACCOUNT_ID_PLACEHOLDER if fields[4] == own_account else FOREIGN_ACCOUNT_PLACEHOLDER
        )
        value = ":".join(fields)
    return redact_aws_account_ids(value)


def redact_account_ids_in_document(document: Any, *, own_account: str) -> Any:
    """Mask every string in a captured policy document, leaving its shape alone.

    Applied to what IAM returned before anything reads it, so the readers below work on
    the same values whether they came from a template or from the account. Keys are left
    as they are: a condition key, a principal type and an element name are names IAM
    defines, and none of them can hold an account ID.
    """
    if isinstance(document, str):
        return redact_account_in_arn(document, own_account=own_account)
    if isinstance(document, dict):
        return {
            key: redact_account_ids_in_document(value, own_account=own_account)
            for key, value in document.items()
        }
    if isinstance(document, list):
        return [
            redact_account_ids_in_document(value, own_account=own_account) for value in document
        ]
    return document


# --------------------------------------------------------------------------------------
# Normalising the two spellings of one resource
# --------------------------------------------------------------------------------------


def _fold_field(value: str, *, accepted: Sequence[str], placeholder: str) -> str:
    return placeholder if value in accepted else value


def normalize_policy_string(value: str, *, partition: str, region: str) -> str:
    """Fold the three ARN fields a template cannot spell, and nothing else.

    ``value`` may be written either way round: the template's pseudo-parameters or the
    account's expanded and redacted values. Both fold to the same thing, and only when
    the field holds exactly one of the accepted values for its own position. A string
    that is not a six-field ARN is returned unchanged.

    Raises :class:`PolicyNotComparableError` if a substitution survives, which means the
    template used one this does not understand, or used one somewhere it is not folded.
    """
    fields = split_arn_fields(value)
    if fields is not None:
        fields[1] = _fold_field(
            fields[1],
            accepted=(TEMPLATE_PARTITION, partition),
            placeholder=PARTITION_PLACEHOLDER,
        )
        # An empty region is IAM's own spelling for a global resource and stays empty on
        # both sides. Folding it would let a global ARN compare equal to a regional one.
        if fields[3] != "":
            fields[3] = _fold_field(
                fields[3],
                accepted=(TEMPLATE_REGION, region),
                placeholder=REGION_PLACEHOLDER,
            )
        fields[4] = _fold_field(
            fields[4],
            accepted=(TEMPLATE_ACCOUNT, AWS_ACCOUNT_ID_PLACEHOLDER),
            placeholder=ACCOUNT_PLACEHOLDER,
        )
        value = ":".join(fields)
    if SUBSTITUTION_OPEN in value:
        raise PolicyNotComparableError(
            f"a substitution this comparison does not understand survived normalisation: {value!r}"
        )
    return value


# --------------------------------------------------------------------------------------
# Projecting a committed template
# --------------------------------------------------------------------------------------


def _template_string(value: object, *, what: str) -> str:
    """Read one template value that has to resolve to a string without an account.

    ``Fn::Sub`` with a plain string is the only intrinsic accepted, because it is the only
    one whose result is knowable from the document alone. A ``Fn::Sub`` carrying a
    variable map, a ``Ref``, a ``Fn::Join`` or a ``Fn::If`` resolves against a stack this
    module cannot see.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, dict) and set(value) == {"Fn::Sub"}:
        substituted = value["Fn::Sub"]
        if isinstance(substituted, str):
            return substituted
    raise PolicyNotComparableError(f"{what} is not a literal or a plain Fn::Sub: {value!r}")


def _template_strings(value: object, *, what: str) -> tuple[str, ...]:
    if isinstance(value, list):
        return tuple(_template_string(item, what=what) for item in value)
    return (_template_string(value, what=what),)


def _condition_values(value: object, *, what: str) -> tuple[str, ...]:
    """Condition values, with IAM's optional quotation marks put back on.

    ``{"Bool": {"aws:SecureTransport": true}}`` is a policy IAM accepts and returns
    unquoted, and it means the same thing as the quoted spelling. Both sides go through
    the same normalisation the contract applies, so a template that quotes and an account
    that does not cannot report as drift.
    """
    items = value if isinstance(value, list) else [value]
    return tuple(_template_string(parse_condition_value(item), what=what) for item in items)


def _optional_sid(statement: Mapping[str, Any], *, what: str) -> str | None:
    sid = statement.get("Sid")
    return None if sid is None else _template_string(sid, what=f"{what} Sid")


def _conditions(statement: Mapping[str, Any], *, what: str) -> tuple[IamConditionEntry, ...]:
    condition = statement.get("Condition", {})
    if not isinstance(condition, dict):
        raise PolicyNotComparableError(f"{what} Condition is not a map: {condition!r}")
    entries: list[IamConditionEntry] = []
    for operator, keyed in sorted(condition.items()):
        if not isinstance(operator, str) or not isinstance(keyed, dict):
            raise PolicyNotComparableError(f"{what} Condition is not a map of maps")
        for condition_key, values in sorted(keyed.items()):
            entries.append(
                IamConditionEntry(
                    operator=operator,
                    condition_key=condition_key,
                    values=_condition_values(values, what=f"{what} Condition value"),
                )
            )
    return tuple(entries)


def _action_match(statement: Mapping[str, Any], *, what: str) -> IamActionMatch:
    for element in ("Action", "NotAction"):
        if element in statement:
            return IamActionMatch(
                element=element,
                actions=_template_strings(statement[element], what=f"{what} {element}"),
            )
    raise PolicyNotComparableError(f"{what} names neither Action nor NotAction")


def _resource_match(statement: Mapping[str, Any], *, what: str) -> IamResourceMatch:
    for element in ("Resource", "NotResource"):
        if element in statement:
            return IamResourceMatch(
                element=element,
                resources=_template_strings(statement[element], what=f"{what} {element}"),
            )
    raise PolicyNotComparableError(f"{what} names neither Resource nor NotResource")


def _principals(value: object, *, what: str) -> tuple[IamPrincipal, ...]:
    if value == "*":
        return (IamPrincipal(principal_type="*", identifier="*"),)
    if not isinstance(value, dict):
        raise PolicyNotComparableError(f"{what} is not a principal map: {value!r}")
    principals: list[IamPrincipal] = []
    for principal_type, identifiers in sorted(value.items()):
        for identifier in _template_strings(identifiers, what=f"{what} {principal_type}"):
            principals.append(IamPrincipal(principal_type=principal_type, identifier=identifier))
    return tuple(principals)


def _principal_match(statement: Mapping[str, Any], *, what: str) -> IamPrincipalMatch:
    for element in ("Principal", "NotPrincipal"):
        if element in statement:
            return IamPrincipalMatch(
                element=element,
                principals=_principals(statement[element], what=f"{what} {element}"),
            )
    raise PolicyNotComparableError(f"{what} names neither Principal nor NotPrincipal")


def _effect(statement: Mapping[str, Any], *, what: str) -> IamEffect:
    effect = statement.get("Effect")
    if effect == "Allow":
        return "Allow"
    if effect == "Deny":
        return "Deny"
    raise PolicyNotComparableError(f"{what} Effect is not Allow or Deny: {effect!r}")


def _statements(document: object, *, what: str) -> list[Mapping[str, Any]]:
    if not isinstance(document, dict):
        raise PolicyNotComparableError(f"{what} is not a policy document: {document!r}")
    statements = document.get("Statement")
    if not isinstance(statements, list) or not statements:
        raise PolicyNotComparableError(f"{what} has no Statement list")
    for statement in statements:
        if not isinstance(statement, dict):
            raise PolicyNotComparableError(f"{what} holds a statement that is not a map")
    return statements


def _policy_version(document: Mapping[str, Any], *, what: str) -> str | None:
    version = document.get("Version")
    return None if version is None else _template_string(version, what=f"{what} Version")


def read_trust_statements(document: object) -> tuple[IamTrustStatement, ...]:
    """Read a trust policy document, whether a template wrote it or IAM returned it.

    IAM's own grammar is what both sides speak, so one reader serves both and a statement
    cannot mean one thing in the template and another in the account. The capture tool
    passes what IAM returned through :func:`redact_account_ids_in_document` first, which
    is the only difference between the two callers.
    """
    what = "AssumeRolePolicyDocument"
    return tuple(
        IamTrustStatement(
            sid=_optional_sid(statement, what=f"{what} statement {index}"),
            effect=_effect(statement, what=f"{what} statement {index}"),
            action_match=_action_match(statement, what=f"{what} statement {index}"),
            principal_match=_principal_match(statement, what=f"{what} statement {index}"),
            conditions=_conditions(statement, what=f"{what} statement {index}"),
        )
        for index, statement in enumerate(_statements(document, what=what), start=1)
    )


def read_inline_policy(policy: object) -> IamInlinePolicy:
    """Read one inline policy from ``PolicyName`` and ``PolicyDocument``.

    Those are the keys CloudFormation uses under ``Policies`` and the keys IAM answers
    ``get-role-policy`` with, so one reader serves both sides for the same reason
    :func:`read_trust_statements` does.
    """
    if not isinstance(policy, dict):
        raise PolicyNotComparableError(f"an inline policy is not a map: {policy!r}")
    name = _template_string(policy.get("PolicyName"), what="PolicyName")
    document = policy.get("PolicyDocument")
    what = f"inline policy {name!r}"
    statements = _statements(document, what=what)
    assert isinstance(document, dict)
    return IamInlinePolicy(
        policy_name=name,
        policy_version=_policy_version(document, what=what),
        statements=tuple(
            IamPermissionStatement(
                sid=_optional_sid(statement, what=f"{what} statement {index}"),
                effect=_effect(statement, what=f"{what} statement {index}"),
                action_match=_action_match(statement, what=f"{what} statement {index}"),
                resource_match=_resource_match(statement, what=f"{what} statement {index}"),
                conditions=_conditions(statement, what=f"{what} statement {index}"),
            )
            for index, statement in enumerate(statements, start=1)
        ),
    )


def iam_policy_from_arn(arn: str, *, what: str) -> tuple[str, ManagedPolicyScope]:
    """Read a managed policy's name and who manages it out of its ARN.

    A name alone does not identify a managed policy: ``arn:aws:iam::aws:policy/X`` and
    ``arn:aws:iam::<account>:policy/X`` are different policies. The owner field says
    which, and it is recorded as a scope rather than as the account it would otherwise be.
    """
    fields = split_arn_fields(arn)
    resource = None if fields is None else IAM_POLICY_RESOURCE.fullmatch(fields[5])
    if fields is None or fields[2] != "iam" or resource is None:
        raise PolicyNotComparableError(f"{what} is not an IAM policy ARN: {arn!r}")
    scope: ManagedPolicyScope = "aws" if fields[4] == "aws" else "customer"
    # An IAM policy ARN is a path followed by a name; the name is the last segment.
    path_and_name: str = resource.group("name")
    return path_and_name.rsplit("/", 1)[-1], scope


def _permissions_boundary(properties: Mapping[str, Any]) -> str | None:
    boundary = properties.get("PermissionsBoundary")
    if boundary is None:
        return None
    arn = _template_string(boundary, what="PermissionsBoundary")
    name, _scope = iam_policy_from_arn(arn, what="PermissionsBoundary")
    return name


def _attached_managed_policies(properties: Mapping[str, Any]) -> tuple[IamAttachedPolicy, ...]:
    arns = properties.get("ManagedPolicyArns", [])
    if not isinstance(arns, list):
        raise PolicyNotComparableError(f"ManagedPolicyArns is not a list: {arns!r}")
    attached: list[IamAttachedPolicy] = []
    for arn in arns:
        name, scope = iam_policy_from_arn(
            _template_string(arn, what="ManagedPolicyArns entry"),
            what="ManagedPolicyArns entry",
        )
        attached.append(IamAttachedPolicy(policy_name=name, scope=scope))
    return tuple(attached)


def _max_session_duration(properties: Mapping[str, Any]) -> int:
    duration = properties.get("MaxSessionDuration", DEFAULT_MAX_SESSION_DURATION_SECONDS)
    if not isinstance(duration, int) or isinstance(duration, bool):
        raise PolicyNotComparableError(f"MaxSessionDuration is not an integer: {duration!r}")
    return duration


def project_template_role(properties: Mapping[str, Any]) -> TemplateRole:
    """Project the ``Properties`` of one ``AWS::IAM::Role`` into a comparable record."""
    policies = properties.get("Policies", [])
    if not isinstance(policies, list):
        raise PolicyNotComparableError(f"Policies is not a list: {policies!r}")
    trust_document = properties.get("AssumeRolePolicyDocument")
    trust_statements = read_trust_statements(trust_document)
    assert isinstance(trust_document, dict)
    return TemplateRole(
        role_name=_template_string(properties.get("RoleName"), what="RoleName"),
        permissions_boundary_policy_name=_permissions_boundary(properties),
        max_session_duration_seconds=_max_session_duration(properties),
        trust_policy_version=_policy_version(trust_document, what="AssumeRolePolicyDocument"),
        trust_statements=trust_statements,
        inline_policies=tuple(read_inline_policy(policy) for policy in policies),
        attached_managed_policies=_attached_managed_policies(properties),
    )


def load_template_roles(path: Path) -> tuple[TemplateRole, ...]:
    """Every ``AWS::IAM::Role`` a committed template declares, projected for comparison."""
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise PolicyNotComparableError(f"{path.name} is not a CloudFormation template")
    resources = document.get("Resources", {})
    if not isinstance(resources, dict):
        raise PolicyNotComparableError(f"{path.name} has no Resources map")
    roles: list[TemplateRole] = []
    for resource in resources.values():
        if not isinstance(resource, dict) or resource.get("Type") != "AWS::IAM::Role":
            continue
        properties = resource.get("Properties")
        if not isinstance(properties, dict):
            raise PolicyNotComparableError(f"{path.name} declares a role with no Properties")
        roles.append(project_template_role(properties))
    return tuple(roles)


def project_deployed_role(
    role: Mapping[str, Any],
    inline_documents: Sequence[Mapping[str, Any]],
    attached: Sequence[Mapping[str, Any]],
    *,
    own_account: str,
    environment: str,
    observed_at: datetime,
) -> DeployedRoleEvidence:
    """One role as IAM described it, masked and narrowed to what a template can be
    compared against.

    Read and dropped on purpose: the role's own ARN, its path, its role ID, its tags, its
    description, and its creation and last-used dates. None of them is comparable to
    anything this repository commits, and the ARN is the account ID with a name attached.

    Lives here rather than in a capture tool because every phase that adds roles needs it
    and the four functions it is built from are already here. It was in
    ``tools/capture_phase1_evidence.py`` until Phase 3 needed the same projection for its
    own four roles, and a second copy would have been a second answer to "what is
    comparable", drifting silently from the comparison directly below.

    Takes the three context values it uses rather than a capture context, so no phase's
    tool has to construct another phase's argument object to project a role.
    """
    masked = redact_account_ids_in_document(dict(role), own_account=own_account)
    boundary = masked.get("PermissionsBoundary")
    boundary_name: str | None = None
    if isinstance(boundary, dict):
        boundary_name, _scope = iam_policy_from_arn(
            str(boundary.get("PermissionsBoundaryArn")), what="PermissionsBoundary"
        )
    trust_document = masked.get("AssumeRolePolicyDocument")
    attached_policies = redact_account_ids_in_document(list(attached), own_account=own_account)
    return DeployedRoleEvidence.model_validate(
        {
            "source": "aws",
            "environment": environment,
            "status": "ok",
            "observed_at": observed_at,
            "role_name": masked.get("RoleName"),
            "permissions_boundary_policy_name": boundary_name,
            "max_session_duration_seconds": masked.get("MaxSessionDuration"),
            "trust_policy_version": (
                trust_document.get("Version") if isinstance(trust_document, dict) else None
            ),
            "trust_statements": read_trust_statements(trust_document),
            "inline_policies": [
                read_inline_policy(
                    redact_account_ids_in_document(dict(document), own_account=own_account)
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


# --------------------------------------------------------------------------------------
# Comparing the two
# --------------------------------------------------------------------------------------


def _grant(effect: str, *, extra: bool) -> DriftDirection:
    """Which way an element that *grants* moves the role: an action, resource, principal."""
    if effect == "Allow":
        return DriftDirection.WIDER if extra else DriftDirection.NARROWER
    return DriftDirection.NARROWER if extra else DriftDirection.WIDER


def _restriction(effect: str, *, extra: bool) -> DriftDirection:
    """Which way a *condition* moves the role. Exactly the inverse of a grant."""
    return _grant(effect, extra=not extra)


class _Comparison:
    """One role compared against one template, accumulating findings as it goes."""

    def __init__(self, *, partition: str, region: str) -> None:
        self._partition = partition
        self._region = region
        self._findings: list[RoleDriftFinding] = []

    @property
    def findings(self) -> tuple[RoleDriftFinding, ...]:
        return tuple(self._findings)

    def report(self, direction: DriftDirection, element: str, detail: str) -> None:
        self._findings.append(RoleDriftFinding(direction=direction, element=element, detail=detail))

    def fold(self, value: str) -> str:
        return normalize_policy_string(value, partition=self._partition, region=self._region)

    def _condition_key(self, condition: IamConditionEntry) -> tuple[str, str, tuple[str, ...]]:
        return (
            condition.operator,
            condition.condition_key,
            tuple(sorted(self.fold(value) for value in condition.values)),
        )

    def _statement_key(
        self, statement: IamTrustStatement | IamPermissionStatement
    ) -> tuple[object, ...]:
        """Everything one statement selects, in a form two statements compare on."""
        selectors: list[object] = [
            statement.sid,
            statement.effect,
            statement.action_match.element,
            tuple(sorted(statement.action_match.actions)),
            tuple(sorted(self._condition_key(entry) for entry in statement.conditions)),
        ]
        if isinstance(statement, IamPermissionStatement):
            selectors.append(statement.resource_match.element)
            selectors.append(
                tuple(sorted(self.fold(one) for one in statement.resource_match.resources))
            )
        else:
            selectors.append(statement.principal_match.element)
            selectors.append(
                tuple(
                    sorted(
                        (principal.principal_type, self.fold(principal.identifier))
                        for principal in statement.principal_match.principals
                    )
                )
            )
        return tuple(selectors)

    def _content(
        self, statement: IamTrustStatement | IamPermissionStatement
    ) -> frozenset[tuple[str, ...]]:
        """What a statement is *about*, used only to decide which statements to pair.

        Deliberately excludes the effect and the element names. Two unrelated Allow
        statements share both of those, and pairing on them would diff a statement about
        ECR against a statement about CloudFormation.
        """
        content: set[tuple[str, ...]] = {
            ("action", action) for action in statement.action_match.actions
        }
        content |= {
            ("condition", entry.operator, entry.condition_key) for entry in statement.conditions
        }
        if isinstance(statement, IamPermissionStatement):
            content |= {("resource", self.fold(one)) for one in statement.resource_match.resources}
        else:
            content |= {
                ("principal", one.principal_type, self.fold(one.identifier))
                for one in statement.principal_match.principals
            }
        return frozenset(content)

    def compare_sets(
        self,
        *,
        element: str,
        deployed: Sequence[str],
        template: Sequence[str],
        effect: str,
        noun: str,
    ) -> None:
        """Report what each side holds and the other does not, with the right direction."""
        in_deployed = sorted(set(deployed) - set(template))
        in_template = sorted(set(template) - set(deployed))
        if in_deployed:
            self.report(
                _grant(effect, extra=True),
                element,
                f"the deployed role carries {noun} the template does not: {', '.join(in_deployed)}",
            )
        if in_template:
            self.report(
                _grant(effect, extra=False),
                element,
                f"the template declares {noun} the deployed role does not: "
                f"{', '.join(in_template)}",
            )

    def compare_conditions(
        self,
        *,
        element: str,
        deployed: Sequence[IamConditionEntry],
        template: Sequence[IamConditionEntry],
        effect: str,
    ) -> None:
        """Compare conditions by what each one keys on, then by the values it accepts.

        The two levels move the role in opposite directions and cannot share a rule. A
        condition that is present in one document and absent from the other *restricts*,
        so dropping one from an ``Allow`` widens the role. The values inside one condition
        are an OR, so adding one to an ``Allow`` widens the role instead. Comparing whole
        conditions as opaque strings gets the second case exactly backwards, which is why
        this does not.
        """
        by_deployed = {(one.operator, one.condition_key): one for one in deployed}
        by_template = {(one.operator, one.condition_key): one for one in template}
        for key in sorted(set(by_deployed) - set(by_template)):
            self.report(
                _restriction(effect, extra=True),
                element,
                f"the deployed role is conditioned on {key[0]} {key[1]}, which the template "
                "does not require",
            )
        for key in sorted(set(by_template) - set(by_deployed)):
            self.report(
                _restriction(effect, extra=False),
                element,
                f"the template requires {key[0]} {key[1]} and the deployed role does not",
            )
        for key in sorted(set(by_deployed) & set(by_template)):
            gained = sorted(set(by_deployed[key].values) - set(by_template[key].values))
            lost = sorted(set(by_template[key].values) - set(by_deployed[key].values))
            if gained and lost:
                self.report(
                    DriftDirection.CHANGED,
                    element,
                    f"{key[0]} {key[1]} accepts {', '.join(gained)} where the template "
                    f"accepts {', '.join(lost)}",
                )
            elif gained:
                self.report(
                    _grant(effect, extra=True),
                    element,
                    f"{key[0]} {key[1]} accepts values the template does not: {', '.join(gained)}",
                )
            elif lost:
                self.report(
                    _grant(effect, extra=False),
                    element,
                    f"{key[0]} {key[1]} does not accept values the template does: "
                    f"{', '.join(lost)}",
                )

    def compare_statement(
        self,
        *,
        element: str,
        deployed: IamTrustStatement | IamPermissionStatement,
        template: IamTrustStatement | IamPermissionStatement,
    ) -> None:
        if deployed.sid != template.sid:
            self.report(
                DriftDirection.CHANGED,
                f"{element} sid",
                f"deployed {deployed.sid!r}, template {template.sid!r}",
            )
        if deployed.effect != template.effect:
            self.report(
                DriftDirection.CHANGED,
                f"{element} effect",
                f"deployed {deployed.effect}, template {template.effect}",
            )
            return
        effect = template.effect
        if deployed.action_match.element != template.action_match.element:
            self.report(
                DriftDirection.CHANGED,
                f"{element} action element",
                f"deployed selects actions by {deployed.action_match.element}, the template "
                f"by {template.action_match.element}",
            )
        else:
            self.compare_sets(
                element=f"{element} actions",
                deployed=deployed.action_match.actions,
                template=template.action_match.actions,
                effect=effect,
                noun="actions",
            )
        self.compare_conditions(
            element=f"{element} conditions",
            deployed=deployed.conditions,
            template=template.conditions,
            effect=effect,
        )
        if isinstance(deployed, IamPermissionStatement) and isinstance(
            template, IamPermissionStatement
        ):
            self._compare_resources(element=element, deployed=deployed, template=template)
        if isinstance(deployed, IamTrustStatement) and isinstance(template, IamTrustStatement):
            self._compare_principals(element=element, deployed=deployed, template=template)

    def _compare_resources(
        self,
        *,
        element: str,
        deployed: IamPermissionStatement,
        template: IamPermissionStatement,
    ) -> None:
        if deployed.resource_match.element != template.resource_match.element:
            self.report(
                DriftDirection.CHANGED,
                f"{element} resource element",
                f"deployed selects resources by {deployed.resource_match.element}, the "
                f"template by {template.resource_match.element}",
            )
            return
        self.compare_sets(
            element=f"{element} resources",
            deployed=[self.fold(one) for one in deployed.resource_match.resources],
            template=[self.fold(one) for one in template.resource_match.resources],
            effect=template.effect,
            noun="resources",
        )

    def _compare_principals(
        self,
        *,
        element: str,
        deployed: IamTrustStatement,
        template: IamTrustStatement,
    ) -> None:
        if deployed.principal_match.element != template.principal_match.element:
            self.report(
                DriftDirection.CHANGED,
                f"{element} principal element",
                f"deployed selects principals by {deployed.principal_match.element}, the "
                f"template by {template.principal_match.element}",
            )
            return
        self.compare_sets(
            element=f"{element} principals",
            deployed=[
                f"{one.principal_type}={self.fold(one.identifier)}"
                for one in deployed.principal_match.principals
            ],
            template=[
                f"{one.principal_type}={self.fold(one.identifier)}"
                for one in template.principal_match.principals
            ],
            effect=template.effect,
            noun="principals",
        )

    def _pair_statements(
        self,
        deployed: Sequence[IamTrustStatement] | Sequence[IamPermissionStatement],
        template: Sequence[IamTrustStatement] | Sequence[IamPermissionStatement],
    ) -> tuple[list[tuple[int, int]], list[int], list[int]]:
        """Decide which deployed statement each template statement became.

        Identical statements are matched first and drop out, which is what makes a
        reordered document produce nothing at all: IAM evaluates statements as a set, so
        the order they are stored in grants nothing either way.

        What is left is paired by how much the two statements still have in common, and
        never by position. Position would diff the statement that moved against whichever
        one landed where it used to be, and report eight differences where there is one.
        Two statements with nothing in common are not paired, so a statement that was
        added or removed outright is reported whole rather than diffed against a stranger.
        """
        open_deployed = set(range(len(deployed)))
        open_template = set(range(len(template)))
        deployed_keys = [self._statement_key(one) for one in deployed]
        template_keys = [self._statement_key(one) for one in template]
        for index in sorted(open_deployed):
            identical = next(
                (
                    other
                    for other in sorted(open_template)
                    if template_keys[other] == deployed_keys[index]
                ),
                None,
            )
            if identical is not None:
                open_deployed.discard(index)
                open_template.discard(identical)
        candidates = sorted(
            (
                -len(self._content(deployed[one]) & self._content(template[other])),
                one,
                other,
            )
            for one in open_deployed
            for other in open_template
        )
        pairs: list[tuple[int, int]] = []
        for shared, one, other in candidates:
            if shared == 0 or one not in open_deployed or other not in open_template:
                continue
            open_deployed.discard(one)
            open_template.discard(other)
            pairs.append((one, other))
        return sorted(pairs), sorted(open_deployed), sorted(open_template)

    def compare_statements(
        self,
        *,
        element: str,
        deployed: Sequence[IamTrustStatement] | Sequence[IamPermissionStatement],
        template: Sequence[IamTrustStatement] | Sequence[IamPermissionStatement],
    ) -> None:
        """Compare two statement lists, ignoring their order and nothing else."""
        pairs, unpaired_deployed, unpaired_template = self._pair_statements(deployed, template)
        for one, other in pairs:
            # Numbered by where it sits in the deployed document, because that is the one
            # a reader goes and looks at.
            self.compare_statement(
                element=f"{element} statement {one + 1}",
                deployed=deployed[one],
                template=template[other],
            )
        for index in unpaired_deployed:
            statement = deployed[index]
            self.report(
                _grant(statement.effect, extra=True),
                element,
                f"the deployed role carries a statement the template does not: "
                f"{self._render(statement)}",
            )
        for index in unpaired_template:
            statement = template[index]
            self.report(
                _grant(statement.effect, extra=False),
                element,
                f"the template declares a statement the deployed role does not: "
                f"{self._render(statement)}",
            )

    def _render(self, statement: IamTrustStatement | IamPermissionStatement) -> str:
        actions = ", ".join(sorted(statement.action_match.actions))
        label: str
        if isinstance(statement, IamPermissionStatement):
            target = ", ".join(sorted(self.fold(one) for one in statement.resource_match.resources))
            label = statement.resource_match.element
        else:
            target = ", ".join(
                sorted(
                    f"{one.principal_type}={self.fold(one.identifier)}"
                    for one in statement.principal_match.principals
                )
            )
            label = statement.principal_match.element
        return f"{statement.effect} {statement.action_match.element} [{actions}] {label} [{target}]"


def _compare_boundary(
    comparison: _Comparison, *, deployed: str | None, template: str | None
) -> None:
    if deployed == template:
        return
    if template is not None and deployed is None:
        comparison.report(
            DriftDirection.WIDER,
            "permissions boundary",
            f"the template attaches {template} and the deployed role has none",
        )
    elif template is None and deployed is not None:
        comparison.report(
            DriftDirection.NARROWER,
            "permissions boundary",
            f"the deployed role is bounded by {deployed} and the template attaches none",
        )
    else:
        comparison.report(
            DriftDirection.CHANGED,
            "permissions boundary",
            f"deployed {deployed}, template {template}",
        )


def _compare_inline_policies(
    comparison: _Comparison,
    *,
    deployed: Sequence[IamInlinePolicy],
    template: Sequence[IamInlinePolicy],
) -> None:
    by_deployed = {policy.policy_name: policy for policy in deployed}
    by_template = {policy.policy_name: policy for policy in template}
    for name in sorted(set(by_deployed) - set(by_template)):
        comparison.report(
            _policy_direction(by_deployed[name], extra=True),
            f"inline policy {name!r}",
            "the deployed role carries an inline policy the template does not declare",
        )
    for name in sorted(set(by_template) - set(by_deployed)):
        comparison.report(
            _policy_direction(by_template[name], extra=False),
            f"inline policy {name!r}",
            "the template declares an inline policy the deployed role does not carry",
        )
    for name in sorted(set(by_deployed) & set(by_template)):
        one, other = by_deployed[name], by_template[name]
        if one.policy_version != other.policy_version:
            comparison.report(
                DriftDirection.CHANGED,
                f"inline policy {name!r} version",
                f"deployed {one.policy_version!r}, template {other.policy_version!r}",
            )
        comparison.compare_statements(
            element=f"inline policy {name!r}",
            deployed=one.statements,
            template=other.statements,
        )


def _policy_direction(policy: IamInlinePolicy, *, extra: bool) -> DriftDirection:
    """A whole policy is as wide as its statements agree it is, and CHANGED when they do not."""
    directions = {_grant(statement.effect, extra=extra) for statement in policy.statements}
    return directions.pop() if len(directions) == 1 else DriftDirection.CHANGED


def compare_role_to_template(
    evidence: DeployedRoleEvidence,
    template: TemplateRole,
    *,
    template_path: str,
    partition: str,
    region: str,
) -> RoleDriftReport:
    """Every way the captured role and the committed template disagree.

    ``partition`` and ``region`` are the values the normalisation is permitted to fold.
    They are required rather than defaulted, because an ARN naming any other region or
    partition is left alone and therefore reported, and a caller who did not choose them
    would not know that.
    """
    if evidence.role_name != template.role_name:
        raise ValueError(
            f"refusing to compare {evidence.role_name!r} against the template for a "
            f"different role, {template.role_name!r}"
        )
    comparison = _Comparison(partition=partition, region=region)
    _compare_boundary(
        comparison,
        deployed=evidence.permissions_boundary_policy_name,
        template=template.permissions_boundary_policy_name,
    )
    if evidence.max_session_duration_seconds != template.max_session_duration_seconds:
        comparison.report(
            DriftDirection.WIDER
            if evidence.max_session_duration_seconds > template.max_session_duration_seconds
            else DriftDirection.NARROWER,
            "max session duration",
            f"deployed {evidence.max_session_duration_seconds}s, template "
            f"{template.max_session_duration_seconds}s",
        )
    if evidence.trust_policy_version != template.trust_policy_version:
        comparison.report(
            DriftDirection.CHANGED,
            "trust policy version",
            f"deployed {evidence.trust_policy_version!r}, template "
            f"{template.trust_policy_version!r}",
        )
    comparison.compare_statements(
        element="trust policy",
        deployed=evidence.trust_statements,
        template=template.trust_statements,
    )
    _compare_inline_policies(
        comparison,
        deployed=evidence.inline_policies,
        template=template.inline_policies,
    )
    comparison.compare_sets(
        element="attached managed policies",
        deployed=[f"{one.scope}:{one.policy_name}" for one in evidence.attached_managed_policies],
        template=[f"{one.scope}:{one.policy_name}" for one in template.attached_managed_policies],
        effect="Allow",
        noun="managed policies",
    )
    return RoleDriftReport(
        role_name=evidence.role_name,
        template_path=template_path,
        findings=comparison.findings,
    )
