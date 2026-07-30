"""The role that lets a submission read which image a commit published.

``.github/workflows/submit-run.yml`` compiles a manifest without credentials, and its
``compile`` job says why: a job that cannot mint a token cannot leak one. The cost is that
the compile step cannot ask ECR anything, so a submitter pastes a 71-character digest
copied out of another repository's build log, and the image scan summary is always absent
-- which fails ``image_scan_is_reviewed`` closed against ``config/image-exceptions.yaml``,
a two-entry allowlist that is currently the only way any image can run at all.

``sbsandbox-intern-edullm-image-resolver`` is what removes both, and these tests are the
reason it is safe for it to be assumable before anybody has approved anything. The
invariant that workflow maintains is not "no AWS": ``deny-unapproved`` already holds
``id-token: write`` and calls STS on every dispatch. The invariant is that an unapproved
dispatch cannot obtain the *admission* role -- cannot start an execution, cannot submit a
job, cannot write lineage. A role that can only describe images and their scan findings
starts nothing and writes nothing, and returns what the build workflow already prints into
a step summary.

**A trust policy cannot distinguish jobs within a workflow.** ``compile``,
``deny-unapproved`` and the resolve job that will hold this role all present the same
``job_workflow_ref`` and the same ``sub``, so this role is assumable by any of them, and by
any job added to that file afterwards. That is acceptable only for as long as it reads and
nothing more, which is why the first test below asserts the action set exactly rather than
approximately.

Everything here reads the committed template through ``load_template_roles``, the same
projection the drift comparison uses, so a template these tests pass cannot be one the
comparison refuses to read.
"""

from __future__ import annotations

from pathlib import Path

from edullm_platform.role_drift import (
    PHASE5_ROLE_TEMPLATES,
    TemplateRole,
    load_template_roles,
    split_arn_fields,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]

ROLE_NAME = "sbsandbox-intern-edullm-image-resolver"
TEMPLATE = "infra/iam/image-resolver-role.yaml"

#: The workflow whose token this role accepts, spelled the way GitHub mints the claim.
SUBMISSION_WORKFLOW_REF = "edu-llm/platform/.github/workflows/submit-run.yml@refs/heads/main"

#: The two reads, and the reason the role exists. ``DescribeImages`` answers which digest a
#: tag points at; ``DescribeImageScanFindings`` answers what the registry found in it.
EXPECTED_ACTIONS = frozenset({"ecr:DescribeImages", "ecr:DescribeImageScanFindings"})

#: The resource portion every grant must end in. A repository outside this prefix is a
#: repository nobody registered.
REPOSITORY_SCOPE = "repository/sbsandbox-intern-edullm-*"

BOUNDARY_NAME = "InternSandboxBoundary"


def image_resolver() -> TemplateRole:
    roles = load_template_roles(PROJECT_ROOT / TEMPLATE)
    assert len(roles) == 1, f"{TEMPLATE} should declare exactly one role, not {len(roles)}"
    return roles[0]


def granted_actions(role: TemplateRole) -> set[str]:
    """Every action the role's inline policies allow, refusing the negated spellings.

    ``NotAction`` with ``Allow`` permits everything that is *not* listed, so a reader that
    collected its list would report the two narrowest-looking actions on the widest
    possible grant.
    """
    granted: set[str] = set()
    for policy in role.inline_policies:
        for statement in policy.statements:
            assert statement.effect == "Allow", (
                f"{role.role_name} carries a {statement.effect} statement; this role is "
                "read-only by construction and has nothing to deny"
            )
            assert statement.action_match.element == "Action", (
                f"{role.role_name} selects actions by {statement.action_match.element}, "
                "which with Allow grants everything it does not list"
            )
            granted.update(statement.action_match.actions)
    return granted


def test_the_image_resolver_grants_exactly_two_read_actions_and_nothing_else() -> None:
    """Mutation: add ``ecr:BatchGetImage`` while we are here.

    Every action beyond these two widens what an unapproved dispatch can reach, and the
    entire argument for letting this role be assumed before approval is that it reads and
    nothing more. Asserted as an exact set rather than as a superset, because a trust
    policy cannot tell the resolve job from any other job in the same workflow file, so
    "these two and whatever else somebody needed" is a grant to all of them.
    """
    assert granted_actions(image_resolver()) == EXPECTED_ACTIONS


