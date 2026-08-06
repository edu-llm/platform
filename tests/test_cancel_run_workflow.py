"""Cancellation, and the check that is the whole of its authorisation.

**The control is in YAML and that is not an accident, but it does mean it needs holding.**
A trust policy cannot see who dispatched a workflow -- every dispatch of a file presents
the same ``sub`` and ``job_workflow_ref`` whoever pressed the button -- and Batch has no
condition key for a job's tags on ``TerminateJob``. So "may this person stop this run"
cannot be written as a grant, and the role the workflow assumes can stop any job this
platform submitted, on any of its queues.

The second half of this module is about the other way this workflow was wrong, which cost
more and was quieter. It searched two of the eleven queues and reported a run on any of the
other nine as no job at all, and it named a log stream that only somebody holding an AWS
credential could read. Both are fixed by reading ``config/execution-targets.yaml`` rather
than by writing names down, so the tests below compare the workflow and the grant against
that file rather than against a list restated here.

What bounds that is the role's shape rather than its scope, and these tests hold both
halves: the role reaches nothing but describing and stopping jobs, and the workflow refuses
an actor who neither owns the run nor administers the organization.

The sharpest case is the one that looks like a detail. A run admitted before the
``edullm:submitter`` tag existed has no tag, the AWS CLI renders that absence as the string
``None``, and an actor is compared against it. If that comparison were allowed to succeed
for any input, a missing record would become an authorisation.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml
from workflow_support import run_step_script, write_stub

from edullm_platform.cli.actions import read_report_sections
from edullm_platform.config import load_yaml
from edullm_platform.contracts.execution import ExecutionTargetCatalog

PROJECT_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_PATH = PROJECT_ROOT / ".github" / "workflows" / "cancel-run.yml"
ROLE_PATH = PROJECT_ROOT / "infra" / "iam" / "run-canceller-role.yaml"
EXECUTION_TARGETS_PATH = PROJECT_ROOT / "config" / "execution-targets.yaml"

RUN_ID = "run_0198f0a1-2b3c-7d4e-8f01-23456789abcd"

#: The stub that drops the locked-environment wrapper and runs the workflow's own heredoc
#: under this interpreter. Three words to shift, because every invocation here reads
#: ``uv run --frozen python``.
UV_PASSTHROUGH = 'shift 3\nexec "${PYTHON_EXECUTABLE}" "$@"\n'


def execution_targets() -> ExecutionTargetCatalog:
    return load_yaml(EXECUTION_TARGETS_PATH, ExecutionTargetCatalog)


def configured_queues() -> set[str]:
    return {target.job_queue for target in execution_targets().targets}


def configured_log_groups() -> set[str]:
    return {target.log_group for target in execution_targets().targets}


def checkout(tmp_path: Path) -> None:
    """The one configuration file these steps read, beside the script that reads it.

    The step reads ``config/execution-targets.yaml`` relative to the working directory, and
    a run body writes itself to disk before it executes -- so running it in the checkout
    would leave a stray file in the repository root every time this ran.
    """
    (tmp_path / "config").mkdir(exist_ok=True)
    shutil.copy2(EXECUTION_TARGETS_PATH, tmp_path / "config" / "execution-targets.yaml")


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


def granted_actions(role: dict[str, Any]) -> set[str]:
    return {
        action
        for policy in role["Policies"]
        for statement in policy["PolicyDocument"]["Statement"]
        for action in (
            statement["Action"]
            if isinstance(statement["Action"], list)
            else [statement["Action"]]
        )
    }


def test_the_canceller_can_stop_a_job_read_its_log_and_do_nothing_else(
    role: dict[str, Any],
) -> None:
    """Mutation: add batch:SubmitJob, which looks harmless beside a cancel grant.

    It is not. A role that can cancel *and* submit is a role that can replace somebody's
    run with its own, which is worse than either power alone -- and because the
    authorisation for cancelling is a workflow check rather than a policy, the blast radius
    of a mistake in that check is exactly the set of actions granted here.

    Asserted as an exact set rather than an absence list, so an action added later has to
    be argued for in this test rather than merely not-forbidden. ``logs:GetLogEvents`` is
    the fourth and it is argued for here rather than only in the template. Naming a log
    stream to somebody who has no AWS credential is not an answer to what their run did,
    and the next step was always an operator reading the end of the log -- which is the
    reading that filed nine credential failures as distributed training bugs. What it costs
    is a disclosure rather than an action, and the two neighbours that would make it worse
    are refused separately below.
    """
    assert granted_actions(role) == {
        "batch:DescribeJobs",
        "batch:ListJobs",
        "batch:TerminateJob",
        "logs:GetLogEvents",
    }


def test_the_log_read_cannot_search_a_group_and_cannot_change_one(role: dict[str, Any]) -> None:
    """Mutation: grant logs:FilterLogEvents, which is the natural way to write "read a log".

    It takes a log group and searches every stream in it, so a read aimed at one run would
    be able to return another run's output -- and this workflow answers a question about one
    named run. ``GetLogEvents`` takes the stream the job itself reported, so the read is
    aimed at one job by construction rather than by the caller being careful.

    Every writing action is refused on a separate ground. A role that can read what a run
    printed and also change it is one that can edit the evidence of the failure it was
    dispatched to explain.
    """
    granted = granted_actions(role)
    logs_actions = {action for action in granted if action.startswith("logs:")}

    assert logs_actions == {"logs:GetLogEvents"}
    for refused in (
        "logs:FilterLogEvents",
        "logs:StartQuery",
        "logs:DescribeLogStreams",
        "logs:DeleteLogGroup",
        "logs:PutRetentionPolicy",
        "logs:PutLogEvents",
    ):
        assert refused not in granted, refused


def test_the_log_grant_names_a_stream_and_carries_no_condition_it_cannot_satisfy(
    role: dict[str, Any],
) -> None:
    """THE STACK 4 LESSON, APPLIED TO THE NEXT GRANT RATHER THAN ONLY RECORDED.

    ``batch:TerminateJob`` was once conditioned on ``batch:JobQueue``, which TerminateJob
    never supplies, so the grant read correctly, deployed cleanly, passed every template
    test and could stop nothing -- and the denial named a missing action rather than an
    unsatisfiable condition. ``iam simulate-principal-policy`` could not separate the two
    either.

    The service authorization reference offers ``logs:GetLogEvents`` exactly one condition
    key, ``aws:ResourceTag/${TagKey}``, and its required resource type is ``log-stream``. A
    log stream cannot be tagged, so that key is never populated for the resource in the
    request, and a condition on it would be the same shape a second time. So this grant
    carries none and narrows by the resource instead, which is a narrowing that is evaluated
    on every call.

    Mutation: add any Condition here. The role would deploy, read back byte-identical to
    this template, and refuse every log read with a message about the action.
    """
    statements = [
        statement
        for policy in role["Policies"]
        for statement in policy["PolicyDocument"]["Statement"]
        if "logs:GetLogEvents" in (
            statement["Action"] if isinstance(statement["Action"], list) else [statement["Action"]]
        )
    ]

    assert len(statements) == 1
    assert "Condition" not in statements[0], (
        "the only condition key GetLogEvents offers is aws:ResourceTag, the resource it is "
        "authorized against is a log stream, and a log stream cannot be tagged -- so a "
        "condition here can never be satisfied and the denial will name the action"
    )
    resources = statements[0]["Resource"]
    assert isinstance(resources, list)
    for resource in resources:
        arn = resource["Fn::Sub"]
        # The stream name is minted by ECS when it starts the task, so it cannot be pinned.
        # What can be is the group it is under, which is the whole narrowing.
        assert arn.endswith(":log-stream:*"), arn
        assert ":log-group:/aws/batch/" in arn, arn


def test_the_log_groups_the_grant_names_are_the_ones_the_configuration_declares(
    role: dict[str, Any],
) -> None:
    """Reads BOTH files. Mutation: promote a queue with a log group of its own.

    ``config/execution-targets.yaml`` decides which log group each queue writes to and this
    template decides which the workflow may read, and nothing in CloudFormation connects
    them -- the same shape ``tests/test_phase3_infrastructure.py`` compares three copies of
    the queue name across.

    Both directions matter. A group the configuration names and this does not is a run whose
    log the workflow silently cannot read, reported to the researcher as an unreadable
    stream. A group this names and the configuration does not is a read of somebody else's
    Batch logs in a shared account, kept alive by a policy nobody revisited.
    """
    statement = next(
        statement
        for policy in role["Policies"]
        for statement in policy["PolicyDocument"]["Statement"]
        if "logs:GetLogEvents" in (
            statement["Action"] if isinstance(statement["Action"], list) else [statement["Action"]]
        )
    )
    reachable = {
        resource["Fn::Sub"].split(":log-group:", 1)[1].removesuffix(":log-stream:*")
        for resource in statement["Resource"]
    }

    assert reachable == configured_log_groups()


def test_stopping_a_job_is_confined_to_the_runs_this_platform_submitted(
    role: dict[str, Any],
) -> None:
    """Mutation: drop the condition, leaving TerminateJob on job/*.

    This is a shared sandbox account with other people's Batch estates in it. Unconditioned,
    this role could stop anything anybody in the account is running, and the workflow check
    above it only ever asks about runs this platform submitted.

    **The condition has to be one TerminateJob can actually satisfy, and the obvious one is
    not.** Scoping by queue is the natural way to write "our jobs" and it cannot work here:
    ``TerminateJob`` takes a job id and a reason, so ``batch:JobQueue`` is never in the
    request context and an ``ArnEquals`` on it matches nothing. A role written that way is
    refused with *no identity-based policy allows the batch:TerminateJob action* -- a message
    that names the missing grant rather than the unsatisfiable condition -- and reads clean
    in every template test, including this one as it used to be written.

    So the shape is asserted rather than the queue names: exactly one statement, conditioned
    on a resource tag, keyed on the one tag ``batch_submit_request`` sets unconditionally.
    The value pattern is asserted too, because a bare presence check would accept a tag
    somebody else writes under the same key.
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
    condition = terminate[0]["Condition"]

    assert "ArnEquals" not in condition, (
        "an ArnEquals here is almost certainly on batch:JobQueue, which TerminateJob never "
        "supplies -- the role would describe and list and stop nothing"
    )
    assert condition == {"StringLike": {"aws:ResourceTag/edullm:run-id": "run_*"}}


def test_the_tag_the_grant_keys_on_is_the_one_every_submission_sets(
    role: dict[str, Any],
) -> None:
    """Reads BOTH the role and a submission. Mutation: rename the tag on either side.

    Nothing else links the policy to the code that writes the tag it keys on. Rename it in
    ``batch_submit_request`` and this role goes on deploying, reads back byte-identical to
    its committed template, passes every other test in this file, and refuses every
    termination -- reporting a missing grant rather than a tag that moved.

    ``edullm:run-id`` rather than ``edullm:submitter`` or ``edullm:experiment``, and the
    request below is built without either of those on purpose. Both are appended only when
    there is a value, so a grant keyed on one of them would be unsatisfiable for exactly the
    runs that record no submitter -- which are the runs nobody can be shown to own, and the
    worst set to be unable to stop.
    """
    from fnmatch import fnmatchcase

    from edullm_platform.execution import batch_submit_request
    from tests.test_phase3_execution import RUN_ID, manifest, target

    terminate = next(
        statement
        for policy in role["Policies"]
        for statement in policy["PolicyDocument"]["Statement"]
        if "batch:TerminateJob" in (
            statement["Action"] if isinstance(statement["Action"], list) else [statement["Action"]]
        )
    )
    ((key, pattern),) = terminate["Condition"]["StringLike"].items()
    tag = key.removeprefix("aws:ResourceTag/")
    assert tag != key, f"the grant is conditioned on {key}, which is not a resource tag"

    tags = batch_submit_request(
        manifest=manifest(),
        target=target(),
        run_id=RUN_ID,
        job_definition=target().job_definition_arn,
    )["Tags"]

    assert tag in tags, (
        f"the grant is conditioned on {tag!r} and a submission does not put that tag on the "
        f"job, so no termination can ever be authorised. A submission tags {sorted(tags)}"
    )
    assert fnmatchcase(tags[tag], pattern), (
        f"a submission tags the job {tag}={tags[tag]!r} and the grant accepts {pattern!r}, "
        "so the condition cannot match"
    )


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
    """Mutation: drop the guard now that the role is deployed and the variable is set.

    Both of those are true and neither is held by anything here. The role comes from
    infra/iam/run-canceller-role.yaml, applied from a laptop because the deployer role holds
    no iam:CreateRole, and AWS_RUN_CANCELLER_ROLE_ARN is a repository setting -- so a stack
    deleted, a variable renamed, or a fork with neither puts this workflow straight back in
    the state the guard exists for, with no diff to review.

    Unguarded it fails unhelpfully. An empty role-to-assume produces "Credentials could not
    be loaded, please check your action inputs", which reads as a broken secret or an expired
    federation and sends the reader to the OIDC configuration. The cause would be one stack
    and one variable, and a researcher cannot tell that from the message.
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


def test_the_run_id_can_be_left_blank_because_no_dropdown_is_possible(
    workflow: dict[str, Any],
) -> None:
    """The field people arrive without, and the reason it is a field rather than a menu.

    ``workflow_dispatch`` choice options are static text in the file, so a dropdown listing
    the dispatcher's own jobs cannot be built. Optional plus a look-up is the nearest thing
    available, and a required field is what it replaces.
    """
    run_id = dispatch_inputs(workflow)["run_id"]

    assert run_id.get("required") is False
    assert run_id.get("default") == ""
    assert "blank" in run_id["description"].lower()


def test_a_blank_run_id_finds_the_runs_belonging_to_whoever_dispatched(
    workflow: dict[str, Any],
) -> None:
    """The look-up keys on the tag the authorisation step already trusts.

    Reading ownership from the job rather than from the lineage store is what keeps this
    free of any new permission, and it is the same fact the refusal below reads. A look-up
    that keyed on anything else would be a second, quieter answer to who owns a run.
    """
    body = workflow_step(workflow, "Work out which run")["run"]

    assert "edullm:submitter" in body
    assert "ACTOR" in body
    assert "describe-jobs" in body, "tags do not come back on a list, only on a describe"


def test_stopping_still_names_its_run_rather_than_guessing(workflow: dict[str, Any]) -> None:
    """The asymmetry the blank default is only safe because of.

    Reporting on the wrong run costs a page of output. Terminating the wrong one costs
    somebody their work, and "my newest" is most likely to be wrong exactly under a retry,
    where the run somebody means to stop is not the attempt that just started.
    """
    body = workflow_step(workflow, "Check the run id looks like one")["run"]

    assert "stopping_requires_a_run_id" in body
    assert 'STOP' in body


def test_every_step_that_acts_on_a_run_uses_the_resolved_id(workflow: dict[str, Any]) -> None:
    """Mutation: leave one step reading the raw input.

    A step still reading ``inputs.run_id`` would receive the empty string on the path this
    change exists for, and would report on nothing while the step beside it reported on a
    real job. The format check and the look-up itself read the raw input on purpose,
    because deciding whether one was given is their whole job.
    """
    reads_raw = {
        step["name"]
        for step in workflow["jobs"]["cancel"]["steps"]
        if "inputs.run_id" in yaml.safe_dump(step.get("env", {}))
    }

    assert reads_raw == {
        "Check the run id looks like one",
        "Work out which run, if you did not name one",
    }, f"these steps read the raw input where they need the resolved one: {reads_raw}"


def test_two_people_looking_at_their_own_runs_do_not_queue_behind_each_other(
    workflow: dict[str, Any],
) -> None:
    """Mutation: leave the concurrency group keyed on the input alone.

    Blank, every look-up dispatch would land in the group ``cancel-`` and serialise against
    every other, which reads as the workflow hanging rather than as a queue.
    """
    group = workflow["concurrency"]["group"]

    assert "github.run_id" in group, f"a blank run id collapses every dispatch into {group}"


# --------------------------------------------------------------------------------------
# Seam: the queues this searches, against the queues the configuration declares
# --------------------------------------------------------------------------------------


def test_no_queue_name_is_written_into_this_workflow() -> None:
    """Mutation: put the eleven queue names in the loops, which is where two of them were.

    A list here is a second roster. It agrees with ``config/execution-targets.yaml`` on the
    day it is typed and stops agreeing at the next promotion, and the failure is silent in
    the direction that matters -- a run on the queue this file has not heard of is reported
    as a run that does not exist, in the same words a run Batch has stopped listing gets.
    That is what happened. Nine GPU shapes were promoted and this file went on searching two
    queues, so anybody on gpu-4xa10g or gpu-8xh100 was told their run was not there while it
    billed to its ceiling.

    Asserted as an absence of every configured name, so the mutation cannot be half done.
    """
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    queues = configured_queues()

    assert queues, "the configuration names no queue, so this test is measuring nothing"
    for queue in sorted(queues):
        assert queue not in text, (
            f"{queue} is written into cancel-run.yml, so the search is a second roster that "
            "will disagree with config/execution-targets.yaml at the next promotion"
        )


def test_the_enumeration_step_lists_every_configured_queue_and_nothing_else(
    workflow: dict[str, Any], tmp_path: Path
) -> None:
    """Executed rather than read. Mutation: read the workload catalog instead.

    Two configuration files answer questions that sound the same. ``workload-catalog.yaml``
    is a pricing-and-shape document and ``execution-targets.yaml`` is the one that says
    where a run actually goes, and only the second names a queue -- so a step reading the
    first would produce nothing and this workflow would search nowhere.

    Running the body is what makes that visible. Reading it for the file name proves the
    string is present; running it proves the answer is every queue the configuration names.

    CONFIGURED RATHER THAN DEPLOYED, WHICH IS THE COMPARISON AND NOT A CONVENIENCE. The two
    sets differ today: the file names sixteen queues and the account holds eleven, because
    five shapes were merged before their stack was applied. Comparing against the account
    would make this test go red on a working tree whose infrastructure has not landed yet,
    and it would be measuring the wrong thing anyway. The two errors are not the same size.
    A queue this file names and the account lacks answers ``list-jobs`` with an empty list
    and exits zero -- verified against all five undeployed shapes and against a name that is
    not a queue at all -- so it costs one call and finds nothing. A queue the account holds
    and this file has not heard of is a run reported to its owner as one that does not
    exist. Searching the superset is how the second is made impossible, and the first is the
    price.
    """
    checkout(tmp_path)
    stub_bin = tmp_path / "bin"
    write_stub(stub_bin, "uv", UV_PASSTHROUGH)

    result = run_step_script(
        workflow_step(workflow, "List every queue a run could be on")["run"],
        cwd=tmp_path,
        env={
            "RUNNER_TEMP": str(tmp_path),
            "PYTHON_EXECUTABLE": sys.executable,
            "PYTHONPATH": str(PROJECT_ROOT / "src"),
        },
        stub_bin=stub_bin,
    )
    listed = (tmp_path / "queues.txt").read_text(encoding="utf-8").split()

    assert result.returncode == 0, result.stderr
    assert set(listed) == configured_queues()
    # Deduplicated, because the nine GPU shapes each have a queue of their own while several
    # profiles may not, and listing one twice would describe every job on it twice.
    assert len(listed) == len(set(listed))


def test_a_checkout_that_names_no_queue_refuses_rather_than_searching_nowhere(
    workflow: dict[str, Any], tmp_path: Path
) -> None:
    """Mutation: let an empty enumeration through and loop over it zero times.

    A search across no queues finds no job, and this workflow reports that as a run Batch
    has stopped listing -- the same sentence a genuine absence produces. That is the exact
    confusion the enumeration exists to end, so an empty answer has to be loud rather than
    indistinguishable from the ordinary one.
    """
    (tmp_path / "config").mkdir(exist_ok=True)
    (tmp_path / "config" / "execution-targets.yaml").write_text(
        "schema_version: 1\ntargets: []\n", encoding="utf-8"
    )
    stub_bin = tmp_path / "bin"
    write_stub(stub_bin, "uv", UV_PASSTHROUGH)

    result = run_step_script(
        workflow_step(workflow, "List every queue a run could be on")["run"],
        cwd=tmp_path,
        env={
            "RUNNER_TEMP": str(tmp_path),
            "PYTHON_EXECUTABLE": sys.executable,
            "PYTHONPATH": str(PROJECT_ROOT / "src"),
        },
        stub_bin=stub_bin,
    )

    assert result.returncode == 1
    assert "no_execution_targets_configured" in result.stderr


def test_the_search_for_a_named_run_covers_every_queue_the_enumeration_listed(
    workflow: dict[str, Any], tmp_path: Path
) -> None:
    """Executed. Mutation: leave one loop reading a hardcoded pair, which is how this began.

    Both loops in this file searched the same two queues, and either one left behind would
    reproduce the defect for half the dispatches -- the look-up by submitter would miss a
    run the named search found, or the other way round. So the queues each loop actually
    asks Batch about are recorded and compared against the enumeration.
    """
    stub_bin = tmp_path / "bin"
    asked = tmp_path / "asked.txt"
    (tmp_path / "queues.txt").write_text("queue-one\nqueue-two\nqueue-three\n", encoding="utf-8")
    write_stub(
        stub_bin,
        "aws",
        'while [[ $# -gt 0 ]]; do\n'
        '  if [[ "$1" == "--job-queue" ]]; then shift; echo "$1" >> "' + str(asked) + '"; fi\n'
        "  shift\n"
        "done\n",
    )
    summary = tmp_path / "summary.md"
    summary.touch()

    result = run_step_script(
        workflow_step(workflow, "Find the job")["run"],
        cwd=tmp_path,
        env={
            "RUNNER_TEMP": str(tmp_path),
            "GITHUB_OUTPUT": str(tmp_path / "step-output.txt"),
            "GITHUB_STEP_SUMMARY": str(summary),
            "RUN_ID": RUN_ID,
        },
        stub_bin=stub_bin,
    )

    assert result.returncode == 0, result.stderr
    assert set(asked.read_text(encoding="utf-8").split()) == {
        "queue-one",
        "queue-two",
        "queue-three",
    }
    assert "not_running=true" in (tmp_path / "step-output.txt").read_text(encoding="utf-8")


# --------------------------------------------------------------------------------------
# What one refused call does to a search of sixteen queues
# --------------------------------------------------------------------------------------

#: A ``list-jobs`` that can be told to behave like each of the three things the real one
#: does. Silence with exit zero is a queue that holds no matching job, which is also exactly
#: what a queue named in configuration and absent from the account answers -- measured. A
#: non-zero exit is a refusal, which is what throttling looks like from the caller. And a
#: job id on stdout is a hit. Every call records the queue it was asked about, because the
#: property under test is which queues were reached and not what came back from them.
LIST_JOBS_STUB = """
queue=""
while [[ $# -gt 0 ]]; do
  if [[ "$1" == "--job-queue" ]]; then
    shift
    queue="$1"
    echo "${queue}" >> "${ASKED_QUEUES}"
  fi
  shift
done
if [[ -n "${REFUSED_QUEUE:-}" && "${queue}" == "${REFUSED_QUEUE}" ]]; then
  echo "An error occurred (TooManyRequestsException) when calling the ListJobs operation" >&2
  exit 254
fi
if [[ -n "${queue}" && "${queue}" == "${HOLDING_QUEUE:-}" ]]; then
  echo "${HELD_JOB:-}"
fi
"""

THREE_QUEUES = ("queue-one", "queue-two", "queue-three")


def search_for_the_named_run(
    workflow: dict[str, Any],
    tmp_path: Path,
    *,
    queues: tuple[str, ...] = THREE_QUEUES,
    refused_queue: str = "",
    holding_queue: str = "",
    held_job: str = "",
) -> tuple[Any, list[str], str, dict[str, str]]:
    """Run the named-run search, and report which queues it reached and what it said."""
    stub_bin = tmp_path / "bin"
    write_stub(stub_bin, "aws", LIST_JOBS_STUB)
    (tmp_path / "queues.txt").write_text(
        "".join(f"{queue}\n" for queue in queues), encoding="utf-8"
    )
    asked = tmp_path / "asked.txt"
    asked.touch()
    summary = tmp_path / "summary.md"
    summary.touch()
    outputs = tmp_path / "step-output.txt"
    outputs.touch()

    result = run_step_script(
        workflow_step(workflow, "Find the job")["run"],
        cwd=tmp_path,
        env={
            "RUNNER_TEMP": str(tmp_path),
            "GITHUB_OUTPUT": str(outputs),
            "GITHUB_STEP_SUMMARY": str(summary),
            "RUN_ID": RUN_ID,
            "ASKED_QUEUES": str(asked),
            "REFUSED_QUEUE": refused_queue,
            "HOLDING_QUEUE": holding_queue,
            "HELD_JOB": held_job,
        },
        stub_bin=stub_bin,
    )
    written = dict(
        line.split("=", 1)
        for line in outputs.read_text(encoding="utf-8").splitlines()
        if "=" in line
    )
    return result, asked.read_text(encoding="utf-8").split(), summary.read_text(
        encoding="utf-8"
    ), written


def as_a_job_log(printed: str, *, step: str) -> str:
    """One step's output, wrapped the way ``gh run view --log`` hands it to the CLI.

    The job name, the step name, an instant, and then the line. ``read_report_sections``
    strips those three columns, and the step name is what bounds a section, so a fixture
    that dropped them would be testing a reader nothing uses.
    """
    return "\n".join(
        f"cancel\t{step}\t2026-08-06T10:58:1{index % 10}.0000000Z {line}"
        for index, line in enumerate(printed.splitlines())
    )


@pytest.mark.parametrize("refused_queue", ["", "queue-two"])
def test_a_run_batch_cannot_find_reaches_the_terminal_and_not_only_the_page(
    refused_queue: str, workflow: dict[str, Any], tmp_path: Path
) -> None:
    """Mutation: print these two sentences with no heading over them, as they were.

    **THE ANSWER WAS WRITTEN AND NOBODY AT A TERMINAL COULD SEE IT.** ``edullm status``
    reads sections out of this job's log by heading, and these were the only two things this
    workflow prints that carried none -- so an admitted run whose job Batch cannot find
    dispatched a runner, waited, and handed back "the workflow finished success and its
    report named no section this verb reads" with a link. Measured on 2026-08-06 against
    ``run_019fd6b2-6aad``: 73 seconds for no answer at all, on one of the states a
    researcher most needs a sentence for, because a run that was admitted and has no job is
    the one that looks like the platform losing their work.

    Both branches are driven, because a complete search that found nothing and one that was
    partly refused are different facts and the whole point of counting the refusals is that
    the second must not be spoken as the first.

    Read back through ``read_report_sections`` rather than by looking for the heading in the
    summary, because the summary is the half that already worked. The seam this closes is
    between what the workflow writes into its job log and what the CLI can pull back out of
    it, and only driving both halves shows that.
    """
    result, _, summary, outputs = search_for_the_named_run(
        workflow, tmp_path, refused_queue=refused_queue
    )
    read_back = read_report_sections(
        as_a_job_log(result.stdout, step="Find the job, and read whose it is"),
        (RUN_ID, "Runs submitted by", "No runs found"),
    )

    assert result.returncode == 0, result.stderr
    assert outputs["not_running"] == "true"
    assert f"## {RUN_ID}" in summary
    assert read_back, (
        "edullm status can read no section out of this, so a researcher who spent a runner "
        "on it gets a link and no sentence"
    )
    assert "Batch stops listing a job" in read_back or "nobody managed to look" in read_back


def test_a_queue_the_account_does_not_have_costs_one_call_and_ends_nothing(
    workflow: dict[str, Any], tmp_path: Path
) -> None:
    """Executed. The case that made this enumeration look dangerous, and is not.

    ``config/execution-targets.yaml`` names sixteen queues and the account holds eleven,
    because five GPU shapes were merged before their stack was applied. The worry is that
    searching a queue Batch has never heard of would fail the step and take the other
    fifteen down with it. It does not: ``list-jobs`` against a nonexistent queue answers an
    empty list and exits zero, measured against all five and against a name that is not a
    queue at all. So the absent queue is stubbed as silence-and-zero, which is the real
    behaviour rather than a convenient one.

    Mutation: make the enumeration read the account instead of the configuration, to "fix"
    this. It would be a fix for nothing, and it would put the roster back under whatever the
    account happens to hold at dispatch time rather than under a reviewed file.
    """
    result, asked, summary, outputs = search_for_the_named_run(
        workflow,
        tmp_path,
        queues=("queue-absent", "queue-present"),
        holding_queue="queue-present",
        held_job="8ab30ff1",
    )

    assert result.returncode == 0, result.stderr
    assert "queue-absent" in asked, "the absent queue was not even asked about"
    assert outputs["job_id"] == "8ab30ff1", "the search stopped at the queue that answered nothing"
    assert "queue_search_incomplete" not in result.stderr + summary, (
        "an empty answer is not a refusal and must not be reported as one"
    )


def test_a_refused_call_does_not_end_the_search_across_the_remaining_queues(
    workflow: dict[str, Any], tmp_path: Path
) -> None:
    """THE REGRESSION GUARD. Executed. Mutation: drop the guard on either list-jobs call.

    Neither loop caught a failure. Under ``set -euo pipefail`` one refused call ended the
    step, and sixteen queues by seven states is a hundred and twelve sequential calls -- long
    enough that ListJobs throttling is ordinary rather than exceptional. Because the
    enumeration is sorted, ``gpu-1xh100`` is third of sixteen, so a refusal there would have
    skipped nine of the eleven queues that exist and answered "no job" about all of them.
    That is the two-queue defect this file was fixed for, arriving by a different route.

    So the first queue is refused and the job is on the last one. Unguarded, the step exits
    254 at the first call and finds nothing.
    """
    result, asked, _summary, outputs = search_for_the_named_run(
        workflow,
        tmp_path,
        refused_queue="queue-one",
        holding_queue="queue-three",
        held_job="8ab30ff1",
    )

    assert result.returncode == 0, result.stderr
    assert {"queue-two", "queue-three"} <= set(asked), (
        "a refusal on the first queue ended the search over the rest"
    )
    assert outputs["job_id"] == "8ab30ff1"


def test_a_search_that_was_refused_somewhere_is_not_reported_as_a_run_that_does_not_exist(
    workflow: dict[str, Any], tmp_path: Path
) -> None:
    """Executed. Mutation: swallow the refusal with a bare ``|| true``.

    That keeps the search alive, which is the important half, and then hands the reader the
    ordinary sentence -- no job under this run id, Batch stops listing after some days. A
    person who reads that stops looking. It is the same confident wrong answer the two-queue
    bug produced, and it would be reintroduced by the very guard that fixes the abort.

    So a refusal is counted, and a search that could not finish says so instead of
    pronouncing on a run it never reached.
    """
    result, asked, summary, outputs = search_for_the_named_run(
        workflow, tmp_path, refused_queue="queue-one"
    )

    assert result.returncode == 0, result.stderr
    assert set(asked) == set(THREE_QUEUES)
    assert outputs["not_running"] == "true"
    assert "queue_search_incomplete" in result.stderr
    # Seven states on the one refused queue.
    assert "7 of the queue searches were refused" in summary
    assert "out of the window" not in summary, (
        "an incomplete search must not borrow the sentence a complete one uses"
    )


def test_the_list_of_your_own_runs_says_when_it_could_not_be_completed(
    workflow: dict[str, Any], tmp_path: Path
) -> None:
    """Executed, over the other loop. Mutation: guard the search and print nothing.

    A blank dispatch runs this enumeration and then the named search, so it makes twice the
    calls and is twice as likely to be throttled. What it produces is a table offered as
    "your runs", and one silently missing the queues nobody reached is worse than the
    refusal it hides: the reader concludes they have no run rather than that nobody looked.
    """
    checkout(tmp_path)
    stub_bin = tmp_path / "bin"
    write_stub(stub_bin, "uv", UV_PASSTHROUGH)
    write_stub(stub_bin, "aws", LIST_JOBS_STUB)
    (tmp_path / "queues.txt").write_text(
        "".join(f"{queue}\n" for queue in THREE_QUEUES), encoding="utf-8"
    )
    asked = tmp_path / "asked.txt"
    asked.touch()
    summary = tmp_path / "summary.md"
    summary.touch()

    result = run_step_script(
        workflow_step(workflow, "Work out which run")["run"],
        cwd=tmp_path,
        env={
            "RUNNER_TEMP": str(tmp_path),
            "PYTHON_EXECUTABLE": sys.executable,
            "PYTHONPATH": str(PROJECT_ROOT / "src"),
            "GITHUB_OUTPUT": str(tmp_path / "step-output.txt"),
            "GITHUB_STEP_SUMMARY": str(summary),
            "RUN_ID": "",
            "ACTOR": "philote-dev",
            "ASKED_QUEUES": str(asked),
            "REFUSED_QUEUE": "queue-two",
        },
        stub_bin=stub_bin,
    )
    written = summary.read_text(encoding="utf-8")

    assert result.returncode == 0, result.stderr
    assert set(asked.read_text(encoding="utf-8").split()) == set(THREE_QUEUES), (
        "a refusal on the second queue ended the enumeration over the third"
    )
    assert "The search was incomplete." in written
    assert "7 of the queue searches were refused" in written


#: ``describe-jobs``, answered out of a catalogue the test writes. Bash cannot assemble the
#: response this step parses, and a stub that returned a fixed document could not vary the
#: one thing under test here, which is how many jobs come back and whose they are.
DESCRIBE_JOBS_HELPER = """
import json
import os
import sys

catalog = json.load(open(os.environ["JOBS_JSON"]))
arguments = sys.argv[1:]
wanted = []
if "--jobs" in arguments:
    for value in arguments[arguments.index("--jobs") + 1 :]:
        if value.startswith("--"):
            break
        wanted.append(value)
print(json.dumps({"jobs": [catalog[job] for job in wanted]}))
"""

#: The listing stub, which unlike ``LIST_JOBS_STUB`` has to answer both calls the blank
#: path makes. One queue and one state hold every job, so the enumeration is exercised
#: without multiplying the fixture by the seven states it walks.
LIST_AND_DESCRIBE_STUB = """
if [[ "${2:-}" == "describe-jobs" ]]; then
  shift 2
  exec "${PYTHON_EXECUTABLE}" "${DESCRIBE_HELPER}" "$@"
fi
queue=""
state=""
while [[ $# -gt 0 ]]; do
  if [[ "$1" == "--job-queue" ]]; then
    shift
    queue="$1"
  fi
  if [[ "$1" == "--job-status" ]]; then
    shift
    state="$1"
  fi
  shift
done
if [[ "${queue}" == "${HOLDING_QUEUE}" && "${state}" == "${HOLDING_STATE}" ]]; then
  cat "${JOB_IDS_FILE}"
fi
"""


def a_listed_job(index: int, *, submitter: str | None, status: str = "SUCCEEDED") -> dict[str, Any]:
    """One row of the fixture. ``createdAt`` ascends with the index, so a low index is old."""
    job: dict[str, Any] = {
        "jobId": f"job-{index:04d}",
        "jobName": f"run_019fd602-{index:04x}",
        "status": status,
        "jobQueue": "arn:aws:batch:us-east-1:123456789012:job-queue/queue-one",
        "createdAt": 1_754_400_000_000 + index * 60_000,
    }
    if submitter is not None:
        job["tags"] = {"edullm:submitter": submitter}
    return job


def list_my_own_runs(
    workflow: dict[str, Any], tmp_path: Path, jobs: list[dict[str, Any]]
) -> tuple[Any, str]:
    """Run the blank-dispatch listing over a fixed set of jobs, and report what it printed."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    checkout(tmp_path)
    stub_bin = tmp_path / "bin"
    write_stub(stub_bin, "uv", UV_PASSTHROUGH)
    write_stub(stub_bin, "aws", LIST_AND_DESCRIBE_STUB)
    helper = tmp_path / "describe_jobs.py"
    helper.write_text(DESCRIBE_JOBS_HELPER, encoding="utf-8")
    catalog = {job["jobId"]: job for job in jobs}
    (tmp_path / "jobs.json").write_text(json.dumps(catalog), encoding="utf-8")
    (tmp_path / "job-ids.txt").write_text(
        "".join(f"{job}\n" for job in catalog), encoding="utf-8"
    )
    (tmp_path / "queues.txt").write_text(
        "".join(f"{queue}\n" for queue in THREE_QUEUES), encoding="utf-8"
    )
    summary = tmp_path / "summary.md"
    summary.touch()
    (tmp_path / "step-output.txt").touch()

    result = run_step_script(
        workflow_step(workflow, "Work out which run")["run"],
        cwd=tmp_path,
        env={
            "RUNNER_TEMP": str(tmp_path),
            "PYTHON_EXECUTABLE": sys.executable,
            "PYTHONPATH": str(PROJECT_ROOT / "src"),
            "GITHUB_OUTPUT": str(tmp_path / "step-output.txt"),
            "GITHUB_STEP_SUMMARY": str(summary),
            "RUN_ID": "",
            "ACTOR": "philote-dev",
            "ASKED_QUEUES": str(tmp_path / "asked.txt"),
            "HOLDING_QUEUE": "queue-one",
            "HOLDING_STATE": "RUNNING",
            "JOB_IDS_FILE": str(tmp_path / "job-ids.txt"),
            "JOBS_JSON": str(tmp_path / "jobs.json"),
            "DESCRIBE_HELPER": str(helper),
        },
        stub_bin=stub_bin,
    )
    return result, summary.read_text(encoding="utf-8")


def test_a_listing_longer_than_the_table_says_what_the_table_left_out(
    workflow: dict[str, Any], tmp_path: Path
) -> None:
    """Executed. Mutation: drop the sentence and keep the slice, which is how this shipped.

    A blank dispatch on 2026-08-06 omitted ``run_019fd602-4e05`` and said nothing, and the
    named search found it ``RUNNING`` three hours and twenty minutes in. The cap is why:
    the table is the twenty newest by ``createdAt``, the enumeration searches SUCCEEDED and
    FAILED as well as the live states, and a run several hours old therefore sorts below
    every short job finished since. So the row the cap drops first is a long-running job,
    which is the row somebody dispatching a blank listing is most often looking for.

    The oldest job here is the only ``RUNNING`` one, which is that shape exactly. What is
    asserted is not that it appears -- the cap is kept deliberately -- but that the reader
    is told the table is a slice and how many rows are under it.
    """
    jobs = [a_listed_job(0, submitter="philote-dev", status="RUNNING")]
    jobs += [a_listed_job(index, submitter="philote-dev") for index in range(1, 26)]

    result, summary = list_my_own_runs(workflow, tmp_path, jobs)

    assert result.returncode == 0, result.stderr
    assert "This is the 20 newest of 26 and not all of them." in summary
    assert "6 more are yours and are not above" in summary
    assert jobs[0]["jobName"] not in summary, (
        "the fixture no longer exercises the cap, so this asserts nothing about it"
    )


def test_a_listing_that_fits_the_table_claims_nothing_was_left_out(
    workflow: dict[str, Any], tmp_path: Path
) -> None:
    """Mutation: print the slice sentence unconditionally.

    A warning that is always there is one nobody reads, and this one has to mean something
    the two hundred ordinary dispatches do not carry. Twenty is the boundary and is the
    value worth driving, because ``>`` and ``>=`` are the same edit away.
    """
    jobs = [a_listed_job(index, submitter="philote-dev") for index in range(20)]

    result, summary = list_my_own_runs(workflow, tmp_path, jobs)

    assert result.returncode == 0, result.stderr
    assert "not all of them" not in summary
    assert "Runs submitted by" in summary


def test_a_job_with_no_submitter_tag_is_counted_out_loud_rather_than_dropped(
    workflow: dict[str, Any], tmp_path: Path
) -> None:
    """Executed. Mutation: keep the filter and drop the counter, which is how this shipped.

    Leaving an untagged job out is right, and the step comment argues it: a list offered as
    "your runs" that quietly included somebody else's would be worse. What was missing is
    that the reader could not tell the difference between a job nobody looked for and a job
    that was found and could not be attributed. Both read as absence.

    The second half is the branch that matters most, because "No runs found" is the answer
    a person gets when they already suspect something is wrong -- and every job here is one
    the enumeration listed.
    """
    mixed = [a_listed_job(0, submitter="philote-dev")]
    mixed += [a_listed_job(index, submitter=None) for index in range(1, 4)]

    result, summary = list_my_own_runs(workflow, tmp_path, mixed)

    assert result.returncode == 0, result.stderr
    assert "3 listed job(s) carry no submitter tag" in summary
    assert "Runs submitted by" in summary

    result, summary = list_my_own_runs(
        workflow, tmp_path / "none-of-them-mine", [a_listed_job(0, submitter=None)]
    )

    assert result.returncode == 0, result.stderr
    assert "1 listed job(s) carry no submitter tag" in summary
    assert "No runs found" in summary, "the empty branch must still be reached"


def test_a_job_belonging_to_somebody_else_is_not_counted_as_unattributed(
    workflow: dict[str, Any], tmp_path: Path
) -> None:
    """Mutation: count every job that is not the actor's, rather than only the untagged.

    Every queue is enumerated whole, so the listing sees the whole platform's jobs and most
    of them are somebody else's by construction. Counting those would put a four-figure
    number in front of a researcher on every dispatch and say nothing, which is the way a
    real warning gets trained out of a reader.
    """
    jobs = [
        a_listed_job(0, submitter="philote-dev"),
        a_listed_job(1, submitter="ericrcwu001"),
        a_listed_job(2, submitter="ericrcwu001"),
    ]

    result, summary = list_my_own_runs(workflow, tmp_path, jobs)

    assert result.returncode == 0, result.stderr
    assert "carry no submitter tag" not in summary
    assert "Runs submitted by" in summary


# --------------------------------------------------------------------------------------
# The log, which is what the person dispatching this came for
# --------------------------------------------------------------------------------------


def test_the_log_group_is_resolved_from_the_queue_rather_than_guessed(
    workflow: dict[str, Any],
) -> None:
    """Mutation: name a log group in this workflow, as the sentence it replaces did.

    The step this replaces told the reader their stream was under the GPU group "or its CPU
    sibling", which is a guess written into a workflow, and the two are not interchangeable
    -- asking CloudWatch for a stream in the wrong group answers ResourceNotFound. The group
    is a property of the queue, so it is resolved from the same file the search enumerates.
    """
    body = workflow_step(workflow, "Say what the run is doing")["run"]
    text = WORKFLOW_PATH.read_text(encoding="utf-8")

    assert "config/execution-targets.yaml" in body
    assert "jobQueue" in body
    for log_group in sorted(configured_log_groups()):
        assert log_group not in text, (
            f"{log_group} is written into cancel-run.yml, so the workflow has an opinion "
            "about where a queue logs that config/execution-targets.yaml already holds"
        )


def test_the_report_hands_the_group_and_the_stream_to_the_step_that_reads_them(
    workflow: dict[str, Any], tmp_path: Path
) -> None:
    """Executed, because the seam between these two steps is a pair of step outputs.

    A ``steps.`` output that is never written resolves to the empty string rather than
    failing, so a report that stopped writing them would leave the tail step reporting that
    the container has not started -- about a run that finished hours ago. Running the body
    is the only way to see that both values arrive.
    """
    checkout(tmp_path)
    # Under a name of its own, because the step redirects the CLI into
    # ``${RUNNER_TEMP}/job.json`` and the shell truncates that file before the stub runs.
    described = tmp_path / "described.json"
    described.write_text(
        json.dumps(
            {
                "jobs": [
                    {
                        "status": "FAILED",
                        "jobQueue": (
                            "arn:aws:batch:us-east-1:123456789012:job-queue/"
                            "sbsandbox-intern-edullm-gpu-4xa10g"
                        ),
                        "container": {
                            "exitCode": 1,
                            "logStreamName": "gpu-4xa10g-run/default/abc",
                        },
                        "attempts": [{"startedAt": 1}],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    stub_bin = tmp_path / "bin"
    write_stub(stub_bin, "uv", UV_PASSTHROUGH)
    write_stub(stub_bin, "aws", f'cat "{described}"\n')
    summary = tmp_path / "summary.md"
    summary.touch()

    result = run_step_script(
        workflow_step(workflow, "Say what the run is doing")["run"],
        cwd=tmp_path,
        env={
            "RUNNER_TEMP": str(tmp_path),
            "PYTHON_EXECUTABLE": sys.executable,
            "PYTHONPATH": str(PROJECT_ROOT / "src"),
            "GITHUB_OUTPUT": str(tmp_path / "step-output.txt"),
            "GITHUB_STEP_SUMMARY": str(summary),
            "JOB_ID": "3f9d1f1e",
            "RUN_ID": RUN_ID,
        },
        stub_bin=stub_bin,
    )
    assert result.returncode == 0, result.stderr
    outputs = dict(
        line.split("=", 1)
        for line in (tmp_path / "step-output.txt").read_text(encoding="utf-8").splitlines()
    )
    binding = next(
        target
        for target in execution_targets().targets
        if target.job_queue == "sbsandbox-intern-edullm-gpu-4xa10g"
    )

    # The group is the one the configuration gives that queue, which is the shared GPU group
    # rather than one named after the shape.
    assert outputs["log_group"] == binding.log_group
    assert outputs["log_stream"] == "gpu-4xa10g-run/default/abc"
    assert f"| Log group | `{binding.log_group}` |" in summary.read_text(encoding="utf-8")


def test_a_job_that_printed_plenty_is_not_reported_as_having_said_nothing(
    workflow: dict[str, Any], tmp_path: Path
) -> None:
    """Mutation: label ``container.reason`` "Container said", which is what shipped.

    ``container.reason`` is Batch's own short note about why a container is in the state it
    is in -- ``OutOfMemoryError``, ``CannotPullContainerError``, ``Essential container in
    task exited``. It is not one byte of what the program printed, and a job that ran to
    ``SUCCEEDED`` normally carries none, so the row read ``| Container said | nothing |``
    about a run with nine lines waiting in CloudWatch. Two separate runs were read that way
    tonight, and the reader's conclusion -- my job produced no output -- is wrong about a job
    that worked.

    ``edullm logs`` on the same id prints the lines, so the two verbs contradicted each other
    over the same stream. The table now says which of them holds the output.

    Executed rather than read, because the row is inside a heredoc and a string assertion
    against the file would pass on a heredoc that never runs.
    """
    checkout(tmp_path)
    described = tmp_path / "described.json"
    described.write_text(
        json.dumps(
            {
                "jobs": [
                    {
                        "status": "SUCCEEDED",
                        "jobQueue": (
                            "arn:aws:batch:us-east-1:123456789012:job-queue/"
                            "sbsandbox-intern-edullm-gpu-4xa10g"
                        ),
                        # No `reason`, which is what a job that finished normally carries.
                        "container": {
                            "exitCode": 0,
                            "logStreamName": "gpu-4xa10g-run/default/abc",
                        },
                        "attempts": [{"startedAt": 1}],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    stub_bin = tmp_path / "bin"
    write_stub(stub_bin, "uv", UV_PASSTHROUGH)
    write_stub(stub_bin, "aws", f'cat "{described}"\n')
    summary = tmp_path / "summary.md"
    summary.touch()

    result = run_step_script(
        workflow_step(workflow, "Say what the run is doing")["run"],
        cwd=tmp_path,
        env={
            "RUNNER_TEMP": str(tmp_path),
            "PYTHON_EXECUTABLE": sys.executable,
            "PYTHONPATH": str(PROJECT_ROOT / "src"),
            "GITHUB_OUTPUT": str(tmp_path / "step-output.txt"),
            "GITHUB_STEP_SUMMARY": str(summary),
            "JOB_ID": "3f9d1f1e",
            "RUN_ID": RUN_ID,
        },
        stub_bin=stub_bin,
    )
    said = summary.read_text(encoding="utf-8")

    assert result.returncode == 0, result.stderr
    assert "Container said" not in said
    assert "| nothing |" not in said, (
        "a bare 'nothing' in this table is read as 'my program printed nothing', which is "
        "the wrong conclusion about a job that worked"
    )
    # The row is still carried, because when Batch does have a reason it is the whole answer.
    assert "nothing reported" in said
    # And the table says which verb holds the output, since this one does not.
    assert "edullm logs" in said


def test_the_tail_reaches_the_step_summary_and_says_to_read_upward(
    workflow: dict[str, Any], tmp_path: Path
) -> None:
    """Executed, because a heredoc into a summary is easy to get wrong and quiet about it.

    The sentence about reading upward is the deliverable rather than decoration. Eight of
    the nine W&B failures were filed as distributed training bugs by somebody who read the
    last lines of a torch log, and the tail this step prints ends the same way -- so the tail
    has to arrive with the instruction that the cause is above it.
    """
    summary = tmp_path / "summary.md"
    summary.touch()
    stub_bin = tmp_path / "bin"
    write_stub(stub_bin, "uv", UV_PASSTHROUGH)
    write_stub(
        stub_bin,
        "aws",
        "cat <<'JSON'\n"
        + json.dumps(
            {
                "events": [
                    {"timestamp": 1, "message": "CommError: user is not logged in"},
                    {"timestamp": 2, "message": "ProcessGroup is not registered"},
                ]
            }
        )
        + "\nJSON\n",
    )

    result = run_step_script(
        workflow_step(workflow, "Show the last fifty lines")["run"],
        cwd=tmp_path,
        env={
            "RUNNER_TEMP": str(tmp_path),
            "PYTHON_EXECUTABLE": sys.executable,
            "GITHUB_STEP_SUMMARY": str(summary),
            "LOG_GROUP": "/aws/batch/sbsandbox-intern-edullm-gpu",
            "LOG_STREAM": "gpu-run/default/abc",
        },
        stub_bin=stub_bin,
    )
    written = summary.read_text(encoding="utf-8")

    assert result.returncode == 0, result.stderr
    assert "CommError: user is not logged in" in written
    assert "ProcessGroup is not registered" in written
    assert "Read upward from the end" in written


@pytest.mark.parametrize(
    ("probe", "environment", "expected"),
    [
        (
            "the container has not started",
            {"LOG_GROUP": "/aws/batch/sbsandbox-intern-edullm-cpu", "LOG_STREAM": ""},
            "no log stream to read yet",
        ),
        (
            "the queue is not one this checkout names",
            {"LOG_GROUP": "", "LOG_STREAM": "gpu-run/default/abc"},
            "does not name",
        ),
    ],
)
def test_a_run_with_no_readable_log_is_reported_rather_than_failed(
    workflow: dict[str, Any],
    tmp_path: Path,
    probe: str,
    environment: dict[str, str],
    expected: str,
) -> None:
    """Mutation: exit non-zero when there is nothing to read.

    A queued job has no stream and a queue outside this checkout resolves to no group.
    Neither is a fault, and a red dispatch would send somebody looking for a broken workflow
    when what happened is that their job has not started yet. The report above this step is
    already written and is the thing they came for.
    """
    summary = tmp_path / "summary.md"
    summary.touch()

    result = run_step_script(
        workflow_step(workflow, "Show the last fifty lines")["run"],
        cwd=tmp_path,
        env={
            "RUNNER_TEMP": str(tmp_path),
            "GITHUB_STEP_SUMMARY": str(summary),
            **environment,
        },
    )

    assert result.returncode == 0, probe
    assert expected in summary.read_text(encoding="utf-8"), probe


def test_a_refused_log_read_names_the_grant_and_does_not_fail_the_dispatch(
    workflow: dict[str, Any], tmp_path: Path
) -> None:
    """Mutation: fail the job, or print the CLI error.

    The role is applied from a laptop, so this workflow can legitimately be merged before
    the grant exists in the account. Failing then would break the one workflow a researcher
    uses to look at a run, in order to add a feature to it. And the error text is withheld
    rather than printed because a CLI diagnostic names the resource it was refused, and a
    log stream ARN carries the account id into a page anybody on the repository may read.
    """
    summary = tmp_path / "summary.md"
    summary.touch()
    stub_bin = tmp_path / "bin"
    write_stub(
        stub_bin,
        "aws",
        'echo "An error occurred (AccessDeniedException) when calling the GetLogEvents '
        'operation: not authorized on arn:aws:logs:us-east-1:123456789012:log-group:x" >&2\n'
        "exit 254\n",
    )

    result = run_step_script(
        workflow_step(workflow, "Show the last fifty lines")["run"],
        cwd=tmp_path,
        env={
            "RUNNER_TEMP": str(tmp_path),
            "GITHUB_STEP_SUMMARY": str(summary),
            "LOG_GROUP": "/aws/batch/sbsandbox-intern-edullm-gpu",
            "LOG_STREAM": "gpu-run/default/abc",
        },
        stub_bin=stub_bin,
    )
    written = summary.read_text(encoding="utf-8")

    assert result.returncode == 0
    assert "log_tail_unreadable" in result.stderr
    assert "logs:GetLogEvents" in written
    assert "123456789012" not in written + result.stdout + result.stderr


def test_the_exit_code_is_reported_as_a_number_and_never_as_a_platform_stage(
    workflow: dict[str, Any],
) -> None:
    """Mutation: map the exit code onto the staged scheme and print the stage name.

    Two of the sixty-seven failures in the retained window carry a staged code and six carry
    researcher-invented ones in the same numeric range, so a report that read a 76 or an 85
    as a stage of this platform would misdescribe three runs for every one it described. The
    number is printed and the sentence beside it says whose number it is.
    """
    body = workflow_step(workflow, "Say what the run is doing")["run"]

    assert "not a stage" in body
    assert "exitCode" in body
