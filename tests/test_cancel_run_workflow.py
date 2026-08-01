"""Cancellation, and the check that is the whole of its authorisation.

**The control is in YAML and that is not an accident, but it does mean it needs holding.**
A trust policy cannot see who dispatched a workflow -- every dispatch of a file presents
the same ``sub`` and ``job_workflow_ref`` whoever pressed the button -- and Batch has no
condition key for a job's tags on ``TerminateJob``. So "may this person stop this run"
cannot be written as a grant, and the role the workflow assumes can stop any job on either
queue.

What bounds that is the role's shape rather than its scope, and these tests hold both
halves: the role reaches nothing but describing and stopping jobs, and the workflow refuses
an actor who neither owns the run nor administers the organization.

The sharpest case is the one that looks like a detail. A run admitted before the
``edullm:submitter`` tag existed has no tag, the AWS CLI renders that absence as the string
``None``, and an actor is compared against it. If that comparison were allowed to succeed
for any input, a missing record would become an authorisation.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_PATH = PROJECT_ROOT / ".github" / "workflows" / "cancel-run.yml"
ROLE_PATH = PROJECT_ROOT / "infra" / "iam" / "run-canceller-role.yaml"


@pytest.fixture(scope="module")
def workflow() -> dict[str, Any]:
    parsed: dict[str, Any] = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
    return parsed


@pytest.fixture(scope="module")
def role() -> dict[str, Any]:
    class Loader(yaml.SafeLoader):
        pass

    def multi(loader: yaml.Loader, suffix: str, node: yaml.Node) -> Any:
        if isinstance(node, yaml.ScalarNode):
            return {f"Fn::{suffix}": loader.construct_scalar(node)}
        if isinstance(node, yaml.SequenceNode):
            return {f"Fn::{suffix}": loader.construct_sequence(node)}
        return {f"Fn::{suffix}": loader.construct_mapping(node)}

    Loader.add_multi_constructor("!", multi)
    template = yaml.load(ROLE_PATH.read_text(encoding="utf-8"), Loader=Loader)
    resource: dict[str, Any] = next(
        value
        for value in template["Resources"].values()
        if value["Type"] == "AWS::IAM::Role"
    )
    return resource["Properties"]


def test_the_canceller_can_stop_a_job_and_do_nothing_else(role: dict[str, Any]) -> None:
    """Mutation: add batch:SubmitJob, which looks harmless beside a cancel grant.

    It is not. A role that can cancel *and* submit is a role that can replace somebody's
    run with its own, which is worse than either power alone -- and because the
    authorisation for cancelling is a workflow check rather than a policy, the blast radius
    of a mistake in that check is exactly the set of actions granted here.

    Asserted as an exact set rather than an absence list, so an action added later has to
    be argued for in this test rather than merely not-forbidden.
    """
    granted = {
        action
        for policy in role["Policies"]
        for statement in policy["PolicyDocument"]["Statement"]
        for action in (
            statement["Action"]
            if isinstance(statement["Action"], list)
            else [statement["Action"]]
        )
    }

    assert granted == {"batch:DescribeJobs", "batch:ListJobs", "batch:TerminateJob"}


def test_stopping_a_job_is_confined_to_this_platforms_two_queues(role: dict[str, Any]) -> None:
    """Mutation: drop the queue condition, leaving TerminateJob on job/*.

    This is a shared sandbox account with other people's Batch estates in it. Without the
    condition this role could stop anything anybody in the account is running, and the
    workflow check above it only ever asks about runs this platform submitted.
    """
    terminate = [
        statement
        for policy in role["Policies"]
        for statement in policy["PolicyDocument"]["Statement"]
        if "batch:TerminateJob" in (
            statement["Action"] if isinstance(statement["Action"], list) else [statement["Action"]]
        )
    ]

    assert len(terminate) == 1
    queues = terminate[0]["Condition"]["ArnEquals"]["batch:JobQueue"]
    rendered = [queue["Fn::Sub"] for queue in queues]

    assert any("sbsandbox-intern-edullm-cpu" in queue for queue in rendered)
    assert any("sbsandbox-intern-edullm-gpu" in queue for queue in rendered)
    assert len(rendered) == 2


def test_the_role_trusts_only_the_file_that_carries_the_check(role: dict[str, Any]) -> None:
    """Mutation: point job_workflow_ref at submit-run.yml, or widen it.

    The authorisation is a step in one workflow file. If any other file could assume this
    role, the check could be bypassed by adding a job somewhere else -- and the review that
    would have caught it is a review of a different file. Naming this one means a job that
    could skip the check has to be added next to the check.
    """
    conditions = role["AssumeRolePolicyDocument"]["Statement"][0]["Condition"]["StringEquals"]

    assert conditions["token.actions.githubusercontent.com:job_workflow_ref"] == (
        "edu-llm/platform/.github/workflows/cancel-run.yml@refs/heads/main"
    )


def dispatch_inputs(workflow: dict[str, Any]) -> dict[str, Any]:
    """The form fields, reached past YAML's reading of ``on:`` as the boolean true."""
    triggers = workflow.get("on", workflow.get(True))
    inputs: dict[str, Any] = triggers["workflow_dispatch"]["inputs"]
    return inputs


def workflow_step(workflow: dict[str, Any], fragment: str) -> dict[str, Any]:
    return next(
        step
        for step in workflow["jobs"]["cancel"]["steps"]
        if fragment in step.get("name", "")
    )


def test_an_absent_submitter_tag_authorises_nobody(workflow: dict[str, Any]) -> None:
    """THE ONE THAT MATTERS. Mutation: compare the actor to the tag and nothing else.

    A run admitted before the ``edullm:submitter`` tag existed carries no tag, and the AWS
    CLI renders an absent tag as the four characters ``None``. A check that only asked
    ``actor == submitter`` would be correct for every run that has a tag and would refuse
    those, which is safe. The unsafe version is the one that treats a missing tag as
    permissive -- and both are one edit apart.

    So the guard is asserted directly: the string ``None`` is recognised, and ownership is
    set false rather than left to a comparison.
    """
    step = workflow_step(workflow, "Refuse a cancellation")
    body = step["run"]

    assert '"None"' in body, (
        "the check does not recognise an absent tag, which the CLI renders as the string "
        "None -- an actor named None would then own every untagged run"
    )
    assert "owns = False" in body


def test_the_check_reads_the_reviewed_roster_rather_than_a_copy(workflow: dict[str, Any]) -> None:
    """Mutation: inline the admin logins into the workflow.

    A second list of who administers this organization is a list that disagrees with the
    first one eventually, and the disagreement is silent until somebody is refused or
    allowed wrongly. config/organization.yaml is the reviewed answer every other part of
    the platform asks.
    """
    body = workflow_step(workflow, "Refuse a cancellation")["run"]

    assert "config/organization.yaml" in body
    assert "OrganizationInventory" in body
    # The two admins today. Named here would be the defect; asserted absent is the check.
    assert "BritishAmericqn" not in body


def test_a_run_that_already_finished_is_not_reported_as_a_failure(
    workflow: dict[str, Any],
) -> None:
    """Mutation: exit non-zero when no job is found.

    A run that has already reached a terminal state cannot be stopped, and that is not a
    fault. Reporting it as one sends somebody looking for a broken cancellation when what
    happened is that their job ended -- and the most likely time to press cancel is exactly
    when a run is finishing.
    """
    body = workflow_step(workflow, "Find the job")["run"]

    assert "not_running=true" in body
    # The message stopped saying "already reached a terminal state" when the search grew to
    # cover SUCCEEDED and FAILED: a job that ended an hour ago is now found and reported, so
    # the only thing not finding one can mean is that Batch has stopped listing it. Saying
    # the run is finished would be a guess, and the wrong one for a run id that was mistyped.
    assert "out of the window" in body
    assert "exit 0" in body


def test_the_reason_reaches_the_termination_with_the_name_of_who_asked(
    workflow: dict[str, Any],
) -> None:
    """Mutation: terminate with a bare reason, or none.

    lifecycle_projection reads the termination reason to tell an operator's cancellation
    from a failure, so a missing reason turns a deliberate stop into an unexplained one in
    the run's history. The actor is the half a bare reason leaves out: "cancelled" and
    "cancelled by whom" are different records.
    """
    body = workflow_step(workflow, "Stop it")["run"]

    assert "terminate-job" in body
    assert re.search(r'--reason "Cancelled by \$\{ACTOR\}', body)


def test_looking_at_a_run_is_what_a_dispatch_does_unless_it_is_told_otherwise(
    workflow: dict[str, Any],
) -> None:
    """Mutation: default `stop` to true, or drop the input and always stop.

    The question people arrive with is what their run is doing, and until this input existed
    the only button that answered anything was the one that ends the run. A default of true
    would mean a mis-dispatch costs somebody twelve hours; a default of false costs a page of
    output.
    """
    stop = dispatch_inputs(workflow)["stop"]

    assert stop["type"] == "boolean"
    assert stop["default"] is False
    assert stop["required"] is False


def test_nothing_in_the_account_changes_unless_stop_was_ticked(workflow: dict[str, Any]) -> None:
    """Mutation: gate only the terminate call, leaving the entitlement check unconditional.

    Both are gated, and the entitlement one matters as much: it fails the job for somebody
    looking at a colleague's run, which turns a read into a refusal and teaches people that
    looking is something they are not allowed to do.
    """
    for step_name in ("Refuse a cancellation the actor is not entitled to make", "Stop it"):
        condition = workflow_step(workflow, step_name)["if"]
        assert "inputs.stop" in condition, step_name

    # And the report is not gated, because somebody about to stop twelve hours of work should
    # see what they are stopping in the same output.
    assert "inputs.stop" not in workflow_step(workflow, "Say what the run is doing")["if"]


def test_the_report_names_why_a_job_is_not_running_and_not_only_that_it_is_not(
    workflow: dict[str, Any],
) -> None:
    """Mutation: report `status` alone.

    Batch says RUNNABLE both for a job waiting on capacity and for one asking for more vCPU
    than any instance in the environment has, and the difference lives in statusReason. The
    container's own reason is the other half: an exit code with no reason beside it is the
    report that sends somebody to read three-gigabyte logs for a message Batch already had.
    """
    body = workflow_step(workflow, "Say what the run is doing")["run"]

    for field in ("statusReason", "exitCode", "logStreamName", "attempts"):
        assert field in body, field


def test_stopping_without_a_reason_is_refused_rather_than_recorded_empty(
    workflow: dict[str, Any],
) -> None:
    """Mutation: make `reason` optional on the form and pass it through.

    It had to become optional on the form -- looking at a run needs no reason and requiring
    one would make the common path ask for a justification it never uses. That moves the
    requirement here, and dropping it would let a termination be recorded with an empty
    reason, which is exactly what lifecycle_projection cannot tell from a failure.
    """
    assert dispatch_inputs(workflow)["reason"]["required"] is False

    body = workflow_step(workflow, "Stop it")["run"]
    assert "cancel_reason_missing" in body
    assert body.index("cancel_reason_missing") < body.index("terminate-job"), (
        "the reason has to be checked before the job is stopped, not after"
    )


def test_the_run_id_is_checked_before_any_credential_is_taken(workflow: dict[str, Any]) -> None:
    """Mutation: validate after configuring credentials, which reads as equivalent.

    It is not. A malformed id is a typing mistake, and answering it needs no AWS at all --
    so taking a credential first means every fat-fingered dispatch assumes a role that can
    stop jobs. Ordering the steps this way keeps the credential out of the common mistake.
    """
    steps = workflow["jobs"]["cancel"]["steps"]
    names = [step.get("name", "") for step in steps]

    validated = next(index for index, name in enumerate(names) if "Check the run id" in name)
    credentialed = next(index for index, name in enumerate(names) if "AWS credentials" in name)

    assert validated < credentialed


def test_a_missing_canceller_role_is_named_rather_than_reported_as_no_credentials(
    workflow: dict[str, Any],
) -> None:
    """Mutation: drop the guard and let configure-aws-credentials fail on an empty role.

    That is what happened. The role comes from infra/iam/run-canceller-role.yaml, which is
    applied from a laptop because the deployer role holds no iam:CreateRole, and it has not
    been applied -- so AWS_RUN_CANCELLER_ROLE_ARN is unset and every dispatch fails.

    It failed unhelpfully. An empty role-to-assume produces "Credentials could not be loaded,
    please check your action inputs", which reads as a broken secret or an expired federation
    and sends the reader to the OIDC configuration, which is fine. The cause is one stack that
    was never applied, and a researcher cannot tell that from the message.
    """
    steps = workflow["jobs"]["cancel"]["steps"]
    names = [step.get("name", "") for step in steps]
    guard = next(index for index, name in enumerate(names) if "canceller role" in name)
    credentialed = next(index for index, name in enumerate(names) if "AWS credentials" in name)

    assert guard < credentialed, "a guard after the credential step guards nothing"

    body = steps[guard]["run"]
    assert "run_canceller_role_not_deployed" in body
    # The reader needs somewhere to go, not only a diagnosis.
    assert "infra/README.md" in body
    assert "Batch execution" in body, (
        "the guard should name the workflow an admin can read a run through, since that is "
        "the thing the person dispatching this actually wanted"
    )
