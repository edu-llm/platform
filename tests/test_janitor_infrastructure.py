"""The three seams between the janitor's schedule, its function and its configuration.

Each of the three fails silently if it is wrong, which is what makes them worth a test. A rule
pointing at nothing runs nothing and reports nothing. A schedule disagreeing with the sweep
interval the settings declare makes the warning-lead check in LaneSettings a statement about a
number nobody uses. And a disabled rule is indistinguishable from a quiet account.
"""

from __future__ import annotations

from pathlib import Path

from edullm_platform.researcher_lane import load_lane_settings
from tests.infrastructure_support import INFRA_ROOT, load_template

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_PATH = INFRA_ROOT / "expiry-janitor.yaml"
WORKFLOW_PATH = PROJECT_ROOT / ".github" / "workflows" / "deploy-phase3-batch.yml"
FUNCTION_NAME = "sbsandbox-intern-edullm-expiry-janitor"
STACK_NAME = "sbsandbox-intern-edullm-janitor"


def resources() -> dict[str, dict[str, object]]:
    return load_template(TEMPLATE_PATH)["Resources"]


def of_type(kind: str) -> dict[str, object]:
    return next(
        value["Properties"]
        for value in resources().values()
        if isinstance(value, dict) and value.get("Type") == kind
    )


def test_the_schedule_is_the_sweep_interval_the_settings_declare() -> None:
    """Mutation: change the rate expression and leave config/reports/researcher-lane.yaml alone.

    LaneSettings refuses a warning lead shorter than the sweep interval, which is the rule that
    makes "warns before it stops anything" hold. That refusal is about the number in the file;
    if the deployed schedule is a different number, the rule guarantees nothing.
    """
    settings = load_lane_settings(PROJECT_ROOT / "config" / "reports" / "researcher-lane.yaml")
    schedule = of_type("AWS::Scheduler::Schedule")

    assert schedule["ScheduleExpression"] == f"rate({settings.sweep_minutes} minutes)"
    assert schedule["State"] == "ENABLED"


def test_the_schedule_window_is_off_so_a_sweep_lands_when_it_says_it_will() -> None:
    """Mutation: set a flexible window, which is the default a console wizard offers.

    A window lets an invocation land up to fifteen minutes late, which is three sweeps of slack
    on a component whose whole promise is that a machine stops near the time its owner named --
    and it would silently break the relationship between the warning lead and the sweep
    interval that LaneSettings enforces.
    """
    assert of_type("AWS::Scheduler::Schedule")["FlexibleTimeWindow"] == {"Mode": "OFF"}


def test_the_function_is_handed_every_number_the_settings_file_declares() -> None:
    """THE SEAM THE ZIP CARRYING NO CONFIGURATION CREATES.
    Mutation: edit config/reports/researcher-lane.yaml and leave the template's variables alone.

    The handler reads these from the environment because the package carries no config file, so
    the file and the template are two copies of three numbers with nothing but this holding them
    together. Compared field by field against the loaded settings rather than against literals,
    so adding a fourth setting fails here until the template carries it.
    """
    settings = load_lane_settings(PROJECT_ROOT / "config" / "reports" / "researcher-lane.yaml")
    variables = of_type("AWS::Lambda::Function")["Environment"]["Variables"]

    assert variables == {
        "EDULLM_DEFAULT_LIFETIME_HOURS": str(settings.default_lifetime_hours),
        "EDULLM_WARNING_LEAD_MINUTES": str(settings.warning_lead_minutes),
        "EDULLM_SWEEP_MINUTES": str(settings.sweep_minutes),
    }


def test_the_schedule_targets_the_function_this_template_creates() -> None:
    """Mutation: point the target at a function name that is not created here.

    A schedule whose target does not exist raises nothing anywhere. It fires every five minutes,
    the delivery fails, and the only place that is visible is a CloudWatch metric nobody is
    watching -- which is a janitor that stops nothing and looks exactly like a quiet account.
    """
    target = of_type("AWS::Scheduler::Schedule")["Target"]

    assert target["Arn"]["Fn::GetAtt"] == ["ExpiryJanitorFunction", "Arn"]
    assert "ExpiryJanitorFunction" in resources()


