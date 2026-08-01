"""The roles and the state this phase adds so a submission runs the image it declared.

Two changes live here and they are halves of one thing. The first is the role that lets a
submission read which image a commit published; the second is the grant and the state that
let an accepted run be executed on that image rather than on whichever one CloudFormation
happened to pin.

## The role that lets a submission read which image a commit published

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

Everything about that role reads the committed template through ``load_template_roles``,
the same projection the drift comparison uses, so a template these tests pass cannot be one
the comparison refuses to read.

## The job definition an accepted run registers for itself

``batch_register_job_definition_request`` builds a job definition carrying the digest the
manifest declared, because AWS Batch has no submit-time image override and
``RegisterJobDefinition`` is the only mechanism that can change a container's image. Until
this change nothing called it: the state machine submitted against the definition
``infra/batch-compute.yaml`` deploys, whose image is pinned in CloudFormation, so the digest
a submitter declared was validated, gated admission through the ECR scan, was written
immutably into lineage -- and selected nothing.

Closing that costs a grant and a state, and both are asserted below rather than only
described. The grant is the sharper of the two: ``sbsandbox-intern-edullm-admission-states``
is the only principal in this account that may start compute, and this change hands it the
ability to mint the thing it starts. What keeps that bounded is the scope of the
registration and the exact list of roles it may pass, so both are asserted as sets rather
than as memberships -- an approximate assertion here reports a bound it never checked.

These tests read structure rather than the text of an expression, with one deliberate
exception: the register state's JSONata is compared as a whole string, because an
expression that reshaped the request would satisfy every structural assertion and defeat
the point of passing the request through.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml
from infrastructure_support import (
    IAM_ROOT,
    INFRA_ROOT,
    iam_roles,
    load_template,
    resource_of_type,
    statement_actions,
)

from edullm_platform.config import load_yaml
from edullm_platform.contracts.execution import ExecutionTargetCatalog
from edullm_platform.contracts.manifest import RunManifest
from edullm_platform.contracts.workload import WorkloadCatalog
from edullm_platform.execution import (
    batch_register_job_definition_request,
    resolve_execution_target,
)
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

STATE_MACHINE_PATH = INFRA_ROOT / "admission-state-machine.yaml"
SERVICE_ROLES_PATH = IAM_ROOT / "admission-service-roles.yaml"
EXECUTION_TARGETS_PATH = PROJECT_ROOT / "config" / "execution-targets.yaml"

STATES_ROLE_NAME = "sbsandbox-intern-edullm-admission-states"

#: The state that registers the definition, named here so a rename is one edit rather than
#: a dozen literals that could each be updated separately.
REGISTER_STATE = "RegisterJobDefinition"

#: What ``batch:RegisterJobDefinition`` may name: the shape an accepted run mints for itself
#: and nothing else. ``job_definition_name`` puts the run id under this project's prefix and
#: a run id is ``run_<uuid7>``, so this matches every definition a registration can produce.
#:
#: Narrower than the project prefix on purpose, and the difference is not cosmetic. Under
#: ``sbsandbox-intern-edullm-*`` this role could register a revision of
#: ``sbsandbox-intern-edullm-cpu-run`` or ``-gpu-run``, which are the CloudFormation-owned
#: definitions every earlier run was submitted against -- replacing the image on one of them
#: from inside an execution. ``run_`` cannot match either, because neither begins with it, so
#: the deployed definitions can only be changed by the template that declares them.
REGISTER_SCOPE = (
    "arn:${AWS::Partition}:batch:${AWS::Region}:${AWS::AccountId}:"
    "job-definition/sbsandbox-intern-edullm-run_*"
)

#: Twelve digits that are not this account's, so the ARNs the register request is built
#: from carry no real account id. ``tests/test_evidence.py`` allows this one because it is
#: AWS's own documented example.
EXAMPLE_ACCOUNT = "123456789012"

RUN_ID = "run_019fa439-203e-70c7-bf8a-9ce33bc71f20"


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


def states_role() -> dict[str, Any]:
    matching = [
        role
        for role in iam_roles(load_template(SERVICE_ROLES_PATH))
        if role["RoleName"] == STATES_ROLE_NAME
    ]
    assert len(matching) == 1, f"expected exactly one role named {STATES_ROLE_NAME}"
    return matching[0]


def states_role_statements() -> list[dict[str, Any]]:
    return [
        statement
        for policy in states_role()["Policies"]
        for statement in policy["PolicyDocument"]["Statement"]
    ]


def states_role_actions() -> list[str]:
    return [
        action
        for statement in states_role_statements()
        for action in statement_actions(statement)
    ]


def the_one_statement_granting(action: str) -> dict[str, Any]:
    """The single statement that grants ``action``, and nothing else.

    One statement rather than a union, for the reason the existing grants are written with:
    a second Allow naming a subset of the first grants nothing and reads as though it did,
    so the next reader has to work out which of the two is load-bearing. Asserting the
    action is alone in its statement is what makes the resource list below a scope on that
    action rather than on whatever else shares the statement.
    """
    statements = [
        statement
        for statement in states_role_statements()
        if action in statement_actions(statement)
    ]
    assert len(statements) == 1, f"{action} belongs in exactly one statement"
    assert statement_actions(statements[0]) == [action]
    return statements[0]


def resource_arns(resource: object) -> list[str]:
    """The ARN strings a statement's Resource names, with the Fn::Sub wrappers unwrapped."""
    if isinstance(resource, str):
        return [resource]
    if isinstance(resource, list):
        return [arn for item in resource for arn in resource_arns(item)]
    assert isinstance(resource, dict), f"unexpected Resource shape: {resource}"
    assert list(resource) == ["Fn::Sub"], f"unexpected Resource shape: {resource}"
    return [resource["Fn::Sub"]]


