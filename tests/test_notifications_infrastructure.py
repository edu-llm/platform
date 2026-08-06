"""The templates, read as YAML and held against the code they deploy.

Every seam here is two files with nothing connecting them: a role name in one template and a
Role property in another, a response type in a mapping and a key in a handler, an artifact
key in a builder and an S3Key in a template. Each pair is compared here, because CloudFormation
checks none of them and the failure mode of every one is a function that deploys and does not
work.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
INFRA = PROJECT_ROOT / "infra"


def template(name: str) -> dict[str, Any]:
    """CloudFormation YAML, parsed with the loader that tolerates its own tags.

    The templates in this repository use the long form, `Fn::Sub` rather than `!Sub`, so
    safe_load reads them without a custom constructor.
    """
    return yaml.safe_load((INFRA / name).read_text(encoding="utf-8"))


def test_the_notifier_role_exists_and_is_bounded() -> None:
    """Every role in this account carries InternSandboxBoundary. Mutation: drop it.

    The boundary is what caps a role created here, and a role without one is a role the
    account's own controls do not reach.
    """
    role = template("iam/notifier-lambda-role.yaml")["Resources"]["NotifierLambdaRole"]

    assert role["Properties"]["RoleName"] == "sbsandbox-intern-edullm-notifier-lambda"
    assert "InternSandboxBoundary" in role["Properties"]["PermissionsBoundary"]["Fn::Sub"]
    assert (
        role["Properties"]["AssumeRolePolicyDocument"]["Statement"][0]["Principal"]["Service"]
        == "lambda.amazonaws.com"
    )


def actions_of(role: dict[str, Any]) -> list[str]:
    return [
        action
        for policy in role["Properties"]["Policies"]
        for statement in policy["PolicyDocument"]["Statement"]
        for action in (
            [statement["Action"]] if isinstance(statement["Action"], str) else statement["Action"]
        )
    ]


def statements_acting(role: dict[str, Any], wanted: str) -> list[dict[str, Any]]:
    return [
        statement
        for policy in role["Properties"]["Policies"]
        for statement in policy["PolicyDocument"]["Statement"]
        if wanted
        in (
            [statement["Action"]] if isinstance(statement["Action"], str) else statement["Action"]
        )
    ]


def test_the_notifier_holds_one_batch_read_and_no_batch_write() -> None:
    """Mutation: add batch:DescribeJobs, or any of the three writes.

    ListJobs is here because an array parent's terminal event carries no attempts, so what a
    twenty-cell sweep spent is in Batch and nowhere a notifier can reach in time. Verified
    2026-08-05 against the account: the summary carries startedAt and stoppedAt directly, so
    DescribeJobs buys nothing and is refused.

    The three writes are named rather than covered by a prefix. batch:SubmitJob,
    batch:CancelJob and batch:TerminateJob are what a Batch-reading credential reaches for
    next, and a component that says what happened must not be able to make something happen.
    """
    role = template("iam/notifier-lambda-role.yaml")["Resources"]["NotifierLambdaRole"]
    actions = actions_of(role)

    assert [action for action in actions if action.startswith("batch:")] == ["batch:ListJobs"]
    for refused in (
        "batch:DescribeJobs",
        "batch:SubmitJob",
        "batch:CancelJob",
        "batch:TerminateJob",
    ):
        assert refused not in actions


def test_the_batch_read_is_bounded_by_the_only_condition_it_can_be() -> None:
    """Mutation: drop the region condition.

    batch:ListJobs has no resource type, so Resource must be "*" and aws:RequestedRegion is
    the whole of the bound. That is not a narrowing somebody declined to write, it is not
    expressible, and #227 settled the argument for accepting it on 2026-08-05 when it granted
    the same action to sbsandbox-intern-edullm-nightly-reader under the same condition.
    """
    role = template("iam/notifier-lambda-role.yaml")["Resources"]["NotifierLambdaRole"]
    batch = statements_acting(role, "batch:ListJobs")

    assert len(batch) == 1
    assert batch[0]["Resource"] == "*"
    assert batch[0]["Condition"]["StringEquals"]["aws:RequestedRegion"] == {
        "Fn::Sub": "${AWS::Region}"
    }


def test_the_lineage_read_is_one_prefix_and_carries_no_listing() -> None:
    """Mutation: widen it to the bucket, or add s3:ListBucket over it.

    The submitter is read from intent/{run_id}.json, and the run id is the job name, so the
    key is derived and nothing has to be searched for. A listing of this bucket is the grant
    that lets something enumerate every run this platform has ever admitted, and it buys
    nothing here.

    The other six prefixes stay unreadable. decision/, binding/, events/, result/,
    conflicts/ and submission-failure/ answer questions no message asks, and attempt/ is
    refused for a different reason the plan measures: the recorder writes it in answer to the
    same event, so a notifier reading it finds nothing anyway.
    """
    role = template("iam/notifier-lambda-role.yaml")["Resources"]["NotifierLambdaRole"]
    reads = statements_acting(role, "s3:GetObject")

    assert len(reads) == 1
    assert reads[0]["Resource"]["Fn::Sub"].endswith("sbsandbox-intern-edullm-lineage/intent/*")
    assert not [
        statement
        for statement in statements_acting(role, "s3:ListBucket")
        if "lineage" in str(statement["Resource"])
    ]


def test_the_notifier_role_can_write_nothing_anywhere() -> None:
    """The one property of this role worth holding to a test in its own right."""
    role = template("iam/notifier-lambda-role.yaml")["Resources"]["NotifierLambdaRole"]
    actions = actions_of(role)

    assert not [action for action in actions if action.startswith("s3:Put")]
    assert not [action for action in actions if action.startswith("s3:Delete")]
    assert "secretsmanager:GetSecretValue" in actions
    assert not [
        action
        for action in actions
        if action.startswith("secretsmanager:") and action != "secretsmanager:GetSecretValue"
    ]


def test_the_webhook_grant_names_one_secret_and_carries_no_wildcard() -> None:
    """Mutation: end the resource in `-*`, which is what the W&B grants do.

    They have to: those templates were written before the secrets existed, and Secrets Manager
    appends a six-character suffix at creation, so a pattern is the only thing a template can
    name in advance. This secret was created first, on 2026-08-05, so the exact ARN is
    available and the pattern would be a wildcard bought for nothing. What it would reach is
    every secret whose name begins with this one, which is the class a later
    `...-runs-webhook-staging` falls into without anybody deciding it should.

    The endpoint is the credential here, because a Slack incoming webhook carries its whole
    secret in the URL path, so the read of it is worth pinning harder than an ordinary one.
    """
    role = template("iam/notifier-lambda-role.yaml")["Resources"]["NotifierLambdaRole"]
    reads = statements_acting(role, "secretsmanager:GetSecretValue")

    assert len(reads) == 1
    resource = reads[0]["Resource"]["Fn::Sub"]
    assert resource.endswith(":secret:sbsandbox-intern-edullm-runs-webhook-wL0CYM")
    assert "*" not in resource.removeprefix("arn:${AWS::Partition}")


def test_the_listing_grant_is_narrowed_by_a_prefix_condition() -> None:
    """s3:ListBucket authorizes against the bucket ARN, so the Resource cannot narrow it.

    Mutation: drop the condition. Without it this enumerates every key in the outputs bucket,
    which is every team's output rather than the checkpoints of the run being described. The
    lifecycle role carries the same grant with the same condition and is the precedent.
    """
    role = template("iam/notifier-lambda-role.yaml")["Resources"]["NotifierLambdaRole"]
    listing = statements_acting(role, "s3:ListBucket")

    assert len(listing) == 1
    assert listing[0]["Condition"]["StringLike"]["s3:prefix"] == "teams/*/runs/*/checkpoints/*"


def test_the_deployer_may_pass_the_notifier_role() -> None:
    """Mutation: create the role and forget this.

    lambda:CreateFunction takes a role ARN and the calling principal must be allowed to pass
    it. Without this line the stack in Task 11 fails at CreateFunction with an AccessDenied
    that names PassRole and reads like a broken template.

    The list stays written out in full. A prefix would let this role pass any role that ever
    gets a matching name, including one created later with permissions nobody weighed against
    a deploy credential.
    """
    document = (INFRA / "iam" / "infra-deployer-role.yaml").read_text(encoding="utf-8")

    assert "role/sbsandbox-intern-edullm-notifier-lambda" in document


def test_the_deployed_stack_registry_knows_about_both_new_stacks() -> None:
    """The nightly holds every deployed stack against the template main declares.

    Mutation: deploy a stack and leave it out. A stack nothing compares is a stack that can
    drift from its template in the console with nothing going red.
    """
    from tools.verify_deployed_stacks import STACKS

    assert "sbsandbox-intern-edullm-notifier-iam" in STACKS
    assert STACKS["sbsandbox-intern-edullm-notifier-iam"].template.name == (
        "notifier-lambda-role.yaml"
    )


def test_the_lifecycle_rule_feeds_both_the_recorder_and_the_notifier() -> None:
    """ONE RULE AND TWO TARGETS, WHICH IS THE WHOLE INTEGRATION. Mutation: a second rule.

    The pattern is sixteen job queue ARNs written out, and a rule scoped to a queue that no
    longer exists deploys perfectly and matches nothing forever. A second copy of that
    pattern is a second thing to keep right, and the one that goes stale is the one nobody
    is watching. EventBridge takes several targets, so the notifier is a target.
    """
    rule = template("batch-events.yaml")["Resources"]["LifecycleRule"]
    targets = {target["Id"] for target in rule["Properties"]["Targets"]}

    assert targets == {"lifecycle-queue", "notifier-queue"}


def test_the_notifier_target_carries_its_own_dead_letter_config() -> None:
    """Mutation: point the notifier target at the recorder's dead-letter queue.

    Two failures that mean different things must not land in one place. A lineage record that
    was never written is a hole in the audit trail; a message nobody received is a message
    nobody received. The alarms on them fire at different people.
    """
    rule = template("batch-events.yaml")["Resources"]["LifecycleRule"]
    notifier = next(
        target for target in rule["Properties"]["Targets"] if target["Id"] == "notifier-queue"
    )

    assert "notifier-dlq" in notifier["DeadLetterConfig"]["Arn"]["Fn::Sub"]


def test_the_mapping_honours_the_verdict_the_handler_answers_with() -> None:
    """Mutation: delete FunctionResponseTypes.

    The handler returns a per-message failure list. Lambda ignores that return value entirely
    unless the mapping declares this response type: without it a returned list is an ordinary
    successful return, every message in the batch is deleted, and the failed ones are lost
    with no retry and no dead-letter. At BatchSize 1 the two behave identically, which is
    exactly why it is configured rather than argued away.
    """
    from edullm_platform.notifier_handler import BATCH_ITEM_FAILURES_RESPONSE_TYPE

    mapping = template("notifications.yaml")["Resources"]["NotifierEventSourceMapping"]

    assert mapping["Properties"]["FunctionResponseTypes"] == [BATCH_ITEM_FAILURES_RESPONSE_TYPE]


def test_the_function_names_the_handler_and_the_key_its_builder_produces() -> None:
    """Two seams with nothing between them: the builder writes an object, the template names
    one. Mutation: rename either."""
    from tools.build_notifier_lambda import ARTIFACT_KEY, HANDLER_ENTRY_POINT

    function = template("notifications.yaml")["Resources"]["NotifierFunction"]

    assert function["Properties"]["Handler"] == HANDLER_ENTRY_POINT
    assert function["Properties"]["Code"]["S3Key"] == ARTIFACT_KEY
    assert function["Properties"]["Code"]["S3ObjectVersion"]


def test_the_function_is_told_which_secret_holds_the_webhook_and_never_the_url() -> None:
    """Mutation: put the URL in a template variable.

    A Slack incoming webhook carries its whole credential in the URL path. A Lambda
    environment variable holding one is plaintext in the template, in the console and in
    get-function-configuration, and this repository is public.
    """
    from edullm_platform.notifications.facts import (
        DEFAULT_LINEAGE_BUCKET,
        LINEAGE_BUCKET_VARIABLE,
    )
    from edullm_platform.notifier_handler import (
        CONFIG_DIRECTORY_VARIABLE,
        WEBHOOK_SECRET_VARIABLE,
    )

    variables = template("notifications.yaml")["Resources"]["NotifierFunction"]["Properties"][
        "Environment"
    ]["Variables"]

    assert variables[WEBHOOK_SECRET_VARIABLE] == "sbsandbox-intern-edullm-runs-webhook"
    assert CONFIG_DIRECTORY_VARIABLE not in variables
    assert variables[LINEAGE_BUCKET_VARIABLE] == DEFAULT_LINEAGE_BUCKET
    for value in variables.values():
        assert "https://" not in str(value)


def test_the_notifier_looks_for_its_configuration_where_its_builder_puts_it() -> None:
    """Mutation: set EDULLM_CONFIG_DIRECTORY in the template, or hard-code the default again.

    THIS IS THE CHECK THAT WAS MISSING ON 2026-08-06 AND THE ONLY ONE THAT WOULD HAVE CAUGHT
    IT WITHOUT AN AWS ACCOUNT. ``tests/test_lambda_package_closure.py`` already holds the
    three *filenames* the notifier reads to the three its builder packages, in both
    directions, and it passed throughout the outage -- because a filename is not a path. The
    builder wrote them to ``edullm_platform/config/`` and the handler read
    ``/var/task/config/``, and nothing in the repository compared the two, so every
    invocation of the deployed function raised FileNotFoundError on organization.yaml while
    every check agreed the artifact was correct.

    Two halves, because there are two ways to reintroduce it.

    The template must not set the variable at all. A deployment that sets it overrides the
    only resolution that cannot be wrong, and the value it sets is a string nobody rebuilds
    when the builder's layout changes -- which is exactly the string that caused this.

    The handler's default must land on the builder's prefix. ``PACKAGED_CONFIG_PREFIX`` is
    read from the builder rather than restated, so moving the packaged config moves what this
    expects, and a handler left behind fails here.
    """
    from tools.build_admission_lambda import PACKAGED_CONFIG_PREFIX

    from edullm_platform.notifier_handler import (
        CONFIG_DIRECTORY_VARIABLE,
        DEFAULT_CONFIG_DIRECTORY,
    )

    variables = template("notifications.yaml")["Resources"]["NotifierFunction"]["Properties"][
        "Environment"
    ]["Variables"]
    assert CONFIG_DIRECTORY_VARIABLE not in variables

    # Lambda unpacks the zip at /var/task, so the packaged prefix rooted there is the path
    # the handler must arrive at. The default is derived from the module's own __file__, so
    # rooting it at this checkout's src/ is the same derivation against a different root.
    source_root = PROJECT_ROOT / "src"
    assert DEFAULT_CONFIG_DIRECTORY == source_root / PACKAGED_CONFIG_PREFIX
    assert (
        Path("/var/task") / DEFAULT_CONFIG_DIRECTORY.relative_to(source_root)
        == Path("/var/task") / PACKAGED_CONFIG_PREFIX
    )


def test_the_bucket_the_function_is_told_about_is_the_one_its_role_may_read() -> None:
    """Mutation: point the variable at a bucket the role has no grant on.

    Two files with nothing between them. The role names the bucket in an object ARN and the
    template names it in an environment variable, and a mismatch is an AccessDenied on every
    message, swallowed by the fallback, showing up as a channel where nobody is ever named.
    That is the quietest failure in this whole plan.
    """
    from edullm_platform.notifications.facts import LINEAGE_BUCKET_VARIABLE

    bucket = template("notifications.yaml")["Resources"]["NotifierFunction"]["Properties"][
        "Environment"
    ]["Variables"][LINEAGE_BUCKET_VARIABLE]
    role = template("iam/notifier-lambda-role.yaml")["Resources"]["NotifierLambdaRole"]
    reads = [
        statement["Resource"]["Fn::Sub"]
        for policy in role["Properties"]["Policies"]
        for statement in policy["PolicyDocument"]["Statement"]
        if statement["Action"] == "s3:GetObject"
    ]

    assert reads == [f"arn:${{AWS::Partition}}:s3:::{bucket}/intent/*"]


def test_the_visibility_timeout_is_six_times_the_function_timeout() -> None:
    """Mutation: set the visibility timeout below the function timeout.

    A message becomes visible again while the notifier is still working on it, a second
    invocation posts the same line, and the channel gets the message twice with nothing
    reporting an error.
    """
    resources = template("notifications.yaml")["Resources"]
    visibility = resources["NotifierQueue"]["Properties"]["VisibilityTimeout"]
    timeout = resources["NotifierFunction"]["Properties"]["Timeout"]

    assert visibility >= timeout * 6


def test_something_watches_for_the_notifier_going_quiet() -> None:
    """A notifier that stopped posting looks exactly like a quiet week.

    Mutation: drop the alarms. The whole failure mode of this component is silence, so the
    depth of its dead-letter queue and the age of its backlog are the only two things that
    can tell the difference.
    """
    alarms = {
        name
        for name, resource in template("notifications.yaml")["Resources"].items()
        if resource["Type"] == "AWS::CloudWatch::Alarm"
    }

    assert alarms >= {"NotifierDeadLetterDepthAlarm", "NotifierBacklogAlarm", "NotifierErrorsAlarm"}


def test_the_release_registry_knows_about_the_notifier() -> None:
    from tools.release_lambda import FUNCTIONS

    assert "notifier" in FUNCTIONS
    assert FUNCTIONS["notifier"].s3_key == "notifier/notifier.zip"
    assert FUNCTIONS["notifier"].template.name == "notifications.yaml"
