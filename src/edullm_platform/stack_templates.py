"""Which CloudFormation stack each committed template is applied as.

One table, in the library, because two things need it and they used to answer separately.
``tools/verify_deployed_stacks.py`` needs it to hold the account against ``main``, and
:mod:`edullm_platform.pending_amendments` needs it to say which apply clears a recorded
amendment. The second of those used to be typed into the record by hand, and on 2026-08-05
a record for ``sbsandbox-intern-edullm-lifecycle-lambda`` named
``sbsandbox-intern-edullm-phase3-batch-iam``. That role is declared by
``infra/iam/lifecycle-lambda-role.yaml``, which is applied as
``sbsandbox-intern-edullm-phase3-lifecycle-iam``, so following the record would have
reconciled three other roles against a template that never mentions the grant and reported
success. Nothing compared the string to anything, because there was nothing to compare it
to. This is that thing.

**A template appears once, and the uniqueness is enforced rather than assumed.** The
derivation the amendment register performs is role, then template, then stack, and every
step has to be a function. Two stacks deployed from one file would make the last step a
choice, and a choice made silently by dictionary order is how the typed field went wrong in
the first place. :func:`stack_for_template` raises instead.

**Where a stack is applied from is read off the template's directory rather than recorded.**
No workflow in this repository deploys anything under ``infra/iam/``: the deployer role holds
no ``iam:CreateRole``, deliberately, and ``infra/README.md`` carries the by-hand command for
every one of them. So a template under that directory is a laptop apply and everything else
is a workflow, and ``tests/test_pending_amendment_stacks.py`` holds that to the workflows
themselves rather than to this sentence. It matters because three amendment records got
exactly this wrong: each named a deploy workflow as the thing that would clear it, and none
of the three workflows applies a single IAM stack.
"""

from __future__ import annotations

from typing import Final

__all__ = [
    "IAM_TEMPLATE_DIRECTORY",
    "STACK_TEMPLATES",
    "UnmappedTemplateError",
    "applied_from_a_laptop",
    "stack_for_template",
    "template_for_stack",
]

#: Templates under here declare IAM roles, and no workflow may apply one.
IAM_TEMPLATE_DIRECTORY: Final = "infra/iam/"


class UnmappedTemplateError(KeyError):
    """A template resolves to no stack, or to more than one."""


