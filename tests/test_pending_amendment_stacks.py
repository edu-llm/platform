"""That the stack a pending amendment names is the stack that would actually clear it.

**The defect this replaces was a field that looked like a control and could not fail.**
:class:`~edullm_platform.pending_amendments.PendingAmendment` carried a ``cleared_by``
string naming the CloudFormation stack whose application ends the record. Nothing compared
it to anything. The only assertion over it was
``assert amendment.cleared_by.strip()`` in ``tests/test_phase1_deployed_roles.py``, which
passes for every non-empty string and therefore passes for every wrong one.

On 2026-08-05 it was wrong in a live record. An amendment to
``sbsandbox-intern-edullm-lifecycle-lambda`` gave ``sbsandbox-intern-edullm-phase3-batch-iam``.
That role is declared by ``infra/iam/lifecycle-lambda-role.yaml``, which is applied as
``sbsandbox-intern-edullm-phase3-lifecycle-iam``. The named stack is applied from
``infra/iam/batch-roles.yaml`` and holds three other roles. Following the record would have
reconciled those three against a template that never mentions the grant, succeeded, and left
the finding exactly where it was, with nothing to say whether the deploy had failed or the
record had lied. The audit that followed found three of the five standing records wrong in
the same shape.

**So the field is gone and the fact is derived**, role to template to stack, and what is
left to check is that the derivation lands somewhere real. That is this file. It is not a
naming-pattern check and must never become one: a pattern is satisfied by
``sbsandbox-intern-edullm-phase3-batch-iam`` for a lifecycle role, which is the exact string
that was wrong. The check has to open the template the stack is deployed from and find the
role in it.

**The mutation this is written against.** Point a record at a plausible but wrong stack. It
cannot be done by editing the record any more, so it is done one level up, in the role
registry: say that ``sbsandbox-intern-edullm-lifecycle-lambda`` is declared by
``infra/iam/batch-roles.yaml``. That is the original defect reproduced through the
derivation, it resolves cleanly to ``sbsandbox-intern-edullm-phase3-batch-iam``, and
:func:`test_every_pending_amendment_resolves_to_a_stack_whose_template_declares_its_role`
is what goes red, because that template declares no such role.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

import pytest
import yaml
from workflow_support import WORKFLOWS_ROOT

from edullm_platform.pending_amendments import (
    PENDING_AMENDMENTS,
    PendingAmendment,
    PendingAmendmentError,
    declared_role_templates,
)
from edullm_platform.role_drift import DriftDirection, RoleDriftFinding
from edullm_platform.stack_templates import (
    IAM_TEMPLATE_DIRECTORY,
    STACK_TEMPLATES,
    UnmappedTemplateError,
    applied_from_a_laptop,
    stack_for_template,
    template_for_stack,
)

PROJECT_ROOT: Final = Path(__file__).resolve().parents[1]

#: The role and the template the 2026-08-05 record got wrong, kept as the worked example
#: because a case named after a real failure is one nobody deletes as hypothetical.
LIFECYCLE_ROLE: Final = "sbsandbox-intern-edullm-lifecycle-lambda"
LIFECYCLE_TEMPLATE: Final = "infra/iam/lifecycle-lambda-role.yaml"
LIFECYCLE_STACK: Final = "sbsandbox-intern-edullm-phase3-lifecycle-iam"
WRONG_BUT_PLAUSIBLE_TEMPLATE: Final = "infra/iam/batch-roles.yaml"
WRONG_BUT_PLAUSIBLE_STACK: Final = "sbsandbox-intern-edullm-phase3-batch-iam"

#: What a workflow deploying a template looks like, matched against the file's own text.
#: The parsed form is not used here on purpose: ``aws_commands`` in ``workflow_support``
#: refuses a script whose ``aws`` call sits inside a pipeline or a subshell, and several
#: workflows have one, so parsing would narrow this from every workflow to most of them.
TEMPLATE_FILE_ARGUMENT: Final = re.compile(r"--template-file\s+(\S+)")


def role_names_declared_by(relative_path: str) -> set[str]:
    """Every ``AWS::IAM::Role`` name one committed template declares.

    Read out of the raw YAML rather than through
    :func:`~edullm_platform.role_drift.load_template_roles`, which projects a role for
    comparison and raises on anything it cannot compare. This case needs the weaker
    question -- is the role in this file at all -- to survive a template that gains a
    resource the projector has not learned yet.
    """
    document = yaml.safe_load((PROJECT_ROOT / relative_path).read_text(encoding="utf-8"))
    resources = document.get("Resources", {}) if isinstance(document, dict) else {}
    return {
        properties["RoleName"]
        for resource in resources.values()
        if isinstance(resource, dict) and resource.get("Type") == "AWS::IAM::Role"
        for properties in [resource.get("Properties", {})]
        if isinstance(properties, dict) and isinstance(properties.get("RoleName"), str)
    }


# --------------------------------------------------------------------------------------
# The check the old `assert cleared_by.strip()` should have been
# --------------------------------------------------------------------------------------


def test_every_pending_amendment_resolves_to_a_stack_whose_template_declares_its_role() -> None:
    """The whole point of this file. Record, to stack, to template, to the role itself.

    Every link is asserted rather than the endpoints, because a chain that lands in the
    right place through a wrong middle is the thing that went wrong: the old record named a
    stack that exists, deployed from a template that exists, holding roles that exist, and
    not the role the record was about.
    """
    wrong: list[str] = []
    for amendment in PENDING_AMENDMENTS:
        stack = amendment.cleared_by
        if stack not in dict(STACK_TEMPLATES):
            wrong.append(f"{amendment.role_name}: {stack} is not a stack this repository deploys")
            continue
        template = template_for_stack(stack)
        if template != amendment.template:
            wrong.append(
                f"{amendment.role_name}: the record resolves to {stack}, which is deployed "
                f"from {template}, and the role is declared by {amendment.template}"
            )
            continue
        declared = role_names_declared_by(template)
        if amendment.role_name not in declared:
            wrong.append(
                f"{amendment.role_name}: applying {stack} applies {template}, which declares "
                f"{sorted(declared)} and not this role. Applying it would reconcile somebody "
                "else's roles and report success while the finding stayed exactly where it is"
            )

    assert not wrong, "\n".join(wrong)


def test_the_template_a_record_resolves_to_carries_the_findings_element() -> None:
    """The inline policy each finding names is one the resolved template actually declares.

    A weaker tie than the case above and a different one. That case proves the apply
    touches the role; this proves it touches the part of the role the record is waiting on,
    so a record cannot resolve to the right template through a finding about a policy that
    template does not have.

    Only findings naming an inline policy are checked. A trust policy finding names no
    policy by name, and the element string is prose around a position rather than an
    identifier, so there is nothing here to look up.
    """
    missing: list[str] = []
    for amendment in PENDING_AMENDMENTS:
        document = yaml.safe_load(
            (PROJECT_ROOT / amendment.template).read_text(encoding="utf-8")
        )
        names = {
            policy["PolicyName"]
            for resource in document.get("Resources", {}).values()
            if isinstance(resource, dict) and resource.get("Type") == "AWS::IAM::Role"
            if resource.get("Properties", {}).get("RoleName") == amendment.role_name
            for policy in resource["Properties"].get("Policies", [])
            if isinstance(policy, dict) and isinstance(policy.get("PolicyName"), str)
        }
        for finding in amendment.findings:
            if not finding.element.startswith("inline policy '"):
                continue
            named = finding.element.split("'")[1]
            if named not in names:
                missing.append(
                    f"{amendment.role_name}: a finding names inline policy {named!r} and "
                    f"{amendment.template} declares {sorted(names)} on that role"
                )

    assert not missing, "\n".join(missing)


# --------------------------------------------------------------------------------------
# That the derivation cannot answer a plausible wrong thing
# --------------------------------------------------------------------------------------


def test_a_record_for_a_role_no_template_declares_is_refused() -> None:
    # The first step of the chain. A role nothing declares is compared by nothing, so the
    # findings the record waits to stop seeing would never be reported and it could never
    # clear.
    with pytest.raises(PendingAmendmentError, match="no committed template declares that role"):
        PendingAmendment(
            role_name="sbsandbox-intern-edullm-not-a-role",
            reason="a reason",
            findings=(
                RoleDriftFinding(
                    direction=DriftDirection.NARROWER,
                    element="inline policy 'x'",
                    detail="the template declares an inline policy the deployed role lacks",
                ),
            ),
        )


def test_a_template_no_stack_is_deployed_from_resolves_to_nothing() -> None:
    # The second step. Refusing rather than answering ``None`` is the property: every
    # caller is about to print the answer as an instruction, and a caller handed ``None``
    # either prints it or substitutes something it made up.
    with pytest.raises(UnmappedTemplateError, match="no stack in STACK_TEMPLATES"):
        stack_for_template("infra/iam/a-template-nobody-applies.yaml")


def test_one_template_may_not_be_claimed_by_two_stacks() -> None:
    # What makes the second step a function rather than a choice. Enforced at import of
    # `stack_templates`, so this reads the table rather than calling anything.
    templates = [template for _stack, template in STACK_TEMPLATES]

    assert len(set(templates)) == len(templates), (
        "two stacks claim one template, so which apply clears a change to it is a choice, "
        "and a choice made by dictionary order is how the typed field went wrong"
    )


def test_every_role_a_pending_amendment_could_name_resolves_to_a_stack() -> None:
    """Not just the roles recorded today, but every role a record is allowed to name.

    Written this way round because the register is empty most of the time. A case that
    only walks ``PENDING_AMENDMENTS`` proves nothing on the ordinary day, and the day it
    would have caught something is the day somebody adds a role to a registry and writes a
    record for it in the same hour.
    """
    unresolvable: list[str] = []
    for role_name, template in sorted(declared_role_templates().items()):
        try:
            stack_for_template(template)
        except UnmappedTemplateError:
            unresolvable.append(f"{role_name} is declared by {template}, which no stack applies")

    assert not unresolvable, "\n".join(unresolvable)


def test_every_role_registry_entry_names_a_template_that_declares_it() -> None:
    """The link the mutation attacks, checked for every role rather than every record.

    ``declared_role_templates`` is the first step of the derivation, and a wrong entry
    there is indistinguishable from the defect this file exists for: the record still
    resolves, still to a real stack, and still to the wrong one.
    """
    wrong = [
        f"{role_name} is registered against {template}, which declares "
        f"{sorted(role_names_declared_by(template))}"
        for role_name, template in sorted(declared_role_templates().items())
        if role_name not in role_names_declared_by(template)
    ]

    assert not wrong, "\n".join(wrong)


# --------------------------------------------------------------------------------------
# That "needs a laptop" is true, which is the half three records got wrong
# --------------------------------------------------------------------------------------


def test_no_workflow_applies_a_stack_under_the_iam_directory() -> None:
    """What makes :func:`applied_from_a_laptop` a derivation rather than an assumption.

    Three standing records named a deploy workflow as the thing that would clear them, and
    all three were wrong the same way. The deployer role holds no ``iam:CreateRole``, so no
    workflow can apply an IAM stack, and this reads that off the workflows rather than off
    the sentence saying so.
    """
    applied = [
        f"{path.name} deploys {template}"
        for path in sorted(WORKFLOWS_ROOT.glob("*.yml"))
        for template in TEMPLATE_FILE_ARGUMENT.findall(path.read_text(encoding="utf-8"))
        if template.startswith(IAM_TEMPLATE_DIRECTORY)
    ]

    assert not applied, "\n".join(applied)


def test_every_iam_stack_needs_a_laptop_and_nothing_else_does() -> None:
    # The other direction of the same claim, so a template moved out of `infra/iam/` cannot
    # quietly turn a laptop apply into a merge somebody waits for.
    for stack, template in STACK_TEMPLATES:
        assert applied_from_a_laptop(stack) == template.startswith(IAM_TEMPLATE_DIRECTORY), stack


def test_a_record_says_a_laptop_is_needed_where_one_is() -> None:
    # The sentence a reader acts on, for the record that was corrected by hand and for the
    # three the audit found. All five resolve to IAM stacks today, so all five say laptop.
    for amendment in PENDING_AMENDMENTS:
        assert amendment.needs_a_laptop
        assert amendment.cleared_by in amendment.describe_clearing()
        assert amendment.template in amendment.describe_clearing()
        assert "from a laptop" in amendment.describe_clearing()


# --------------------------------------------------------------------------------------
# The worked example, kept as a case so the defect has a name in the suite
# --------------------------------------------------------------------------------------


def test_the_stack_the_2026_08_05_record_named_declares_none_of_its_role() -> None:
    """The original wrong answer, held wrong.

    If somebody later merges the two Phase 3 IAM templates, or moves the lifecycle role into
    ``batch-roles.yaml``, this case fails and says so. That is the right outcome: the
    example above it in this file would have stopped being an example of anything, and the
    mutation described in the module docstring would have stopped being lethal.
    """
    assert declared_role_templates()[LIFECYCLE_ROLE] == LIFECYCLE_TEMPLATE
    assert stack_for_template(LIFECYCLE_TEMPLATE) == LIFECYCLE_STACK
    assert stack_for_template(WRONG_BUT_PLAUSIBLE_TEMPLATE) == WRONG_BUT_PLAUSIBLE_STACK
    assert LIFECYCLE_ROLE not in role_names_declared_by(WRONG_BUT_PLAUSIBLE_TEMPLATE)