def test_the_schedule_assumes_the_one_role_that_may_invoke_the_janitor() -> None:
    """THE SEAM THAT REPLACED AN AWS::Lambda::Permission, AND WHY IT IS NOT ONE.
    Mutation: point RoleArn at the sweep role, which is the other one in the same stack.

    A schedule invokes by assuming this role, so a target with no role or the wrong role fires
    every five minutes and is refused every five minutes, which is the same silence as a
    schedule pointing at nothing. The route exists because an Events::Rule target would need
    lambda:AddPermission on the deployer, which infra/iam/infra-deployer-role.yaml excludes.
    """
    target = of_type("AWS::Scheduler::Schedule")["Target"]

    assert target["RoleArn"]["Fn::Sub"].endswith(":role/sbsandbox-intern-edullm-janitor-schedule")


def test_nothing_here_asks_the_deployer_for_a_grant_it_deliberately_lacks() -> None:
    """Mutation: add an AWS::Lambda::Permission back, which is the obvious wiring.

    infra/iam/infra-deployer-role.yaml withholds lambda:AddPermission -- "the deployer creates
    the validator but may neither run it nor change who may run it" -- so a Permission resource
    here deploys nothing and fails the whole workflow at CreateStack. infra/batch-events.yaml
    met the same fork and recorded the rule: a capability added rather than a restriction
    removed. This is that rule holding.
    """
    kinds = {
        value.get("Type") for value in resources().values() if isinstance(value, dict)
    }

    assert "AWS::Lambda::Permission" not in kinds


def test_the_function_runs_the_handler_the_builder_names() -> None:
    """Mutation: change Handler and leave the builder's HANDLER_ENTRY_POINT alone.

    The zip carries only the modules the entrypoint reaches, so a Handler naming a different
    module deploys bytes that do not contain it and fails at cold start on an import.
    """
    from tools.build_janitor_lambda import ARTIFACT_KEY, HANDLER_ENTRY_POINT

    function = of_type("AWS::Lambda::Function")

    assert function["FunctionName"] == FUNCTION_NAME
    assert function["Handler"] == HANDLER_ENTRY_POINT
    assert function["Code"]["S3Key"] == ARTIFACT_KEY


def test_the_code_object_version_is_pinned() -> None:
    """Mutation: drop S3ObjectVersion.

    Without it a new zip under the same key leaves the resource's properties byte-identical, the
    change set comes back empty, and `deploy --no-fail-on-empty-changeset` reports success while
    the old code keeps running. infra/README.md carries the same argument for the other two.
    """
    code = of_type("AWS::Lambda::Function")["Code"]

    assert isinstance(code["S3ObjectVersion"], str)
    assert code["S3ObjectVersion"] != ""


def test_the_function_runs_as_the_janitor_role_and_not_as_another_one() -> None:
    """Mutation: paste the lifecycle recorder's role ARN while copying the template.

    The recorder's role can write lineage objects and the janitor's can stop machines, and the
    two are narrowed against opposite hazards. A function running as the wrong one either fails
    every sweep with an AccessDenied nobody is watching for, or -- the direction that matters --
    holds grants nothing about it was reviewed against.
    """
    role = of_type("AWS::Lambda::Function")["Role"]

    assert role["Fn::Sub"].endswith(":role/sbsandbox-intern-edullm-janitor-lambda")


def test_the_workflow_deploys_this_stack_under_the_name_the_audit_asks_about() -> None:
    """Mutation: add the deploy step under a different stack name.

    tools/verify_deployed_stacks.py asks CloudFormation about a name, and the audit reader is
    granted GetTemplate on that name. A workflow creating the same resources under a second name
    leaves the first reported as declared-and-never-deployed for ever, while a stack nobody has
    a template comparison for runs the sweep.
    """
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

    assert f"--stack-name {STACK_NAME} " in workflow
    assert "--template-file infra/expiry-janitor.yaml" in workflow