#: Every stack this repository deploys, in the order the phases created them.
#:
#: Adding a stack means adding a row here and adding its ARN to the audit reader's
#: ``cloudformation:GetTemplate`` grant in ``infra/iam/audit-reader-role.yaml``. A row
#: without the grant is a denial rather than a silence, and a grant without a row is caught
#: by the account listing ``tools/verify_deployed_stacks.py`` makes on every run.
#:
#: **Declared rather than derived from the stack name, and that was measured.** A convention
#: fits most of these and not all: ``…-phase1-ecr`` is ``ecr-repositories.yaml``,
#: ``…-phase2-admission-iam`` is ``iam/admission-role.yaml``, and
#: ``…-phase2-admission-service-roles`` follows neither rule. Something that fits seventeen
#: of twenty-one skips four by resolving to a path that is not there, which is a silent hole
#: exactly where a name is unusual.
STACK_TEMPLATES: Final[tuple[tuple[str, str], ...]] = (
    ("sbsandbox-intern-edullm-phase1-ecr", "infra/ecr-repositories.yaml"),
    ("sbsandbox-intern-edullm-ecr-publisher-iam", "infra/iam/ecr-publisher-role.yaml"),
    ("sbsandbox-intern-edullm-infra-deployer-iam", "infra/iam/infra-deployer-role.yaml"),
    (
        "sbsandbox-intern-edullm-phase2-admission-service-roles",
        "infra/iam/admission-service-roles.yaml",
    ),
    ("sbsandbox-intern-edullm-phase2-admission-iam", "infra/iam/admission-role.yaml"),
    ("sbsandbox-intern-edullm-phase2-lineage", "infra/lineage-bucket.yaml"),
    ("sbsandbox-intern-edullm-phase2-artifacts", "infra/artifacts-bucket.yaml"),
    ("sbsandbox-intern-edullm-phase2-admission", "infra/admission-state-machine.yaml"),
    ("sbsandbox-intern-edullm-phase3-batch-iam", "infra/iam/batch-roles.yaml"),
    ("sbsandbox-intern-edullm-phase3-lifecycle-iam", "infra/iam/lifecycle-lambda-role.yaml"),
    ("sbsandbox-intern-edullm-phase3-outputs", "infra/outputs-bucket.yaml"),
    ("sbsandbox-intern-edullm-phase3-network", "infra/batch-network.yaml"),
    ("sbsandbox-intern-edullm-phase3-batch", "infra/batch-compute.yaml"),
    ("sbsandbox-intern-edullm-phase3-events", "infra/batch-events.yaml"),
    ("sbsandbox-intern-edullm-phase4-gpu-iam", "infra/iam/batch-gpu-roles.yaml"),
    ("sbsandbox-intern-edullm-phase4-gpu", "infra/batch-compute-gpu.yaml"),
    ("sbsandbox-intern-edullm-phase4-gpu-shapes", "infra/batch-compute-gpu-shapes.yaml"),
    ("sbsandbox-intern-edullm-dataset-validator-iam", "infra/iam/dataset-validator-role.yaml"),
    ("sbsandbox-intern-edullm-researcher-iam", "infra/iam/researcher-role.yaml"),
    ("sbsandbox-intern-edullm-janitor-iam", "infra/iam/janitor-lambda-role.yaml"),
    ("sbsandbox-intern-edullm-janitor", "infra/expiry-janitor.yaml"),
    ("sbsandbox-intern-edullm-run-canceller-iam", "infra/iam/run-canceller-role.yaml"),
    ("sbsandbox-intern-edullm-audit-reader-iam", "infra/iam/audit-reader-role.yaml"),
    ("sbsandbox-intern-edullm-phase5-image-resolver-iam", "infra/iam/image-resolver-role.yaml"),
    ("sbsandbox-intern-edullm-run-preview-iam", "infra/iam/run-preview-role.yaml"),
    ("sbsandbox-intern-edullm-notifier-iam", "infra/iam/notifier-lambda-role.yaml"),
    # Before the three stacks whose alarms import its topic ARN, which is also the order
    # .github/workflows/deploy-phase3-batch.yml deploys them in. An export cannot be imported
    # before it exists, so a first deploy in any other order fails on the importing stack.
    ("sbsandbox-intern-edullm-alarms", "infra/alarm-destination.yaml"),
    ("sbsandbox-intern-edullm-notifications", "infra/notifications.yaml"),
    ("sbsandbox-intern-edullm-scratch", "infra/scratch-bucket.yaml"),
    ("sbsandbox-intern-edullm-lane-instance-iam", "infra/iam/lane-instance-role.yaml"),
)


def _by_template() -> dict[str, str]:
    """The table read the other way round, refusing a template two stacks claim.

    Built at import rather than on each call, so a second stack added against a template
    already in the table fails the first thing that imports this module rather than the
    first caller that happens to ask about that template.
    """
    found: dict[str, str] = {}
    for stack, template in STACK_TEMPLATES:
        if template in found:
            raise UnmappedTemplateError(
                f"{template} is declared as the template of two stacks, {found[template]} "
                f"and {stack}. Anything deriving which apply clears a change to that file "
                "would have to choose between them, and choosing silently is the defect "
                "this table exists to remove."
            )
        found[template] = stack
    return found


_STACK_BY_TEMPLATE: Final = _by_template()
_TEMPLATE_BY_STACK: Final = dict(STACK_TEMPLATES)


def stack_for_template(relative_path: str) -> str:
    """The one stack this template is applied as.

    Raises rather than answering ``None``, because every caller here is deriving a fact it
    is about to print as an instruction. A caller that got ``None`` would either print it
    or fall back to something it made up, and both read to somebody at 05:00 as an answer.
    """
    try:
        return _STACK_BY_TEMPLATE[relative_path]
    except KeyError:
        raise UnmappedTemplateError(
            f"no stack in STACK_TEMPLATES is deployed from {relative_path}, so nothing can "
            "say which apply would realise a change to it. Either the template is applied "
            "under a name this table does not carry, in which case add the row, or it is "
            "applied by nothing at all, which is the finding."
        ) from None


def template_for_stack(stack: str) -> str:
    """The template this stack is deployed from."""
    try:
        return _TEMPLATE_BY_STACK[stack]
    except KeyError:
        raise UnmappedTemplateError(
            f"{stack} is not a stack this repository declares a template for."
        ) from None


def applied_from_a_laptop(stack: str) -> bool:
    """Whether this stack needs a person with an SSO session rather than a workflow run.

    Every IAM stack does, and nothing else here does. See the module docstring for why the
    directory is enough and where that is held to the workflows.
    """
    return template_for_stack(stack).startswith(IAM_TEMPLATE_DIRECTORY)
