"""The one job allowed to put a message on the notifier queue, and its role.

``notifier_handler`` could render an approval request from the day #337 merged and nothing
ever sent it one, so what a lead received was GitHub's own deployment notification -- a
workflow run name, no cost, no machine, no hours. The producer lives in
``.github/workflows/notify-approval-requested.yml`` and is called from exactly one place.

**Why it is a file rather than a seventh job in submit-run.yml, which is the thing to check
rather than to take on trust.** Both ``infra/iam/admission-role.yaml`` and
``infra/iam/image-resolver-role.yaml`` pin ``job_workflow_ref`` to
``submit-run.yml@refs/heads/main``, and a trust policy cannot distinguish jobs within a
workflow -- the sentence is in the image resolver template. So a job added there holding
``id-token: write`` is a new principal under both, and the role it needs becomes assumable by
``resolve`` and ``deny-unapproved``. A job that comes from a reusable workflow presents the
*called* file as its ``job_workflow_ref``, which closes both directions at once, and the
cases below hold each one.

**What the arrangement costs is that this file is callable from anywhere in the repository,**
so a caller added to any workflow could post into the runs channel. That is checked here
rather than left to review, because the check is one line and the habit is not.
"""

from __future__ import annotations

from typing import Any

import pytest
import yaml
from workflow_support import (
    PROJECT_ROOT,
    aws_commands,
    command_tokens,
    load_workflow,
    only_job,
    step,
    unreal_context_references,
)

WORKFLOWS = PROJECT_ROOT / ".github" / "workflows"
WORKFLOW_PATH = WORKFLOWS / "notify-approval-requested.yml"
CALLER_PATH = WORKFLOWS / "submit-run.yml"
ROLE_TEMPLATE = PROJECT_ROOT / "infra" / "iam" / "notifier-publisher-role.yaml"
NOTIFICATIONS_TEMPLATE = PROJECT_ROOT / "infra" / "notifications.yaml"

#: How this workflow is named by the job that calls it.
LOCAL_REFERENCE = "./.github/workflows/notify-approval-requested.yml"

#: What a trust policy must say to mean this file and nothing else.
JOB_WORKFLOW_REF = (
    "edu-llm/platform/.github/workflows/notify-approval-requested.yml@refs/heads/main"
)
CLAIM = "token.actions.githubusercontent.com"

SEND_STEP = "Put it on the notifier queue"
ASSEMBLE_STEP = "Assemble the approval request"
CREDENTIALS_STEP = "Configure AWS credentials"

ROLE_VARIABLE = "AWS_NOTIFIER_PUBLISHER_ROLE_ARN"


def _load() -> dict[str, Any]:
    return load_workflow(WORKFLOW_PATH)


def _send_job() -> dict[str, Any]:
    return only_job(_load())


