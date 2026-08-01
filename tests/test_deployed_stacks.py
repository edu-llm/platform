"""That every CloudFormation stack in the account is the template ``main`` declares.

CI compares this repository against itself. Nothing compared the account against this
repository, and the stacks under ``infra/iam/`` are applied by hand from laptops, so the two
can part company with no run log saying they did. On 2026-08-01 they did: an
``s3:DeleteObject`` grant was added to the GPU workload role and applied at 17:11, and at
17:53 the same stack was applied from a branch cut before the grant existed, which took it
back. A change set shows what the template being applied says and not what it omits relative
to what is live, so nothing warned anyone.

**The property this module exists to hold is that a stack the check cannot account for is
reported rather than skipped.** Everything else here is a comparison that can be argued
about; that one is the difference between a check and a check with a hole in it, because the
hole opens exactly where a new stack was added and nobody thought about this file. It is
asserted from both directions -- a deployed stack absent from the table, and a table entry
that is not deployed -- and neither is allowed to be silent.

**The two non-zero exits are not interchangeable.** Exit 1 says the account and ``main``
disagree and sends a reader to a stack; exit 2 says this check did not manage to look and
sends them to a credential or a grant. A reader who cannot tell them apart goes hunting a
deploy on the morning an IAM stack lapsed, so the cases below assert each separately and
assert that neither reason turns up in the other's output.

Nothing here reaches AWS. ``subprocess.run`` is replaced, and the agreement case answers with
the committed template files themselves, so the comparison is exercised against the real
templates rather than against a fixture that has been shaped to pass it.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
import yaml
from infrastructure_support import ACCOUNT_LITERAL, INFRA_ROOT
from workflow_support import WORKFLOWS_ROOT, aws_commands, load_workflow

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TOOL = PROJECT_ROOT / "tools" / "verify_deployed_stacks.py"

#: The stack the 2026-08-01 revert happened to, used wherever a case needs a real one. Its
#: template holds three roles that separate changes touch, which is what made two people
#: overwrite each other there rather than anywhere else.
GPU_ROLES_STACK = "sbsandbox-intern-edullm-phase4-gpu-iam"
GPU_ROLES_TEMPLATE = "infra/iam/batch-gpu-roles.yaml"
GPU_WORKLOAD_ROLE = "BatchGpuWorkloadRole"

#: A name matching the prefix that no table entry claims. Deliberately plausible: the way
#: this check goes blind is somebody adding a sixth phase and a stack to go with it.
UNMAPPED_STACK = "sbsandbox-intern-edullm-phase6-inference"

#: What the CLI prints when a call is refused. The real message continues into the caller's
#: ARN and the resource ARN, and both carry the account id, which is what
#: `test_a_denial_does_not_put_the_account_id_in_the_log` holds the tool to.
DENIED = (
    "An error occurred (AccessDenied) when calling the GetTemplate operation: User: "
    "arn:aws:sts::123456789012:assumed-role/sbsandbox-intern-edullm-nightly-reader/session "
    "is not authorized to perform: cloudformation:GetTemplate on resource: "
    "arn:aws:cloudformation:us-east-1:123456789012:stack/"
    "sbsandbox-intern-edullm-phase4-gpu-iam/9d1f0a10"
)

NOT_FOUND = (
    "An error occurred (ValidationError) when calling the GetTemplate operation: Stack with "
    "id sbsandbox-intern-edullm-phase4-gpu-iam does not exist"
)


def load() -> Any:
    specification = importlib.util.spec_from_file_location("verify_deployed_stacks", TOOL)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    # Registered before it is executed, because `@dataclass` resolves a string annotation by
    # looking the defining module up in sys.modules. A module built from a file path is not
    # there unless it is put there, and what a reader gets instead is an AttributeError from
    # inside dataclasses.py naming neither this file nor the tool.
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def module() -> Any:
    return load()


@pytest.fixture
def agreeing(module: Any) -> dict[str, str]:
    """What the account would answer if every stack were the file ``main`` declares.

    The committed text itself, so the agreement case runs the real templates through the
    real comparison. A fixture written by hand would only prove the comparison agrees with
    whatever shape it was given.
    """
    return {
        name: stack.template.read_text(encoding="utf-8") for name, stack in module.STACKS.items()
    }


def answer_cloudformation_with(
    monkeypatch: pytest.MonkeyPatch,
    module: Any,
    bodies: dict[str, str],
    *,
    refusals: dict[str, tuple[int, str, str]] | None = None,
    statuses: dict[str, str] | None = None,
    also_listed: Sequence[str] = (),
    listing: tuple[int, str, str] | None = None,
) -> list[list[str]]:
    """Stand in for the CLI, answering the listing and each template separately.

    Returns the calls that were made, because a tool that listed the account and then only
    asked about the stacks it already knew would pass most of the cases below.
    """
    refused = refusals or {}
    status_of = statuses or {}
    listed = sorted({*bodies, *refused, *also_listed})
    calls: list[list[str]] = []

    def run(command: Sequence[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(list(command))
        operation = command[2]
        if operation == "list-stacks":
            if listing is not None:
                code, out, err = listing
                return subprocess.CompletedProcess(list(command), code, out, err)
            summaries = [
                {"StackName": name, "StackStatus": status_of.get(name, "UPDATE_COMPLETE")}
                for name in listed
            ]
            payload = json.dumps({"StackSummaries": summaries})
            return subprocess.CompletedProcess(list(command), 0, payload, "")

        assert operation == "get-template", command
        name = command[command.index("--stack-name") + 1]
        if name in refused:
            code, out, err = refused[name]
            return subprocess.CompletedProcess(list(command), code, out, err)
        payload = json.dumps({"TemplateBody": bodies[name]})
        return subprocess.CompletedProcess(list(command), 0, payload, "")

    monkeypatch.setattr(module.subprocess, "run", run)
    return calls


def run_main(
    module: Any,
    capsys: pytest.CaptureFixture[str],
    argv: Sequence[str] = (),
) -> tuple[int, str, str]:
    code = int(module.main(list(argv)))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def without_the_delete_grant(body: str) -> str:
    """The GPU workload role as the account held it between 17:53 and 18:07 on 2026-08-01.

    The reverted apply removed the ``s3:DeleteObject`` Allow and left everything else, which
    is the shape that has to be recognisable: one statement gone from the middle of a list.
    """
    template = yaml.safe_load(body)
    policy = template["Resources"][GPU_WORKLOAD_ROLE]["Properties"]["Policies"][0]
    statements = policy["PolicyDocument"]["Statement"]
    policy["PolicyDocument"]["Statement"] = [
        statement
        for statement in statements
        if not (statement["Effect"] == "Allow" and statement.get("Action") == "s3:DeleteObject")
    ]
    assert len(policy["PolicyDocument"]["Statement"]) == len(statements) - 1
    return str(yaml.safe_dump(template))


def with_a_changed_property(body: str) -> str:
    template = yaml.safe_load(body)
    template["Resources"][GPU_WORKLOAD_ROLE]["Properties"]["MaxSessionDuration"] = 43200
    return str(yaml.safe_dump(template))


def with_an_extra_resource(body: str) -> str:
    template = yaml.safe_load(body)
    template["Resources"]["SomeRoleNobodyReviewed"] = {
        "Type": "AWS::IAM::Role",
        "Properties": {"RoleName": "sbsandbox-intern-edullm-added-by-hand"},
    }
    return str(yaml.safe_dump(template))


# ----------------------------------------------------------------------------------------
# The claim holds
# ----------------------------------------------------------------------------------------


def test_an_account_that_matches_main_everywhere_is_the_whole_check(
    module: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    agreeing: dict[str, str],
) -> None:
    answer_cloudformation_with(monkeypatch, module, agreeing)

    code, out, err = run_main(module, capsys)

    assert code == module.EXIT_OK, err
    assert err == ""
    for name in agreeing:
        assert name in out


def test_every_mapped_stack_is_asked_about_rather_than_a_sample(
    module: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    agreeing: dict[str, str],
) -> None:
    """Mutation: stop at the first stack, or compare only the ones the listing returned.

    A check that reads twenty of twenty-one stacks is a check with one stack nobody looks
    at, and which one that is would be decided by dictionary order rather than by anybody.
    """
    calls = answer_cloudformation_with(monkeypatch, module, agreeing)

    run_main(module, capsys)

    asked = {
        command[command.index("--stack-name") + 1]
        for command in calls
        if command[2] == "get-template"
    }
    assert asked == set(module.STACKS)


def test_the_comparison_survives_the_way_cloudformation_stores_a_template(
    module: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    agreeing: dict[str, str],
) -> None:
    """THE REASON THIS COMPARES PARSED STRUCTURES. Mutation: compare the two texts.

    CloudFormation hands back a template body that is not the bytes it was given. Measured
    on 2026-08-01 against the live account: every non-ASCII character comes back as a
    question mark, so the section sign in a comment in `infra/outputs-bucket.yaml` alone
    would make a text comparison red every night for four of the twenty-one stacks. A check
    that cries wolf is a check that gets muted, which is the same as not having one.

    Re-indented and re-quoted here as well, because a template deployed by an older CLI or
    edited in the console comes back normalised in ways nobody controls.
    """
    reformatted = {
        name: yaml.safe_dump(yaml.safe_load(body), default_flow_style=False, indent=4)
        for name, body in agreeing.items()
    }
    for name, body in reformatted.items():
        assert body != agreeing[name], f"{name} was not actually reformatted"
    answer_cloudformation_with(monkeypatch, module, reformatted)

    code, _, err = run_main(module, capsys)

    assert code == module.EXIT_OK, err


def test_a_template_cloudformation_returns_as_json_is_read_the_same_way(
    module: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    agreeing: dict[str, str],
) -> None:
    """`get-template` answers with a string for a YAML stack and an object for a JSON one.

    Every template here is YAML today, so the object form is the branch that would rot
    unnoticed until somebody converted one and got a comparison against a string.
    """
    as_objects = {name: yaml.safe_load(body) for name, body in agreeing.items()}

    def run(command: Sequence[str], **_: object) -> subprocess.CompletedProcess[str]:
        if command[2] == "list-stacks":
            summaries = [
                {"StackName": name, "StackStatus": "CREATE_COMPLETE"} for name in as_objects
            ]
            return subprocess.CompletedProcess(
                list(command), 0, json.dumps({"StackSummaries": summaries}), ""
            )
        name = command[command.index("--stack-name") + 1]
        payload = json.dumps({"TemplateBody": as_objects[name]})
        return subprocess.CompletedProcess(list(command), 0, payload, "")

    monkeypatch.setattr(module.subprocess, "run", run)

    code, _, err = run_main(module, capsys)

    assert code == module.EXIT_OK, err


# ----------------------------------------------------------------------------------------
# The claim is false
# ----------------------------------------------------------------------------------------


def test_a_grant_the_account_lost_names_the_resource_that_lost_it(
    module: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    agreeing: dict[str, str],
) -> None:
    """THE ONE THAT MATTERS, AND IT IS THE 2026-08-01 REVERT REPLAYED.

    Mutation: report that the stack differs without saying where. At 05:00 a reader who did
    not make the change has a four-hundred-line template and a sentence saying it is wrong,
    and the useful half is which resource and which statement.

    The direction matters as much as the fact. What went wrong was a grant present in `main`
    and absent from the account, so the message has to distinguish that from something added
    out of band; the repair for one is a deploy and for the other it is a conversation.
    """
    reverted = dict(agreeing)
    reverted[GPU_ROLES_STACK] = without_the_delete_grant(agreeing[GPU_ROLES_STACK])
    answer_cloudformation_with(monkeypatch, module, reverted)

    code, _, err = run_main(module, capsys)

    assert code == module.EXIT_DISAGREES
    assert "deployed_stack_is_not_main" in err
    assert GPU_ROLES_STACK in err
    assert GPU_ROLES_TEMPLATE in err
    assert GPU_WORKLOAD_ROLE in err, "the resource is the actionable half"
    assert "s3:DeleteObject" in err, "and so is what the account is missing"
    assert "only in main" in err, "which way the difference runs decides the repair"


def test_one_statement_removed_is_reported_as_one_difference(
    module: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    agreeing: dict[str, str],
) -> None:
    """Mutation: compare lists by index, which is the obvious implementation.

    Removing the third of five statements shifts every statement after it, so a positional
    comparison reports the third, the fourth and a missing fifth: three findings for one
    edit, two of them describing statements nobody touched. The reader then has to work out
    which is the real one, at 05:00, and the message that was supposed to save them the
    reading has cost them more of it.
    """
    reverted = dict(agreeing)
    reverted[GPU_ROLES_STACK] = without_the_delete_grant(agreeing[GPU_ROLES_STACK])
    answer_cloudformation_with(monkeypatch, module, reverted)

    _, _, err = run_main(module, capsys)

    assert err.count("only in main") == 1
    assert "only in the account" not in err


def test_a_changed_property_names_the_property_and_both_values(
    module: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    agreeing: dict[str, str],
) -> None:
    """Mutation: report the resource and stop there.

    A resource is a hundred lines. Naming the property and both sides of it is what turns
    the message into something a reader can act on without opening the console first, and
    both sides are needed because which one is wrong is not always the account.
    """
    changed = dict(agreeing)
    changed[GPU_ROLES_STACK] = with_a_changed_property(agreeing[GPU_ROLES_STACK])
    answer_cloudformation_with(monkeypatch, module, changed)

    code, _, err = run_main(module, capsys)

    assert code == module.EXIT_DISAGREES
    assert f"Resources.{GPU_WORKLOAD_ROLE}.Properties.MaxSessionDuration" in err
    assert "43200" in err
    assert "3600" in err


def test_a_resource_only_the_account_has_is_reported_as_such(
    module: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    agreeing: dict[str, str],
) -> None:
    """A resource nothing in this repository declares, which is the out-of-band shape.

    Two of these have already been found in this account by recapturing roles and comparing:
    an inline policy on the shared CPU workload role that no template declared, and an ECR
    repository on the execution role that no template declared. Both were found by hand.
    """
    extended = dict(agreeing)
    extended[GPU_ROLES_STACK] = with_an_extra_resource(agreeing[GPU_ROLES_STACK])
    answer_cloudformation_with(monkeypatch, module, extended)

    code, _, err = run_main(module, capsys)

    assert code == module.EXIT_DISAGREES
    assert "Resources.SomeRoleNobodyReviewed" in err
    assert "only in the account" in err
    assert "only in main" not in err


def test_every_stack_is_reported_rather_than_the_first_one_that_differs(
    module: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    agreeing: dict[str, str],
) -> None:
    """Mutation: return on the first finding.

    An apply from a stale branch reverts whatever that branch was stale about, which is
    routinely more than one stack: the deploy workflows apply four and six stacks in a run.
    A tool that stopped at the first would have the rest repaired on later mornings after
    later red runs.
    """
    other = "sbsandbox-intern-edullm-phase3-batch-iam"
    changed = dict(agreeing)
    changed[GPU_ROLES_STACK] = with_an_extra_resource(agreeing[GPU_ROLES_STACK])
    changed[other] = with_an_extra_resource(agreeing[other])
    answer_cloudformation_with(monkeypatch, module, changed)

    code, _, err = run_main(module, capsys)

    assert code == module.EXIT_DISAGREES
    assert err.count("deployed_stack_is_not_main") == 2
    assert GPU_ROLES_STACK in err
    assert other in err


# ----------------------------------------------------------------------------------------
# A stack this cannot account for
# ----------------------------------------------------------------------------------------


def test_a_deployed_stack_the_table_does_not_claim_is_a_finding(
    module: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    agreeing: dict[str, str],
) -> None:
    """THE PROPERTY THE WHOLE CHECK RESTS ON. Mutation: iterate the table and ignore the rest.

    That reads as correct and is the hole. The table is written by whoever last thought about
    this file, and the stack it will not contain is the one somebody deploys next -- so a
    check that only compares what it already knows about goes blind precisely where a new
    stack was added, and stays green while doing it. The listing is not there to find the
    stacks to compare; it is there to find the ones that are not being compared.
    """
    answer_cloudformation_with(monkeypatch, module, agreeing, also_listed=[UNMAPPED_STACK])

    code, _, err = run_main(module, capsys)

    assert code == module.EXIT_DISAGREES
    assert "deployed_stack_is_unaccounted_for" in err
    assert UNMAPPED_STACK in err
    # Naming the table is the actionable half: the reader has to decide whether the stack
    # belongs, and either way the next edit is in one file.
    assert "tools/verify_deployed_stacks.py" in err


def test_a_table_entry_that_is_not_deployed_is_also_a_finding(
    module: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    agreeing: dict[str, str],
) -> None:
    """The other direction, and it is not symmetric with the one above.

    A stack in the table and not in the account means either that a template `main` carries
    was never applied, or that somebody deleted a stack this repository still declares. Both
    are statements about the account rather than about this check, which is what puts them on
    exit 1 beside a mismatch rather than on exit 2 beside a denial.
    """
    absent = dict(agreeing)
    del absent[GPU_ROLES_STACK]
    answer_cloudformation_with(monkeypatch, module, absent)

    code, _, err = run_main(module, capsys)

    assert code == module.EXIT_DISAGREES
    assert "declared_stack_is_not_deployed" in err
    assert GPU_ROLES_STACK in err
    assert GPU_ROLES_TEMPLATE in err


def test_a_stack_belonging_to_another_team_is_not_reported(
    module: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    agreeing: dict[str, str],
) -> None:
    """Mutation: report every stack in the account that the table does not claim.

    This is a shared sandbox with sixteen other teams in it and about seventy stacks. A check
    that reported all of them would be forty-nine lines of noise every night, which is how a
    check stops being read. The prefix is the boundary of what this repository claims, and
    `test_every_stack_a_workflow_deploys_carries_the_prefix` is what keeps that true.
    """
    answer_cloudformation_with(
        monkeypatch,
        module,
        agreeing,
        also_listed=["mcat-dev-api", "gt-evidence-sandbox-storage", "CDKToolkit"],
    )

    code, out, err = run_main(module, capsys)

    assert code == module.EXIT_OK, err
    assert "mcat-dev-api" not in out + err


@pytest.mark.parametrize("status", ["DELETE_COMPLETE", "REVIEW_IN_PROGRESS"])
def test_a_status_holding_no_deployed_template_is_not_read_as_a_deployed_stack(
    module: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    agreeing: dict[str, str],
    status: str,
) -> None:
    """The two statuses where the name exists and nothing is applied under it.

    A deleted stack is returned by `list-stacks` forever, so reading it as deployed would
    report a stack that is not there. A stack in REVIEW_IN_PROGRESS has a change set and has
    never been applied, so there is no deployed template to fetch. Neither is skipped
    quietly: a table entry in either state falls out as declared and not deployed, which is
    the finding on the line below.
    """
    absent = dict(agreeing)
    del absent[GPU_ROLES_STACK]
    answer_cloudformation_with(
        monkeypatch,
        module,
        absent,
        also_listed=[GPU_ROLES_STACK],
        statuses={GPU_ROLES_STACK: status},
    )

    code, _, err = run_main(module, capsys)

    assert code == module.EXIT_DISAGREES
    assert "declared_stack_is_not_deployed" in err


def test_a_status_this_check_has_never_heard_of_is_compared_rather_than_dropped(
    module: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    agreeing: dict[str, str],
) -> None:
    """Mutation: name the statuses that count and ignore everything else.

    AWS adds stack statuses. An allow-list would drop a stack into a status added after this
    was written, silently, which is the failure this whole module is about pointed at itself.
    The exclusion list is two statuses long and everything outside it is looked at.
    """
    answer_cloudformation_with(
        monkeypatch,
        module,
        agreeing,
        statuses={GPU_ROLES_STACK: "SOME_STATUS_INVENTED_IN_2027"},
    )

    code, _, err = run_main(module, capsys)

    assert code == module.EXIT_OK, err


# ----------------------------------------------------------------------------------------
# The check could not be made
# ----------------------------------------------------------------------------------------


def test_a_refused_template_read_is_neither_agreement_nor_disagreement(
    module: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    agreeing: dict[str, str],
) -> None:
    """Mutation: fold a denial into the mismatch exit, or swallow it into a pass.

    Swallowing it retires the check on the morning the grant lapses without anybody deciding
    to. Folding it in sends whoever reads the failure looking for a deploy that never
    happened.
    """
    bodies = dict(agreeing)
    del bodies[GPU_ROLES_STACK]
    answer_cloudformation_with(
        monkeypatch, module, bodies, refusals={GPU_ROLES_STACK: (255, "", DENIED)}
    )

    code, _, err = run_main(module, capsys)

    assert code == module.EXIT_UNUSABLE
    assert "deployed_stack_unreadable" in err
    assert "deployed_stack_is_not_main" not in err
    assert "AccessDenied" in err
    # The remedy is a grant rather than a deploy, so the template carrying it is named where
    # the failure is read.
    assert "nightly-reader-role.yaml" in err


def test_a_denial_does_not_put_the_account_id_in_the_log(
    module: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    agreeing: dict[str, str],
) -> None:
    """Mutation: print the CLI's stderr, which is the obvious way to be helpful.

    A CloudFormation denial names the calling role's ARN and the stack ARN, and both carry
    the account id. This runs in a scheduled job whose log and step summary are public, and
    every committed capture in this repository masks that number.
    """
    bodies = dict(agreeing)
    del bodies[GPU_ROLES_STACK]
    answer_cloudformation_with(
        monkeypatch, module, bodies, refusals={GPU_ROLES_STACK: (255, "", DENIED)}
    )

    _, out, err = run_main(module, capsys)

    assert "123456789012" not in out + err
    assert "assumed-role" not in out + err


def test_a_listing_that_was_refused_does_not_stop_the_stacks_being_compared(
    module: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    agreeing: dict[str, str],
) -> None:
    """Mutation: give up when the listing fails, because completeness cannot be established.

    Only half of it cannot. The table still says what twenty-one stacks should be, and a
    denied `cloudformation:ListStacks` is no reason to stop asking about them -- the answer
    is still true and still worth a morning. What the run must not do is claim it looked at
    everything, so the refusal is reported and the exit is never zero.
    """
    answer_cloudformation_with(
        monkeypatch,
        module,
        agreeing,
        listing=(255, "", "An error occurred (AccessDenied) when calling the ListStacks operation"),
    )

    code, out, err = run_main(module, capsys)

    assert code == module.EXIT_UNUSABLE
    assert "deployed_stacks_not_listed" in err
    assert GPU_ROLES_STACK in out, "the stacks the table names were still compared"


def test_a_disagreement_outranks_a_call_that_could_not_be_made(
    module: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    agreeing: dict[str, str],
) -> None:
    """Both are printed, and the exit is the one naming a definite finding.

    Somebody with one stack to repair has to repair it whatever happened to the other, and
    the other is on the line above rather than hidden behind the exit code.
    """
    other = "sbsandbox-intern-edullm-phase3-batch-iam"
    bodies = dict(agreeing)
    bodies[other] = with_an_extra_resource(agreeing[other])
    del bodies[GPU_ROLES_STACK]
    answer_cloudformation_with(
        monkeypatch, module, bodies, refusals={GPU_ROLES_STACK: (255, "", DENIED)}
    )

    code, _, err = run_main(module, capsys)

    assert code == module.EXIT_DISAGREES
    assert "deployed_stack_is_not_main" in err
    assert "deployed_stack_unreadable" in err


def test_a_stack_the_listing_found_and_the_read_cannot_reach_is_not_read_as_absent(
    module: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    agreeing: dict[str, str],
) -> None:
    """A ValidationError from GetTemplate is CloudFormation saying the stack is not there.

    It arrives as an ordinary CLI failure and has to be told apart from a denial: this one is
    a statement about the account and the other is a statement about the grant.
    """
    bodies = dict(agreeing)
    del bodies[GPU_ROLES_STACK]
    answer_cloudformation_with(
        monkeypatch, module, bodies, refusals={GPU_ROLES_STACK: (254, "", NOT_FOUND)}
    )

    code, _, err = run_main(module, capsys)

    assert code == module.EXIT_DISAGREES
    assert "declared_stack_is_not_deployed" in err
    assert "deployed_stack_unreadable" not in err


def test_a_cli_that_is_not_installed_is_unusable(
    module: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def run(command: Sequence[str], **_: object) -> subprocess.CompletedProcess[str]:
        raise OSError("no aws on PATH")

    monkeypatch.setattr(module.subprocess, "run", run)

    code, _, err = run_main(module, capsys)

    assert code == module.EXIT_UNUSABLE
    assert "deployed_stacks_not_listed" in err


@pytest.mark.parametrize(
    ("payload", "why"),
    [
        ("", "the CLI printed nothing at all"),
        ("not json", "the answer did not parse"),
        ('{"TemplateBody": "{unclosed"}', "the body is not YAML"),
        ('{"TemplateBody": "a bare string"}', "the body parsed to something that is not a mapping"),
        ('{"TemplateBody": null}', "the field was absent"),
    ],
    ids=["empty", "not-json", "not-yaml", "not-a-mapping", "absent"],
)
def test_an_answer_that_is_not_a_template_is_unusable_rather_than_a_mismatch(
    module: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    agreeing: dict[str, str],
    payload: str,
    why: str,
) -> None:
    """Comparing an unreadable answer against the file would report drift this cannot claim.

    The tool would be right that the two are not equal and wrong about what that means, and
    the reader would be sent to a stack for a defect in a response.
    """
    bodies = dict(agreeing)
    del bodies[GPU_ROLES_STACK]
    answer_cloudformation_with(
        monkeypatch, module, bodies, refusals={GPU_ROLES_STACK: (0, payload, "")}
    )

    code, _, err = run_main(module, capsys)

    assert code == module.EXIT_UNUSABLE, why
    assert "deployed_stack_unreadable" in err
    assert "deployed_stack_is_not_main" not in err


def test_a_mapped_template_that_is_not_on_disk_is_unusable(
    module: Any, tmp_path: Path
) -> None:
    """A defect in the checkout rather than a statement about the account.

    Every template in the table is committed, so a missing one means this ran from somewhere
    other than a checkout of this repository -- and comparing the account against nothing
    would report every resource in the stack as unreviewed.
    """
    with pytest.raises(module.DeployedStackFinding) as raised:
        module.read_committed_template(tmp_path / "absent.yaml")

    assert raised.value.reason == "committed_template_unusable"
    assert raised.value.code == module.EXIT_UNUSABLE


@pytest.mark.parametrize(
    ("body", "why"),
    [
        ("{unclosed\n", "not YAML"),
        ("- a list\n", "a list where a template belongs"),
        ("", "an empty file, which parses to None"),
    ],
    ids=["not-yaml", "not-a-mapping", "empty"],
)
def test_a_mapped_template_that_does_not_parse_is_unusable(
    module: Any, tmp_path: Path, body: str, why: str
) -> None:
    template = tmp_path / "template.yaml"
    template.write_text(body, encoding="utf-8")

    with pytest.raises(module.DeployedStackFinding) as raised:
        module.read_committed_template(template)

    assert raised.value.reason == "committed_template_unusable", why
    assert raised.value.code == module.EXIT_UNUSABLE


def test_a_missing_template_stops_the_account_being_asked_about(
    module: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    agreeing: dict[str, str],
) -> None:
    """Mutation: fetch the deployed template first and read the file afterwards.

    There would be nothing to compare the answer against, so the call is a credential spent
    on a question the run cannot answer. Reading the file first also means a broken checkout
    fails identically with no network.
    """
    calls = answer_cloudformation_with(monkeypatch, module, agreeing)
    monkeypatch.setattr(
        module,
        "read_committed_template",
        _raising(module, "committed_template_unusable", module.EXIT_UNUSABLE),
    )

    code, _, err = run_main(module, capsys)

    assert code == module.EXIT_UNUSABLE
    assert [command for command in calls if command[2] == "get-template"] == []
    assert "committed_template_unusable" in err


def _raising(module: Any, reason: str, code: int) -> Any:
    def refuse(*_: object, **__: object) -> dict[str, Any]:
        raise module.DeployedStackFinding(reason, "as it happens, no", code=code)

    return refuse


# ----------------------------------------------------------------------------------------
# The table, and the two places it has to agree with
# ----------------------------------------------------------------------------------------


def test_the_table_is_declared_rather_than_derived_from_the_stack_name(module: Any) -> None:
    """WHY AN EXPLICIT TABLE, ASSERTED RATHER THAN LEFT IN A PULL REQUEST.

    Deriving the template from the stack name would fit most of these and not all of them:
    `…-phase1-ecr` is `ecr-repositories.yaml`, `…-phase2-admission-iam` is
    `iam/admission-role.yaml`, and `…-phase2-admission-service-roles` and
    `…-infra-deployer-iam` follow no rule shared with either. A convention that fits
    seventeen of twenty-one skips four, and it skips them by returning a path that is not
    there rather than by saying it could not tell -- which is the silent hole this check
    exists to close.

    The cost of declaring it is that the table can fall behind the account, and that cost is
    paid by `test_a_deployed_stack_the_table_does_not_claim_is_a_finding`: a stack the table
    does not claim fails the run.
    """
    for name, stack in module.STACKS.items():
        assert stack.name == name
        assert stack.template.is_file(), name
        assert stack.template.is_relative_to(INFRA_ROOT)


def workflow_deploys() -> set[tuple[str, str]]:
    """Every ``cloudformation deploy`` a workflow makes, as a stack name and a template path.

    Only the steps that deploy are lexed. ``aws_commands`` refuses a script whose every
    ``aws`` is not a top-level command, which is right for the step it is reading and wrong
    as a filter over the whole directory -- one workflow calls the CLI inside a loop to dump
    stack events after a failure, and that step is not a deploy.
    """
    return {
        (command[command.index("--stack-name") + 1], command[command.index("--template-file") + 1])
        for path in sorted(WORKFLOWS_ROOT.glob("*.yml"))
        for job in load_workflow(path)["jobs"].values()
        for item in job["steps"]
        if "cloudformation deploy" in str(item.get("run", ""))
        for command in aws_commands(str(item["run"]))
        if command[:3] == ["aws", "cloudformation", "deploy"]
    }


def test_every_stack_a_deploy_workflow_applies_is_in_the_table(module: Any) -> None:
    """Mutation: leave a CI stack out, because CI applying it makes it safe.

    It does not. CI applies whatever the commit it runs on says, so a stack deployed by a
    workflow is exactly as capable of holding something `main` no longer declares -- a
    rollback, a reverted merge, or a dispatch from a branch. Read from the workflows so a
    stack added to one of them and not to the table fails here rather than at 05:00.
    """
    applied = workflow_deploys()

    assert applied, "no workflow deploys anything, so this test is measuring nothing"
    for name, template in sorted(applied):
        assert name in module.STACKS, name
        assert module.STACKS[name].template == PROJECT_ROOT / template


def test_every_stack_a_workflow_deploys_carries_the_prefix(module: Any) -> None:
    """What makes it safe to ignore the rest of the account.

    The listing is filtered to `sbsandbox-intern-edullm-`, so a stack of ours under any other
    name would not be reported as unaccounted for -- it would not be seen at all. This holds
    the half that is mechanisable; the laptop stacks are held by `infra/README.md` naming
    each one, which the case below reads.
    """
    for name, _ in sorted(workflow_deploys()):
        assert name.startswith(module.STACK_NAME_PREFIX), name


def test_every_committed_template_is_claimed_by_a_stack(module: Any) -> None:
    """Mutation: add a template and forget the table, which is how the account gets ahead.

    A template under `infra/` with no stack claiming it is either something nobody has
    deployed yet or something deployed under a name this file does not know. The second is
    the case that matters and it is indistinguishable from the first here, so both fail: the
    table is where a template's stack name is written down, and that is the one thing about a
    hand-applied stack that this repository had no record of until 2026-07-31.
    """
    claimed = {stack.template for stack in module.STACKS.values()}
    templates = {
        path
        for path in [*INFRA_ROOT.glob("*.yaml"), *(INFRA_ROOT / "iam").glob("*.yaml")]
        if "AWSTemplateFormatVersion" in path.read_text(encoding="utf-8")
    }

    assert templates == claimed


def test_the_stack_names_are_the_ones_the_deploy_procedure_writes_down(module: Any) -> None:
    """The laptop half of the table, held against the file that is its only other record.

    Nothing in this repository deploys an IAM stack, so `infra/README.md` is where each one's
    name is decided. A name that differs between the two is a stack this check asks about and
    nobody applies, which reports as declared and not deployed every night.
    """
    readme = (INFRA_ROOT / "README.md").read_text(encoding="utf-8")

    for name, stack in module.STACKS.items():
        if (INFRA_ROOT / "iam") not in stack.template.parents:
            continue
        assert f"`{name}`" in readme, name


def test_the_tool_exposes_a_parser(module: Any) -> None:
    parser = module.build_parser()

    assert parser.parse_args([]).profile is None, (
        "the nightly runs on an assumed role and passes no profile, so a default here would "
        "send it looking for a laptop's SSO session"
    )
    assert parser.parse_args([]).region == "us-east-1"


def test_the_tool_never_writes_an_account_id_into_its_own_source() -> None:
    source = TOOL.read_text(encoding="utf-8")

    assert "carries the account id" in source
    assert not ACCOUNT_LITERAL.search(source)
