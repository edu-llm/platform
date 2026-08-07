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

**A STACK NAME APPEARS ONCE AND A TEMPLATE MAY APPEAR SEVERAL TIMES, AND THE ASYMMETRY IS
THE WHOLE OF WHAT THIS TABLE PROMISES.** The derivation the amendment register performs is
role, then template, then stack, and every step has to be a function. A choice made silently
by dictionary order is how the typed field went wrong in the first place.

Both halves used to be enforced by refusing a duplicate template outright, and that was
correct while every template was applied once. ``infra/batch-capacity-block.yaml`` is not: it
is parameterised on an instance type, a reservation id and an availability zone, so one file
is how *every* capacity block is deployed, and two researchers holding blocks on two
different shapes in the same month is two stacks from it. The old rule made that an
``UnmappedTemplateError`` at **module import**, so the second block did not fail at review or
at deploy -- it took the binary down for everybody, including the people with no block at all.

The alternative was one live block at a time, which is smaller and was rejected. A stack name
is unique per account and region, so AWS already enforces it and nothing had to be written
down; what it costs is that this platform could offer exactly one of its four block-backed
shapes at any moment, which is the opposite of what pricing four of them was for. The
constraint is also invisible until somebody has already searched for offerings on the second
one.

So :func:`stacks_for_template` answers the several and :func:`stack_for_template` keeps the
one, raising for a template with more than one rather than choosing. Nothing that derives an
apply from a role reaches the second case: ``pending_amendments`` resolves through
``declared_role_templates()``, which is IAM role templates only, and no capacity block stack
declares a role. :func:`sole_template_stacks` is what holds that to the table rather than to
this sentence.

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

from collections.abc import Iterable
from typing import Final

__all__ = [
    "CAPACITY_BLOCK_STACK_PREFIX",
    "CAPACITY_BLOCK_TEMPLATE",
    "IAM_TEMPLATE_DIRECTORY",
    "STACK_TEMPLATES",
    "DuplicateStackError",
    "UnmappedTemplateError",
    "applied_from_a_laptop",
    "sole_template_stacks",
    "stack_for_template",
    "stacks_for_template",
    "template_by_stack",
    "template_for_stack",
]

#: Templates under here declare IAM roles, and no workflow may apply one.
IAM_TEMPLATE_DIRECTORY: Final = "infra/iam/"

#: The one template this repository deploys more than once.
CAPACITY_BLOCK_TEMPLATE: Final = "infra/batch-capacity-block.yaml"

#: What every stack deployed from it is named, with the compute profile appended.
#:
#: **THE SHAPE IS IN THE NAME BECAUSE THE STACK IS PINNED TO ONE SHAPE.** The template creates a
#: compute environment fixed to one instance type in one availability zone, so a block on
#: ``p6-b200.48xlarge`` and a block on ``p5en.48xlarge`` cannot share a stack whatever else is
#: true. Naming them ``…-capacity-block-gpu-8xb200`` and ``…-capacity-block-gpu-8xh200`` makes an
#: account listing say which shape each one is holding, which is the question anybody looking at
#: a four-figure daily charge asks first.
#:
#: The compute profile rather than the instance type, so that the name joins to
#: ``config/workload-catalog.yaml`` and ``config/execution-targets.yaml`` -- the two files that
#: have to be edited when the stack goes up and reverted when the window closes. A dated suffix
#: was considered and deferred: it would allow two windows on one shape, which nothing can route
#: to anyway, because an execution target row carries one queue arn per profile.
CAPACITY_BLOCK_STACK_PREFIX: Final = "sbsandbox-intern-edullm-capacity-block-"


class UnmappedTemplateError(KeyError):
    """A template resolves to no stack, or to more than one where the caller needs one."""


