"""What the janitor may do to a machine, and the four things it must not.

The narrowest interesting role in this repository, because it is the only one that acts on
instances a person launched. Every assertion below is about the difference between stopping a
machine and destroying the work on it.
"""

from __future__ import annotations

from edullm_platform.researcher_lane import (
    EXPIRES_AT_TAG_KEY,
    PROJECT_TAG_KEY,
    WARNING_TAG_KEY,
)
from tests.infrastructure_support import INFRA_ROOT, load_template

TEMPLATE_PATH = INFRA_ROOT / "iam" / "janitor-lambda-role.yaml"
SWEEP_ROLE = "sbsandbox-intern-edullm-janitor-lambda"
SCHEDULE_ROLE = "sbsandbox-intern-edullm-janitor-schedule"


def role(name: str) -> dict[str, object]:
    """One role out of this template, chosen by the name it will hold in the account.

    By name rather than by position, because this stack declares two and they are narrowed
    against opposite hazards -- picking whichever CloudFormation happened to list first would
    make every assertion below a statement about an arbitrary one of them.
    """
    resources = load_template(TEMPLATE_PATH)["Resources"]
    return next(
        value["Properties"]
        for value in resources.values()
        if isinstance(value, dict)
        and value.get("Type") == "AWS::IAM::Role"
        and value["Properties"]["RoleName"] == name
    )


def statements(name: str = SWEEP_ROLE) -> list[dict[str, object]]:
    return [
        statement
        for policy in role(name)["Policies"]
        for statement in policy["PolicyDocument"]["Statement"]
    ]


def actions(name: str = SWEEP_ROLE) -> set[str]:
    found: set[str] = set()
    for statement in statements(name):
        value = statement["Action"]
        found.update([value] if isinstance(value, str) else value)
    return found


def test_the_janitor_may_stop_a_machine_and_never_terminate_one() -> None:
    """THE ONE ASSERTION THIS MODULE EXISTS FOR.
    Mutation: add ec2:TerminateInstances.

    system-overview.md draws the janitor as stopping a machine at expiry, and
    docs-frank/reference/decisions.md puts strictness on the irreversible half throughout. A
    stopped machine keeps its root volume and restarts with one command; a terminated one is
    gone, and the researcher whose work was on it has no recourse and no warning that mattered.
    """
    granted = actions()

    assert "ec2:StopInstances" in granted
    assert "ec2:TerminateInstances" not in granted
    assert "ec2:DeleteVolume" not in granted
    assert "ec2:RunInstances" not in granted


def test_the_janitor_may_write_only_its_own_tag() -> None:
    """Mutation: grant ec2:CreateTags unconditionally, or add ec2:DeleteTags.

    A sweeper that can rewrite ExpiresAt can extend a machine's life indefinitely on its own
    authority, and one that can delete tags can remove the evidence it did. The condition is
    what keeps the warning tag the only key it writes.

    The permitted key is compared against WARNING_TAG_KEY rather than against a literal, so a
    handler that starts writing a differently spelled tag fails here instead of failing with an
    AccessDenied inside a scheduled sweep nobody is watching.
    """
    tagging = next(
        one
        for one in statements()
        if "ec2:CreateTags" in ([one["Action"]] if isinstance(one["Action"], str) else one["Action"])
    )
    condition = tagging["Condition"]

    assert "ec2:DeleteTags" not in actions()
    assert condition["ForAllValues:StringEquals"]["aws:TagKeys"] == [WARNING_TAG_KEY]


def test_the_stop_is_conditioned_on_the_two_tags_the_lane_writes() -> None:
    """Mutation: drop the condition, so the grant covers every instance in a shared account.

    The handler filters on these two tags as well, and stating the same rule twice is
    deliberate: the handler's filter is what makes the sweep correct, and this is what makes a
    bug in the handler unable to stop MCAT's instance. Read against the constants the handler
    reads, so the two cannot drift into agreeing about different keys.
    """
    stopping = next(
        one
        for one in statements()
        if "ec2:StopInstances"
        in ([one["Action"]] if isinstance(one["Action"], str) else one["Action"])
    )

    assert stopping["Condition"]["StringLike"] == {
        f"aws:ResourceTag/{PROJECT_TAG_KEY}": "?*",
        f"aws:ResourceTag/{EXPIRES_AT_TAG_KEY}": "?*",
    }