def execution_target_bindings() -> dict[str, dict[str, Any]]:
    """Every backed compute profile, keyed by profile, as deployed configuration says it."""
    loaded = yaml.safe_load(EXECUTION_TARGETS_PATH.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    targets = loaded["targets"]
    assert isinstance(targets, list) and targets
    return {binding["compute_profile"]: binding for binding in targets}


def state_machine_definition() -> dict[str, Any]:
    template = load_template(STATE_MACHINE_PATH)
    _logical_id, machine = resource_of_type(template, "AWS::StepFunctions::StateMachine")
    definition = machine["Properties"]["DefinitionString"]["Fn::Sub"]
    assert isinstance(definition, str)
    parsed = json.loads(definition)
    assert isinstance(parsed, dict)
    return parsed


def manifest(compute_profile: str) -> RunManifest:
    payload: dict[str, Any] = {
        "schema_version": 1,
        "repository": "OLMo-core",
        "commit_sha": "4204375e6db85abc244ec7f626de8d3cc3511402",
        "image_digest": (
            "sha256:4ebdba1ba3b57096efb4f4647ed41ed5ded4ac9e77e8c9038b7ff24db0bc6db8"
        ),
        "dataset_release": "dolma-2026-07",
        "command": ["python", "-m", "olmo_core.train", "--config", "smoke"],
        "team": "memory-split",
        "wandb_project": "olmo-core-memory-split",
        "workload_profile": "olmo-core-check-cpu",
        "compute_profile": compute_profile,
        "maximum_runtime_hours": "1",
        "maximum_attempts": 1,
        "checkpoint": None,
        "fanout": None,
    }
    return RunManifest.model_validate(payload)


def every_register_request_field() -> frozenset[str]:
    """Every key ``batch_register_job_definition_request`` can put in a request.

    Derived by building one for every backed profile rather than listed, because a
    hand-written list is a second copy of a key set that already exists in the Python -- and
    the field it forgets is the field the ASL is then never searched for, which is the
    defect the search below exists to catch, moved one level up into the test's own
    constant.
    """
    catalog = load_yaml(PROJECT_ROOT / "config" / "workload-catalog.yaml", WorkloadCatalog)
    targets = load_yaml(EXECUTION_TARGETS_PATH, ExecutionTargetCatalog)
    return frozenset(
        key
        for profile in execution_target_bindings()
        for key in batch_register_job_definition_request(
            manifest=manifest(profile),
            target=resolve_execution_target(
                compute_profile=profile,
                catalog=catalog,
                targets=targets,
                account_id=EXAMPLE_ACCOUNT,
            ),
            run_id=RUN_ID,
        )
    )


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


# --------------------------------------------------------------------------------------
# What the admission state machine may register, and what it may pass to do it
# --------------------------------------------------------------------------------------


def test_the_states_role_may_register_a_job_definition_only_under_our_own_names() -> None:
    """Mutation: scope the grant to ``*``, or to ``job-definition/*``.

    This is the first grant that lets the admission state machine create a Batch resource
    rather than only use one, so what bounds it is the name it may create under. A wildcard
    reaching every job definition in the account would let one execution replace another
    intern's definition with a revision of our choosing -- and a job definition names the
    roles its container runs as, so that is not a Batch resource being edited, it is an
    identity being handed to an image somebody else did not choose.

    ``sbsandbox-intern-`` alone is every intern's prefix here, which is why the scope starts
    after the ``edullm`` segment, exactly as the deployer's stack and ECR scopes do.
    """
    statement = the_one_statement_granting("batch:RegisterJobDefinition")

    assert statement["Effect"] == "Allow"
    assert resource_arns(statement["Resource"]) == [REGISTER_SCOPE]


def test_the_states_role_passes_exactly_the_roles_a_registration_fixes_and_nothing_else() -> None:
    """Reads BOTH the role and config/execution-targets.yaml. Mutation: a prefix scope.

    ``sbsandbox-intern-edullm-*`` would read as the same grant and is not: passing a role is
    how a principal lends its own limits away, so a prefix lets this state machine hand a
    container any role that ever takes a matching name, including one created in a later
    phase with permissions nobody weighed against a principal that can start compute. The
    ARNs are written out in full for that reason, and a fifth is then a visible edit.

    The expected set is read out of the deployed target configuration rather than listed
    here, because the roles a registration passes are exactly the two each backed profile
    names -- so promoting a third profile without extending this grant fails here rather
    than at the first submission on it.
    """
    granted = resource_arns(the_one_statement_granting("iam:PassRole")["Resource"])
    expected = {
        f"arn:${{AWS::Partition}}:iam::${{AWS::AccountId}}:role/{binding[field]}"
        for binding in execution_target_bindings().values()
        for field in ("execution_role", "workload_role")
    }

    assert set(granted) == expected
    # No duplicates, so the list length is the number of roles rather than the number of
    # lines somebody added.
    assert len(granted) == len(expected)
    for arn in granted:
        assert "*" not in arn.rsplit("/", 1)[1], f"{arn} is a prefix rather than a role"
    # The instance role is deliberately absent: it is passed by CreateComputeEnvironment,
    # which this principal does not hold and must not, and it is the one role in the Batch
    # set whose credentials the ECS agent on a shared host can reach.
    assert not [arn for arn in granted if arn.endswith("-instance")]


def test_the_states_role_gains_no_way_to_remove_a_definition_it_registered() -> None:
    """Mutation: add ``batch:DeregisterJobDefinition`` alongside the register grant.

    Revisions accumulate and Batch enforces no quota on them, so nothing is failing for
    want of a cleanup and the verb would be granted against a call nothing makes. Cleaning
    up is a later phase's work, and it is worth doing there rather than here because the
    principal that would deregister is the wrong one: this role records a run as accepted
    and then launches it, and one that could also retire the definition that run is bound to
    could make a completed run unreadable -- the binding record would name a definition
    Batch no longer describes.

    Asserted as an exact ordered list rather than as three memberships, so a fourth verb
    cannot arrive unnoticed on the strength of these three having been allowed.
    """
    actions = states_role_actions()

    assert [action for action in actions if action.startswith("batch:")] == [
        "batch:SubmitJob",
        "batch:TagResource",
        "batch:RegisterJobDefinition",
    ]
    # Spelled out as well as excluded by the list above, because this is the one a reader
    # is most likely to add back while tidying.
    assert "batch:DeregisterJobDefinition" not in actions
    # A wildcard grants every Batch verb without naming one, which is how the exact list
    # above would otherwise be satisfied by a role that holds everything.
    assert not [action for action in actions if "*" in action]


# --------------------------------------------------------------------------------------
# The state that registers the definition an accepted run is submitted against
# --------------------------------------------------------------------------------------


def test_an_accepted_run_registers_its_own_definition_before_anything_is_submitted() -> None:
    """Mutation: leave ``ResolveExecutionTarget`` pointing straight at SubmitToBatch.

    The register state is reachable only from the accepted branch, which is the same rule
    the submit request already follows and the reason the handler omits the whole
    ``execution`` key on a refusal: a rejected submission that carried a registration
    request would be one ``InputPath`` away from having a definition minted for it.

    ``Arguments`` rather than ``Parameters``, measured on 2026-07-27 against
    ``aws stepfunctions validate-state-machine-definition``: ``InputPath`` with no
    ``Parameters`` is refused for an ``aws-sdk`` resource, ``"Parameters.$"`` is not a
    supported field, and JSONata ``Arguments`` validates. SubmitToBatch carries the whole
    measurement and this state copies it.
    """
    states = state_machine_definition()["States"]
    register = states[REGISTER_STATE]

    assert states["AdmissionAccepted"]["Choices"][0]["Next"] == "ResolveExecutionTarget"
    assert states["AdmissionAccepted"]["Default"] == "Rejected"
    assert states["ResolveExecutionTarget"]["Next"] == REGISTER_STATE
    assert register["Resource"].endswith(":aws-sdk:batch:registerJobDefinition")
    assert register["Next"] == "SubmitToBatch"
    assert register["QueryLanguage"] == "JSONata"
    assert "Parameters" not in register
    # One reference and nothing else, asserted whole rather than as a substring: an
    # expression that wrapped the request would satisfy "contains the path" and could
    # reshape what Batch is asked to register.
    assert register["Arguments"] == "{% $states.input.execution.register_request %}"


def test_the_register_state_names_no_field_of_the_definition_it_registers() -> None:
    """Reads the ASL and the Python. Mutation: rebuild the request in a ``Parameters`` block.

    Omission is silent on every axis of a job definition: a missing ``Secrets`` block is a
    training run that cannot reach W&B, a missing ``LinuxParameters`` is a DataLoader bus
    error partway into training, and a missing GPU resource requirement is a container that
    trains on the CPU at GPU prices and reports nothing wrong. Passing the request through
    whole means there are no keys here to omit, so the assertion is that this state names
    none of them.

    ``Type`` is excluded and is the exception that proves the rule: it is both a job
    definition field and the name every Amazon States Language state carries, so a search
    for it would fail on ``"Type": "Task"``. What covers it is the exact ``Arguments``
    assertion above, which leaves nowhere for a reconstructed request to be written.
    """
    register = state_machine_definition()["States"][REGISTER_STATE]
    text = json.dumps(register)

    searchable = every_register_request_field() - {"Type"}
    assert "JobDefinitionName" in searchable, "the derivation stopped producing field names"
    for field in sorted(searchable):
        assert field not in text, f"{REGISTER_STATE} names {field}, so it can drop {field}"


def test_a_registration_batch_refuses_is_recorded_rather_than_killing_the_execution() -> None:
    """Mutation: delete the ``Catch``.

    By the time this state runs, the intent and the decision records are written and the
    decision says accepted. An execution that dies here fails with whatever Batch said and
    writes nothing, so the run's lineage ends at a record promising a submission that never
    happened -- and the submitter is sent to read a story whose last chapter exists only in
    an execution history. That failure has been paid for on this project already, by the
    ``command`` times ``team`` override budget, which is why this routes to the refusal path
    the submit failure already uses rather than to one invented here.

    The error name is broad on purpose. What Step Functions calls a Batch refusal of a
    registration has not been measured against this account, and the discipline that kept
    ``States.ALL`` on the lineage writes until ``tools/probe_conditional_write.py`` measured
    ``S3.S3Exception`` applies here too: a guessed name that never fires is a Catch that
    reads as protection and is not.
    """
    states = state_machine_definition()["States"]
    catch = states[REGISTER_STATE]["Catch"]

    assert len(catch) == 1
    assert catch[0]["ErrorEquals"] == ["States.ALL"]
    assert catch[0]["Next"] == "RecordSubmissionFailure"
    # A JSONata state replaces its whole output rather than merging into it, so the failure
    # is put beside the input the way ResultPath would have. RecordSubmissionFailure reads
    # $.admission.run_id out of that, so a Catch that returned only the error would write a
    # record under a key built from nothing.
    assert catch[0]["Output"] == (
        "{% $merge([$states.input, {'registration_failure': $states.errorOutput}]) %}"
    )
    assert states["RecordSubmissionFailure"]["Next"] == "SubmissionFailed"
    assert states["SubmissionFailed"]["Type"] == "Fail"
    assert states["SubmissionFailed"]["Error"] == "BatchSubmissionFailed"


def test_the_definition_a_job_is_submitted_against_is_the_revision_just_registered() -> None:
    """Mutation: drop the ``JobDefinition`` merge from the register state's ``Output``.

    THIS IS THE WHOLE POINT OF THE CHANGE AND IT IS ONE EXPRESSION WIDE. Without the merge
    the submit request reaches Batch carrying whatever the handler put there, the run
    executes on a definition this registration did not produce, and nothing anywhere fails:
    the job runs, the binding is written, and the digest in the decision record goes on
    describing an image that was never pulled. That is the state this change exists to end,
    so it is asserted against the expression rather than against its shape.

    The merge is here rather than in SubmitToBatch because SubmitToBatch names no field of
    the request -- see the Phase 3 seam test that holds it to that -- and this is the only
    state that knows the answer, since the revision ARN does not exist until Batch has
    replied.
    """
    states = state_machine_definition()["States"]

    assert states[REGISTER_STATE]["Output"] == (
        "{% $merge([$states.input, {'execution': $merge([$states.input.execution, "
        "{'submit_request': $merge([$states.input.execution.submit_request, "
        "{'JobDefinition': $states.result.JobDefinitionArn}])}])}]) %}"
    )
    # The other half: the submit state still passes the request through whole, so the ARN
    # merged above is the only JobDefinition that can reach Batch.
    assert states["SubmitToBatch"]["Arguments"] == "{% $states.input.execution.submit_request %}"
    assert "Parameters" not in states["SubmitToBatch"]
