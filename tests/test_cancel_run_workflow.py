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