def test_the_janitor_holds_no_s3_and_no_batch_action() -> None:
    """Mutation: add s3:PutObject so the sweep can record itself somewhere.

    The component furthest from any gate -- reached by a schedule rather than by anything a
    person approved -- and the only thing it may do with that distance is stop a machine whose
    owner promised it would be gone. Anywhere it could write is a place a malformed schedule
    could reach. Its record is its own CloudWatch log.
    """
    for action in actions():
        assert not action.startswith("s3:")
        assert not action.startswith("batch:")


def test_the_janitor_can_create_its_own_log_group_and_no_other() -> None:
    """Mutation: widen the logs resource to "*".

    Lambda creates the function's own group on first invocation using this role; without the
    grant the function runs and logs nowhere, which is the failure mode hardest to notice on a
    component whose entire output is a log line. Scoped to this function's group so it can
    write no other -- including the Batch job groups, whose contents it must not alter.
    """
    logging = next(one for one in statements() if "logs:CreateLogGroup" in one["Action"])
    resource = logging["Resource"]["Fn::Sub"]

    assert resource.endswith("log-group:/aws/lambda/sbsandbox-intern-edullm-expiry-janitor:*")


def test_the_schedule_role_may_invoke_one_named_function_and_no_other() -> None:
    """Mutation: scope the invoke to the sbsandbox-intern-edullm-* prefix.

    This role is what EventBridge Scheduler assumes, so its reach is the whole of what a
    schedule can do with it. The prefix every other Lambda grant in this repository uses would
    let a schedule created later invoke the admission validator or the lifecycle recorder --
    which is a way to run the gate, or rewrite lineage, on a timer.

    It exists at all because the deployer must not gain lambda:AddPermission, which is what an
    Events::Rule target would need. infra/batch-events.yaml recorded the rule for this fork: a
    capability added rather than a restriction removed.
    """
    granted = statements(SCHEDULE_ROLE)

    assert len(granted) == 1
    assert granted[0]["Action"] == "lambda:InvokeFunction"
    assert granted[0]["Resource"]["Fn::Sub"].endswith(
        ":function:sbsandbox-intern-edullm-expiry-janitor"
    )
    assert "*" not in granted[0]["Resource"]["Fn::Sub"].rsplit(":", 1)[-1]


def test_the_schedule_role_is_trusted_only_by_the_scheduler_and_only_from_here() -> None:
    """Mutation: drop the aws:SourceAccount condition.

    A service principal with no source condition is trusted from any account's schedule, which
    is the confused-deputy shape EventBridge Scheduler's own documentation warns about. This
    role can invoke a function that stops machines.

    Mutation: write the condition value as ``Ref: AWS::AccountId``, which is the spelling a
    reader reaches for first and which resolves identically in CloudFormation.
    ``edullm_platform.role_drift`` resolves a literal or a plain ``Fn::Sub`` and refuses
    anything else, so a ``Ref`` makes this role uncomparable to its deployed self -- and takes
    every other template-reading test down with it, because they load the whole directory.
    """
    trust = role(SCHEDULE_ROLE)["AssumeRolePolicyDocument"]
    entry = trust["Statement"][0]

    assert entry["Principal"] == {"Service": "scheduler.amazonaws.com"}
    assert entry["Condition"]["StringEquals"]["aws:SourceAccount"] == {
        "Fn::Sub": "${AWS::AccountId}"
    }


def test_neither_janitor_role_can_do_the_other_one_s_job() -> None:
    """Mutation: fold the two into one role that both stops machines and is invocable.

    A single role would be assumable by two services, so anything that could make the scheduler
    assume it could also stop machines directly, and the narrowing on each half would be
    decoration. Keeping them apart is what makes the reach of a passed role readable.
    """
    assert "ec2:StopInstances" not in actions(SCHEDULE_ROLE)
    assert "lambda:InvokeFunction" not in actions(SWEEP_ROLE)