def _template() -> dict[str, Any]:
    loaded = yaml.safe_load(ROLE_TEMPLATE.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _role() -> dict[str, Any]:
    resources = _template()["Resources"]
    assert len(resources) == 1, "one template, one role, and nothing else to review"
    role = next(iter(resources.values()))
    assert role["Type"] == "AWS::IAM::Role"
    properties = role["Properties"]
    assert isinstance(properties, dict)
    return properties


def _queue_name_from_infrastructure() -> str:
    """The name of the queue the notifier consumes, read off the template that declares it."""
    resources = yaml.safe_load(NOTIFICATIONS_TEMPLATE.read_text(encoding="utf-8"))["Resources"]
    queues = {
        name: resource["Properties"]["QueueName"]
        for name, resource in resources.items()
        if resource.get("Type") == "AWS::SQS::Queue"
    }
    live = sorted(name for name in queues.values() if not name.endswith(("-dlq", "-dead-letter")))
    assert len(live) == 1, f"expected one queue that is not a dead-letter queue, found {live}"
    return live[0]


# ---------------------------------------------------------------------------------------
# Who may call this
# ---------------------------------------------------------------------------------------


def test_only_the_submission_workflow_calls_this() -> None:
    """Mutation: call it from a second workflow, or from a scheduled one.

    This is the cost of moving the job out of submit-run.yml, and it is the whole of the
    cost: the role trusts this file, so anything able to run it can put a message in the runs
    channel. A caller in ``audit.yml`` would be a nightly job able to announce approvals for
    submissions nobody made, and a caller anywhere reachable by ``workflow_dispatch`` would be
    a person with push access able to write to that channel as the platform.

    Read off every workflow in the directory rather than off a list, so a file added tomorrow
    is checked without anybody remembering to add it here.
    """
    callers = sorted(
        f"{path.name}:{job_name}"
        for path in sorted(WORKFLOWS.glob("*.yml"))
        for job_name, job in (load_workflow(path).get("jobs") or {}).items()
        if isinstance(job, dict) and LOCAL_REFERENCE in str(job.get("uses", ""))
    )

    assert callers == ["submit-run.yml:announce-the-gate"], callers


def test_nothing_but_a_call_can_start_this() -> None:
    """Mutation: add ``workflow_dispatch`` so it can be tried by hand.

    A dispatch trigger on this file is a button that posts an approval request into
    #edullm-runs for any submission a person names, with the platform as the author, and
    holding the credential that does it. The way to try this is a real dispatch of
    submit-run.yml, which is what the end-to-end proof of it was.

    ``schedule`` and ``repository_dispatch`` are the same argument. ``pull_request`` would be
    worse than either, since a fork could reach it.
    """
    triggers = _load()["on"]

    assert set(triggers) == {"workflow_call"}
    assert not triggers["workflow_call"], (
        "an input is a value a caller chooses, and the message is built from the compiled "
        "submission precisely so that nothing chooses what it says"
    )


def test_the_workflow_takes_no_secret_from_its_caller() -> None:
    # `secrets: inherit` on the calling job would hand this file the W&B credential and the
    # rest of them for a job that needs none. The webhook the message is posted with is read
    # by the notifier out of Secrets Manager, inside AWS, and never travels through a runner.
    caller = load_workflow(CALLER_PATH)["jobs"]["announce-the-gate"]

    assert "secrets" not in caller
    assert "with" not in caller


# ---------------------------------------------------------------------------------------
# What the job can reach
# ---------------------------------------------------------------------------------------


def test_the_job_assumes_one_role_and_it_is_this_one() -> None:
    """Mutation: point it at the admission role, which is already in a repository variable.

    Every role this platform gives a workflow is named by a repository variable, so the
    difference between the narrow one and the one that can start a run is a few characters in
    a file. The variable is asserted by name, and the account-side half -- that this role
    holds one action -- is below.
    """
    credentials = step(_send_job(), CREDENTIALS_STEP)

    assert credentials["with"]["role-to-assume"] == f"${{{{ vars.{ROLE_VARIABLE} }}}}"
    assert credentials["with"]["mask-aws-account-id"] is True
    # Fifteen minutes for one SendMessage. The role permits an hour and nothing here wants it.
    assert credentials["with"]["role-duration-seconds"] == 900
    assumed = [
        candidate
        for candidate in _send_job()["steps"]
        if "configure-aws-credentials" in str(candidate.get("uses", ""))
    ]
    assert len(assumed) == 1, "two credentials in one job is two things to review"


def test_no_other_workflow_assumes_the_publisher_role() -> None:
    """Mutation: reuse the variable from a job in submit-run.yml.

    The role trusts this file, so a reference from another one cannot work -- but it fails as
    an AssumeRole denial in a job that has already started, which reads like a broken ARN
    rather than like the control it is. Saying so here means the review catches it.
    """
    elsewhere = sorted(
        path.name
        for path in sorted(WORKFLOWS.glob("*.yml"))
        if path != WORKFLOW_PATH and ROLE_VARIABLE in path.read_text(encoding="utf-8")
    )

    assert elsewhere == []


def test_the_only_aws_call_is_one_send_to_the_notifier_queue() -> None:
    """Mutation: add a ``get-queue-url`` call, or a second send.

    The whole argument for this job is that it does one thing. Read off the scripts rather
    than off the role, because the role is applied by hand from a laptop and the workflow is
    what a merge changes -- a second call added here would work the day somebody widened the
    role for an unrelated reason.
    """
    calls = [
        command
        for candidate in _send_job()["steps"]
        if candidate.get("run")
        for command in aws_commands(str(candidate["run"]))
    ]

    assert [command[:3] for command in calls] == [["aws", "sqs", "send-message"]]


def test_the_send_names_the_region_and_reads_the_body_from_a_file() -> None:
    """Mutation: drop ``--region``, or interpolate the envelope into the argument.

    The region is explicit on every call this repository makes, because a runner has no
    configured default and an absent region is a call that fails after a role has been
    assumed. The body is passed as ``file://`` because the envelope carries a manifest and a
    command a submitter typed; putting that on a command line is the shape that makes quoting
    somebody else's text a security question rather than a formatting one.
    """
    send = step(_send_job(), SEND_STEP)
    tokens = command_tokens(str(send["run"]), "sqs", "send-message")

    assert "--region" in tokens
    body = tokens[tokens.index("--message-body") + 1]
    assert body.startswith("file://"), body
    queue_url = tokens[tokens.index("--queue-url") + 1]
    assert queue_url == "${queue_url}"


def test_the_queue_is_the_one_the_notifier_consumes() -> None:
    """Mutation: rename the queue in ``infra/notifications.yaml`` and not here.

    Nothing connects the literal in this workflow to the template that declares the queue.
    A rename would leave a job that assumes a role, builds a correct envelope, and sends it
    to a queue that does not exist -- and the send is the last thing in the job, so what a
    reader sees is a red step at the end of a submission rather than a renamed queue.
    """
    declared = _queue_name_from_infrastructure()
    send = step(_send_job(), SEND_STEP)

    assert send["env"]["QUEUE_NAME"] == declared
    # And the dead-letter queue is not what this reaches, which is the other half of the same
    # check: a message written there would be evidence of a failure that never happened.
    assert not declared.endswith("dlq")


def test_the_job_holds_no_environment_and_the_permissions_it_needs() -> None:
    """Mutation: add ``environment: run-approval-lead`` because the message is about that gate.

    It would deadlock. This message is what asks a lead to release the gate, so a job waiting
    on that gate would wait for a release nobody had been asked for. The absence is also what
    keeps the subject ref-scoped, which is the claim the admission role refuses -- so even if
    this file were somehow accepted under admission's ``job_workflow_ref`` pin, the subject
    would still not match.
    """
    job = _send_job()

    assert "environment" not in job
    assert job["permissions"] == {"contents": "read", "id-token": "write"}
    assert _load()["permissions"] == {"contents": "read"}


def test_the_checkout_is_the_commit_that_compiled_the_document() -> None:
    # The envelope has to be built by the tree that wrote the document it describes, and
    # `github.sha` in a called workflow is the caller's. `persist-credentials: false` for the
    # reason every checkout here has it: this job holds an AWS credential and has no use for a
    # git one as well.
    checkout = step(_send_job(), "Check out the platform")

    assert checkout["with"]["ref"] == "${{ github.sha }}"
    assert checkout["with"]["persist-credentials"] is False


def test_no_step_runs_after_something_has_failed() -> None:
    # The same property tests/test_phase2_submit_run_workflow.py holds over its own jobs. A
    # status function here would send a message built from a step that did not finish.
    job = _send_job()

    assert "if" not in job
    for candidate in job["steps"]:
        assert "if" not in candidate, candidate.get("name")
        assert "continue-on-error" not in candidate, candidate.get("name")


def test_every_expression_names_something_that_exists() -> None:
    """Mutation: read ``vars.AWS_NOTIFIER_ROLE_ARN``, one word short of the real name.

    GitHub resolves an unknown property on a known context to the empty string rather than
    failing, so a plausible typo is an empty role ARN and a job that fails at the credentials
    step with no indication that a name was wrong.
    """
    # aws-account-id is a documented output of the credentials action, which no run body can
    # be read for. Declared the same way tests/test_phase2_submit_run_workflow.py declares it.
    unreal = unreal_context_references(
        WORKFLOW_PATH, declared_step_outputs={"credentials": ("aws-account-id",)}
    )

    assert unreal == [], unreal


# ---------------------------------------------------------------------------------------
# The role, and the trust that is the point of the arrangement
# ---------------------------------------------------------------------------------------


def test_the_trust_pins_this_file_rather_than_the_workflow_that_calls_it() -> None:
    """Mutation: pin ``submit-run.yml``, because that is the workflow a person dispatches.

    This is the one line the whole shape rests on and the one a reviewer is most likely to
    correct in the wrong direction. A token minted by a job that came from a reusable workflow
    carries the *called* file in ``job_workflow_ref``, so pinning the caller would refuse this
    job outright -- and the fix somebody would reach for is to pin both, which restores every
    widening the arrangement exists to avoid: the role becomes assumable by ``resolve`` and
    ``deny-unapproved``, and the announce job becomes a principal under the admission role.
    """
    statements = _role()["AssumeRolePolicyDocument"]["Statement"]
    assert len(statements) == 1, "one way in"
    equals = statements[0]["Condition"]["StringEquals"]

    assert equals[f"{CLAIM}:job_workflow_ref"] == JOB_WORKFLOW_REF
    assert "submit-run.yml" not in str(equals), (
        "pinning the calling workflow makes this role reachable from every job in it"
    )
    # Not a list, for the reason the publisher role gives: a list under StringEquals means
    # "any of", so a second entry is a second file nobody reviewed as a sender.
    assert isinstance(equals[f"{CLAIM}:job_workflow_ref"], str)
    assert equals[f"{CLAIM}:aud"] == "sts.amazonaws.com"
    assert equals[f"{CLAIM}:repository_owner_id"] == "306859726"
    assert equals[f"{CLAIM}:repository_id"] == "1311508598"
    assert equals[f"{CLAIM}:sub"] == (
        "repo:edu-llm@306859726/platform@1311508598:ref:refs/heads/main"
    )
    assert "StringLike" not in statements[0]["Condition"], (
        "a pattern here would admit a ref or a file this review never saw"
    )


def test_the_pinned_file_is_the_file_that_holds_the_send() -> None:
    """Mutation: rename the workflow and not the template.

    The trust names a path, so a rename revokes the role silently and the failure arrives as
    an AssumeRole denial that reads like a broken ARN. infra/README.md describes this failure
    mode for every trust pin here; this makes it a red test instead of a 05:00 one.
    """
    equals = _role()["AssumeRolePolicyDocument"]["Statement"][0]["Condition"]["StringEquals"]
    pinned = equals[f"{CLAIM}:job_workflow_ref"]

    path, _, ref = pinned.partition("@")
    assert ref == "refs/heads/main"
    assert (PROJECT_ROOT / path.removeprefix("edu-llm/platform/")).is_file(), pinned
    assert path.endswith(WORKFLOW_PATH.name)


def test_the_role_may_send_to_one_queue_and_do_nothing_else() -> None:
    """Mutation: widen the resource to ``sbsandbox-intern-edullm-notifier*``.

    One character, and it covers the dead-letter queue beside it. A role able to write there
    could manufacture the evidence of a failure that never happened, in the one place
    somebody looks when they have been told the notifier stopped working. The prefix is also
    the shape that would silently pick up a queue added later.
    """
    policies = _role()["Policies"]
    assert len(policies) == 1, "a second inline policy is a second place to add permissions"
    statements = policies[0]["PolicyDocument"]["Statement"]
    assert len(statements) == 1

    assert statements[0]["Action"] == "sqs:SendMessage"
    resource = statements[0]["Resource"]["Fn::Sub"]
    assert resource.endswith(f":{_queue_name_from_infrastructure()}"), resource
    assert "*" not in resource, resource
    assert "Condition" not in statements[0], (
        "a condition on this call is the Stack 4 shape: SendMessage supplies no key to match"
    )


def test_the_role_is_bounded_and_cannot_outlive_a_job() -> None:
    # The two properties tests/test_phase1_infrastructure.py holds over every template under
    # infra/iam/, asserted here as well because this is the file a reader of this change opens.
    properties = _role()

    assert properties["RoleName"] == "sbsandbox-intern-edullm-notifier-publisher"
    assert properties["PermissionsBoundary"] == {
        "Fn::Sub": "arn:${AWS::Partition}:iam::${AWS::AccountId}:policy/InternSandboxBoundary"
    }
    assert properties["MaxSessionDuration"] <= 3600


def test_the_stack_this_is_applied_as_is_declared_and_needs_a_laptop() -> None:
    """Mutation: apply it under a name the table does not carry.

    ``tools/verify_deployed_stacks.py`` reads that table and reports anything in the account
    that is not in it, so a stack applied under another name is a role nothing reconciles
    against ``main``. It also has to resolve to a laptop apply, because the deployer role
    holds no ``iam:CreateRole`` and no workflow here can apply an IAM stack.
    """
    from edullm_platform.stack_templates import applied_from_a_laptop, stack_for_template

    stack = stack_for_template("infra/iam/notifier-publisher-role.yaml")

    assert stack == "sbsandbox-intern-edullm-notifier-publisher-iam"
    assert applied_from_a_laptop(stack)
    # And the audit reader can read its deployed template, without which the nightly
    # reconciliation reports a denial rather than a comparison.
    audit = (PROJECT_ROOT / "infra" / "iam" / "audit-reader-role.yaml").read_text(encoding="utf-8")
    assert f"stack/{stack}/*" in audit


@pytest.mark.parametrize("absent", ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:PurgeQueue"])
def test_the_producer_cannot_take_a_message_off_the_queue(absent: str) -> None:
    """Mutation: add a receive so the job can confirm its own message arrived.

    It is the obvious next feature and it is the one that breaks the delivery guarantee: a
    producer that can receive can take a message off before the notifier sees it, and a queue
    whose depth returns to zero looks exactly like a queue that was drained correctly. The
    confirmation available to this job is the digest the queue answers with, and delivery is
    proved by the dead-letter queue staying empty.
    """
    granted = {
        action
        for statement in _role()["Policies"][0]["PolicyDocument"]["Statement"]
        for action in (
            statement["Action"]
            if isinstance(statement["Action"], list)
            else [statement["Action"]]
        )
    }

    assert granted == {"sqs:SendMessage"}
    assert absent not in granted


def test_the_assemble_step_calls_the_builder_with_nothing_from_a_submitter() -> None:
    """Mutation: pass ``--url`` from a dispatch input, or the run id from the form.

    Every argument comes from the ``github`` context or from the credentials step, and the
    document itself is read from the artifact rather than reconstructed. A dispatch input
    reaching this step would be a value a submitter chose appearing on the message a lead
    reads, which is the one property the builder is written to guarantee.
    """
    assemble = step(_send_job(), ASSEMBLE_STEP)
    supplied = assemble["env"]

    assert "tools/build_approval_envelope.py" in assemble["run"]
    assert "github.event" not in str(supplied), "a dispatch input is a value a submitter typed"
    assert "inputs." not in str(supplied)
    assert set(supplied) == {
        "ACCOUNT_ID",
        "PUBLISH_REGION",
        "SERVER_URL",
        "PLATFORM_REPOSITORY",
        "WORKFLOW_RUN_ID",
        "WORKFLOW_RUN_ATTEMPT",
    }
