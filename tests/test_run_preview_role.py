"""The role a submission dispatched from a platform branch assumes, and its ceiling.

Every other role trusted to a workflow in this repository pins its subject to
``refs/heads/main``. ``submit-run.yml`` dispatched from a branch therefore died in its second
job, at the credential step, before anything was compiled and before any gate was reached --
so the submission path was the one path in the platform that could not be exercised until it
was already merged, which is the one place a mistake in it is expensive.

``infra/iam/run-preview-role.yaml`` is the way out, and it is a trade rather than a
relaxation. It gives up the ref condition, which is the whole point of it, and buys that back
with the narrowest grant of any role here: one action on one queue. The tests below are what
hold that trade in place, because the role is created from a laptop and a policy widened in
the console leaves the rest of this suite green.

**What each test is guarding against is a different mutation, and they are not
interchangeable.** Widening the trust policy makes the role reachable from somewhere it
should not be; widening the inline policy makes it able to do something it should not. The
first is the one that reads as harmless -- a ``StringLike`` on the environment name looks
like tidying -- and it is the one that turns this into a way to mint an AWS session from an
unreviewed workflow edit.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from infrastructure_support import (
    ACCOUNT_LITERAL,
    BOUNDARY,
    OIDC_PROVIDER,
    iam_roles,
    load_template,
    statement_actions,
    statement_resources,
    walk_strings,
)

from edullm_platform.config import load_yaml
from edullm_platform.contracts.admission import ApprovalEnvironment
from edullm_platform.contracts.execution import ExecutionTargetCatalog

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_PATH = PROJECT_ROOT / "infra" / "iam" / "run-preview-role.yaml"
ADMISSION_TEMPLATE_PATH = PROJECT_ROOT / "infra" / "iam" / "admission-role.yaml"
EXECUTION_TARGETS_PATH = PROJECT_ROOT / "config" / "execution-targets.yaml"

ROLE_NAME = "sbsandbox-intern-edullm-run-preview"
PREVIEW_ENVIRONMENT = "run-approval-preview"
PREVIEW_SUBJECT = (
    f"repo:edu-llm@306859726/platform@1311508598:environment:{PREVIEW_ENVIRONMENT}"
)
CPU_QUEUE = "sbsandbox-intern-edullm-cpu"


def _role() -> dict[str, Any]:
    roles = list(iam_roles(load_template(TEMPLATE_PATH)))
    assert len(roles) == 1, "this template declares one role and the tests below assume it"
    return roles[0]


def _trust_condition() -> dict[str, Any]:
    statements = _role()["AssumeRolePolicyDocument"]["Statement"]
    assert len(statements) == 1, (
        "one trust statement. A second is another way in, and every test below reads the "
        "first one only."
    )
    return dict(statements[0]["Condition"])


def _statements() -> list[dict[str, Any]]:
    policies = _role()["Policies"]
    assert len(policies) == 1
    return list(policies[0]["PolicyDocument"]["Statement"])


def test_the_role_is_bounded_and_federated_the_way_every_other_oidc_role_here_is() -> None:
    role = _role()
    statement = role["AssumeRolePolicyDocument"]["Statement"][0]

    assert role["RoleName"] == ROLE_NAME
    assert role["PermissionsBoundary"] == BOUNDARY
    assert statement["Principal"]["Federated"] == OIDC_PROVIDER
    assert statement["Action"] == "sts:AssumeRoleWithWebIdentity"
    assert statement["Effect"] == "Allow"


def test_the_trust_names_one_environment_as_a_literal_and_never_a_pattern() -> None:
    """Mutation: replace the subject with a StringLike on `...:environment:*`.

    This is the change that reads as tidying and is not. An environment named in a workflow
    is auto-created on first use with no protection rules at all, so a pattern would accept
    the subject minted for any name a workflow author invented -- a session from an
    unreviewed edit. `infra/iam/admission-role.yaml` enumerates its three names for exactly
    this reason and the argument is not restated here.

    The subject is checked as a whole string rather than by substring, because the owner and
    repository ids in the prefix are what stop a fork of this repository presenting a
    matching claim.
    """
    condition = _trust_condition()

    assert condition["StringEquals"]["token.actions.githubusercontent.com:sub"] == PREVIEW_SUBJECT
    assert "sub" not in {
        key.rsplit(":", 1)[1] for key in condition.get("StringLike", {})
    }, "the subject moved into StringLike, which accepts environments nobody created"


def test_the_workflow_file_is_pinned_and_only_the_ref_is_wild() -> None:
    """Mutation: drop `job_workflow_ref`, on the ground that the subject already gates this.

    It does not. The subject names an environment, and an environment is reachable from any
    workflow that declares it -- so without this condition a new workflow file could declare
    `run-approval-preview` and assume this role. The file is pinned with a `StringLike`
    whose only wild segment is after the `@`, which is the ref, which is the one thing this
    role exists to leave free.
    """
    condition = _trust_condition()
    workflow_ref = condition["StringLike"]["token.actions.githubusercontent.com:job_workflow_ref"]

    assert workflow_ref == "edu-llm/platform/.github/workflows/submit-run.yml@*"
    assert workflow_ref.count("*") == 1
    assert workflow_ref.split("@")[0].endswith("/submit-run.yml")
    # The two ids are what a fork cannot present, and they stay under StringEquals.
    equals = condition["StringEquals"]
    assert equals["token.actions.githubusercontent.com:repository_owner_id"] == "306859726"
    assert equals["token.actions.githubusercontent.com:repository_id"] == "1311508598"
    assert equals["token.actions.githubusercontent.com:aud"] == "sts.amazonaws.com"


def test_the_preview_environment_is_not_one_of_the_admission_gates() -> None:
    """The two enumerations must stay disjoint, in both directions.

    `infra/iam/admission-role.yaml` carries a paragraph refusing a fifth OIDC role on the
    ground that it would hold a second copy of the environment enumeration, and that a
    fourth environment added to one list and not the other fails quietly. This role is that
    fifth role, and the answer to the objection is that the two lists share no member: the
    admission role must never accept a preview subject, because a preview has no reviewer
    and admission is where a reviewed submission goes; and this role must never accept a
    production gate, because those subjects are minted on `main` where the ceiling here
    would be a demotion nobody asked for.
    """
    admission_role = next(iter(iam_roles(load_template(ADMISSION_TEMPLATE_PATH))))
    admission_subjects = set(
        walk_strings(admission_role["AssumeRolePolicyDocument"]["Statement"][0]["Condition"])
    )
    preview_subjects = set(walk_strings(_trust_condition()))

    assert PREVIEW_ENVIRONMENT not in set(ApprovalEnvironment)
    assert PREVIEW_SUBJECT in preview_subjects
    assert PREVIEW_SUBJECT not in admission_subjects
    for gate in ApprovalEnvironment:
        accepted = f"repo:edu-llm@306859726/platform@1311508598:environment:{gate.value}"
        assert accepted in admission_subjects, gate
        assert accepted not in preview_subjects, gate


def test_the_role_may_submit_a_job_and_do_nothing_else_at_all() -> None:
    """Mutation: add a second action, or a second statement.

    Asserted exactly rather than approximately, for the reason
    `infra/iam/image-resolver-role.yaml` gives about its own two reads: a role whose trust
    condition is looser than every other role here is affordable only while its grant is
    this small, so the grant is the thing that has to fail on a change.
    """
    statements = _statements()

    assert len(statements) == 1
    assert statements[0]["Effect"] == "Allow"
    assert statement_actions(statements[0]) == ["batch:SubmitJob"]


def test_the_role_reaches_the_cheapest_cpu_queue_and_no_other_queue_in_the_account() -> None:
    """Mutation: add a GPU queue ARN, which is how a preview becomes an H100 hour.

    The queue is read out of `config/execution-targets.yaml` rather than written here twice,
    because the property is a relationship between the two files: the CPU target is whatever
    that file says is backed by CPU, and a role scoped to a queue name that stopped being
    the CPU one is a role scoped to nothing. Every other target in that file is GPU and none
    of them may appear.
    """
    catalog = load_yaml(EXECUTION_TARGETS_PATH, ExecutionTargetCatalog)
    cpu_queues = {
        target.job_queue
        for target in catalog.targets
        if target.compute_profile.startswith("cpu-")
    }
    gpu_queues = {
        target.job_queue
        for target in catalog.targets
        if not target.compute_profile.startswith("cpu-")
    }
    reachable = statement_resources(_statements()[0])

    assert cpu_queues == {CPU_QUEUE}, (
        "config/execution-targets.yaml no longer backs exactly one CPU profile, so "
        "'the cheapest CPU queue' has stopped naming one thing and this role needs a "
        "deliberate decision rather than an updated assertion"
    )
    prefix = "arn:${AWS::Partition}:batch:${AWS::Region}:${AWS::AccountId}"
    assert reachable == [
        f"{prefix}:job-queue/{CPU_QUEUE}",
        f"{prefix}:job-definition/{CPU_QUEUE}-run",
        f"{prefix}:job-definition/{CPU_QUEUE}-run:*",
    ]
    for queue in gpu_queues:
        assert not [arn for arn in reachable if queue in arn], queue


def test_nothing_in_the_template_reaches_a_service_this_role_has_no_business_in() -> None:
    """Mutation: hand it `states:StartExecution`, which is the tempting one.

    It is tempting because it is what `infra/iam/admission-role.yaml` holds, and putting a
    preview back inside admission sounds strictly safer. It is the opposite: StartExecution
    takes the compute profile from its input, no IAM condition can see inside that input,
    and the states role behind the machine enumerates all sixteen queues. So the one grant
    that would restore admission is also the one that removes the ceiling.

    The account id check is the same one every template test here makes, and it is not
    about this role in particular.
    """
    granted = {action for statement in _statements() for action in statement_actions(statement)}
    services = {action.split(":", 1)[0] for action in granted}

    assert services == {"batch"}
    for forbidden in ("states:", "s3:", "iam:", "secretsmanager:", "ecr:", "sts:", "logs:"):
        assert not [action for action in granted if action.startswith(forbidden)], forbidden
    # TerminateJob and CancelJob belong to the run canceller and its own principal.
    assert granted == {"batch:SubmitJob"}
    # The two ids in the trust policy are nine and ten digits; an account id is twelve.
    assert not [
        value for value in walk_strings(load_template(TEMPLATE_PATH)) if ACCOUNT_LITERAL.search(value)
    ]