class DuplicateStackError(ValueError):
    """One stack name is declared twice.

    Raised at import, like the duplicate-template refusal that used to sit beside it, and it is
    the half of that rule that had to stay. A stack name is the thing CloudFormation acts on, so
    two rows sharing one is two different templates claiming to be what is deployed under that
    name, and ``dict(STACK_TEMPLATES)`` would have taken the later one in silence --
    :func:`template_for_stack` would then answer confidently and wrongly, and
    :func:`applied_from_a_laptop` would derive whether a person or a workflow deploys it from the
    wrong file. That was already possible before this class existed; nothing checked it, because
    the template rule made it hard to reach by accident.
    """


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
    # THE FOUR STACKS HERE THAT ARE EXPECTED TO BE ABSENT MOST OF THE TIME, and they are in this
    # table for that reason rather than in spite of it. Each is deployed against one purchased
    # capacity block -- the parameters are a reservation id, an instance type and the one
    # availability zone the block was delivered in -- and torn down when that window ends, so a
    # listing with none of them is the normal state of the account rather than drift.
    #
    # A template under infra/ that this table does not claim fails
    # tests/test_deployed_stacks.py, and that check exists because a hand-applied stack nobody
    # recorded is exactly what this repository had no way to see until 2026-07-31. Leaving a
    # parameterised, redeployable template out on the grounds that it is transient would recreate
    # that hole in the one place where the stack costs four figures a day while it exists.
    #
    # FOUR ROWS AND ONE TEMPLATE, WHICH IS WHY THIS MODULE STOPPED REFUSING A REPEATED TEMPLATE.
    # There was one row here, `…-capacity-block`, and a second concurrent block therefore had
    # nowhere to be declared: adding it raised UnmappedTemplateError at import and took the
    # binary down for everybody. One row per block-backed compute profile is the whole set that
    # can ever exist, because the stack is pinned to one instance type and there are four such
    # profiles in config/workload-catalog.yaml.
    #
    # tests/test_deployed_stacks.py holds these four to the four profiles the catalog flags, so
    # a fifth block shape priced without a row here fails there rather than at the deploy.
    ("sbsandbox-intern-edullm-capacity-block-gpu-8xa100-80gb", CAPACITY_BLOCK_TEMPLATE),
    ("sbsandbox-intern-edullm-capacity-block-gpu-8xh200", CAPACITY_BLOCK_TEMPLATE),
    ("sbsandbox-intern-edullm-capacity-block-gpu-8xb200", CAPACITY_BLOCK_TEMPLATE),
    ("sbsandbox-intern-edullm-capacity-block-gpu-8xb300", CAPACITY_BLOCK_TEMPLATE),
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
    # The one identity allowed to put an approval request on the notifier queue. Its trust
    # pins .github/workflows/notify-approval-requested.yml rather than submit-run.yml, for
    # the reason that template sets out at length.
    (
        "sbsandbox-intern-edullm-notifier-publisher-iam",
        "infra/iam/notifier-publisher-role.yaml",
    ),
)


def _by_template() -> dict[str, tuple[str, ...]]:
    """The table read the other way round, in declaration order per template."""
    found: dict[str, list[str]] = {}
    for stack, template in STACK_TEMPLATES:
        found.setdefault(template, []).append(stack)
    return {template: tuple(stacks) for template, stacks in found.items()}


def template_by_stack(rows: Iterable[tuple[str, str]]) -> dict[str, str]:
    """Rows as a mapping from stack name to template, refusing a name two rows claim.

    Called on :data:`STACK_TEMPLATES` at import rather than on each lookup, so a duplicated stack
    name fails the first thing that imports this module rather than the first caller that happens
    to ask about that name. Takes the rows as an argument so that the refusal itself is testable
    without a second table having to exist in the tree to break it with.
    """
    found: dict[str, str] = {}
    for stack, template in rows:
        if stack in found:
            raise DuplicateStackError(
                f"{stack} is declared twice, from {found[stack]} and from {template}. One name "
                "is one stack in an account, so only one of those two is what is deployed "
                "under it, and nothing here could say which."
            )
        found[stack] = template
    return found


_STACKS_BY_TEMPLATE: Final = _by_template()
_TEMPLATE_BY_STACK: Final = template_by_stack(STACK_TEMPLATES)


def stacks_for_template(relative_path: str) -> tuple[str, ...]:
    """Every stack this template is applied as, in declaration order.

    One entry for all but the capacity block template, and the plural is what makes a second
    concurrent block a deploy rather than an import error. Raises for a template no stack
    claims, for the reason :func:`stack_for_template` gives.
    """
    try:
        return _STACKS_BY_TEMPLATE[relative_path]
    except KeyError:
        raise UnmappedTemplateError(
            f"no stack in STACK_TEMPLATES is deployed from {relative_path}, so nothing can "
            "say which apply would realise a change to it. Either the template is applied "
            "under a name this table does not carry, in which case add the row, or it is "
            "applied by nothing at all, which is the finding."
        ) from None


def sole_template_stacks() -> tuple[tuple[str, str], ...]:
    """The rows whose template is applied exactly once, which is every row but the blocks.

    Exists so that a test can state the narrowed invariant against the table rather than
    against the module docstring: the derivation ``pending_amendments`` performs is a function
    over *these*, and the several-stacks case is reachable only for a template no role registry
    names. See ``tests/test_pending_amendment_stacks.py``.
    """
    return tuple(
        (stack, template)
        for stack, template in STACK_TEMPLATES
        if len(_STACKS_BY_TEMPLATE[template]) == 1
    )


def stack_for_template(relative_path: str) -> str:
    """The one stack this template is applied as.

    Raises rather than answering ``None``, because every caller here is deriving a fact it
    is about to print as an instruction. A caller that got ``None`` would either print it
    or fall back to something it made up, and both read to somebody at 05:00 as an answer.

    Raises for a template deployed several times too, and does not pick. Where several stacks
    run one file, the apply that realises a change to it is *every* one of them, so an answer
    naming one would report an amendment cleared while a stack was still running the old
    template -- and for the capacity block template that stack costs four figures a day.
    """
    stacks = stacks_for_template(relative_path)
    if len(stacks) > 1:
        raise UnmappedTemplateError(
            f"{relative_path} is deployed as {len(stacks)} stacks, {', '.join(stacks)}, so "
            "there is no single apply that realises a change to it. It is parameterised and "
            "deployed once per thing it is parameterised on. Ask stacks_for_template for all "
            "of them and decide what to do about each; choosing one here silently is the "
            "defect this table exists to remove."
        )
    return stacks[0]


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