def test_no_grant_on_the_image_resolver_reaches_outside_the_platform_repositories() -> None:
    """Mutation: scope a statement to ``*`` because describe is harmless anyway.

    It is not harmless on a repository this platform did not create. The read tells a
    caller which digests exist and what a scanner found in them, and on somebody else's
    repository that is a report on software this account has no business enumerating.
    """
    for policy in image_resolver().inline_policies:
        for statement in policy.statements:
            assert statement.resource_match.element == "Resource", (
                f"statements select resources by {statement.resource_match.element}, "
                "which with Allow reaches everything it does not list"
            )
            for resource in statement.resource_match.resources:
                fields = split_arn_fields(resource)
                assert fields is not None, f"{resource} is not an ARN"
                assert fields[2] == "ecr", f"{resource} names a service other than ECR"
                assert fields[4] == "${AWS::AccountId}", (
                    f"{resource} names an account other than the one deploying it"
                )
                assert fields[5] == REPOSITORY_SCOPE, (
                    f"{resource} reaches outside {REPOSITORY_SCOPE}"
                )


def test_the_image_resolver_trusts_only_the_submission_workflow() -> None:
    """Mutation: relax ``job_workflow_ref`` to a ``StringLike`` over the workflows directory.

    ``StringEquals`` is an exact match, which is what makes this pin worth having and also
    what makes it fragile in a way nothing warns about: renaming ``submit-run.yml`` revokes
    this role exactly the way it revokes the admission role. Neither IAM nor GitHub knows
    the string is a file path, so the failure presents as broken credentials on a
    configure-credentials step rather than as a rename.
    """
    statements = image_resolver().trust_statements
    assert len(statements) == 1

    pinned = [
        condition
        for condition in statements[0].conditions
        if condition.condition_key.endswith(":job_workflow_ref")
    ]

    assert len(pinned) == 1
    assert pinned[0].operator == "StringEquals"
    assert pinned[0].values == (SUBMISSION_WORKFLOW_REF,)


def test_the_image_resolver_carries_the_boundary_that_lets_it_be_created_at_all() -> None:
    """Mutation: drop the boundary while reformatting the template.

    ``iam:CreateRole`` is denied outright unless the request carries this exact boundary,
    so the deploy fails rather than creating a weaker role -- but it fails from a laptop,
    hours after review, with an access denial that names ``CreateRole`` and not the missing
    line.
    """
    assert image_resolver().permissions_boundary_policy_name == BOUNDARY_NAME


def test_the_image_resolver_is_registered_against_the_template_that_declares_it() -> None:
    """Reads BOTH sides. Mutation: ship the role and leave the registry alone.

    A role in no registry is a role nothing compares against its template, and the failure
    is silence rather than an error, because a check over an empty set passes. ``infra/README.md``
    says listing it is part of shipping the role rather than a follow-up, for this reason.
    """
    registered = dict(PHASE5_ROLE_TEMPLATES)

    assert registered.get(ROLE_NAME) == TEMPLATE
    assert ROLE_NAME in {
        role.role_name for role in load_template_roles(PROJECT_ROOT / registered[ROLE_NAME])
    }


def test_the_image_resolver_can_pass_no_role_and_reach_no_identity_service() -> None:
    """Mutation: a well-meaning ``iam:PassRole`` added later for some other purpose.

    This role is assumable before a human has approved anything, so anything it can pass,
    an unapproved dispatch can pass. The same goes for ``sts:``: a role that can assume
    another role is worth exactly what that other role is worth, and the one this workflow
    must not reach lives one ``sts:AssumeRole`` away.
    """
    granted = granted_actions(image_resolver())

    assert [action for action in granted if action.startswith(("iam:", "sts:"))] == []
    # A wildcard grants both without spelling either, which is how this test would
    # otherwise pass over a role holding `*`.
    assert [action for action in granted if "*" in action] == []
