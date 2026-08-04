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
IMAGE_RESOLVER_TEMPLATE_PATH = PROJECT_ROOT / "infra" / "iam" / "image-resolver-role.yaml"
EXECUTION_TARGETS_PATH = PROJECT_ROOT / "config" / "execution-targets.yaml"
README_PATH = PROJECT_ROOT / "infra" / "README.md"

ROLE_NAME = "sbsandbox-intern-edullm-run-preview"
PREVIEW_ENVIRONMENT = "run-approval-preview"
SUBJECT_PREFIX = "repo:edu-llm@306859726/platform@1311508598"
PREVIEW_SUBJECT = f"{SUBJECT_PREFIX}:environment:{PREVIEW_ENVIRONMENT}"
BRANCH_SUBJECT = f"{SUBJECT_PREFIX}:ref:refs/heads/*"
MAIN_SUBJECT = f"{SUBJECT_PREFIX}:ref:refs/heads/main"
CPU_QUEUE = "sbsandbox-intern-edullm-cpu"
SUB = "token.actions.githubusercontent.com:sub"
WORKFLOW_REF = "token.actions.githubusercontent.com:job_workflow_ref"


def _role() -> dict[str, Any]:
    roles = list(iam_roles(load_template(TEMPLATE_PATH)))
    assert len(roles) == 1, "this template declares one role and the tests below assume it"
    return roles[0]


def _trust_statements() -> list[dict[str, Any]]:
    """The two, and exactly the two.

    TWO RATHER THAN ONE, AND THE COUNT IS ASSERTED BECAUSE A THIRD IS ANOTHER WAY IN. The
    preview path mints two subject shapes and no single statement can accept both: the
    submit job declares ``environment: run-approval-preview`` and is issued an environment
    subject, and the resolve job must declare no environment at all -- on ``main`` it
    assumes the image resolver, whose trust is pinned to ``:ref:refs/heads/main``, so an
    environment key there would break the production path this role exists to preview.
    ``StringEquals`` on the environment literal and ``StringLike`` on the ref pattern would
    be ANDed against the same ``sub`` and match nothing, so they are separated.
    """
    statements = _role()["AssumeRolePolicyDocument"]["Statement"]
    assert len(statements) == 2, (
        "two trust statements, the environment subject and the branch ref subject. A third "
        "is another way in and no test below would read it."
    )
    return [dict(statement) for statement in statements]


def _environment_condition() -> dict[str, Any]:
    """The statement the submit job's session comes through."""
    matching = [
        dict(statement["Condition"])
        for statement in _trust_statements()
        if statement["Condition"].get("StringEquals", {}).get(SUB) == PREVIEW_SUBJECT
    ]
    assert len(matching) == 1, "exactly one statement accepts the environment subject"
    return matching[0]


def _ref_condition() -> dict[str, Any]:
    """The statement the resolve job's session comes through."""
    matching = [
        dict(statement["Condition"])
        for statement in _trust_statements()
        if SUB in statement["Condition"].get("StringLike", {})
    ]
    assert len(matching) == 1, "exactly one statement accepts a ref subject"
    return matching[0]


def _statements() -> list[dict[str, Any]]:
    policies = _role()["Policies"]
    assert len(policies) == 1
    return list(policies[0]["PolicyDocument"]["Statement"])


REQUIREMENT_HEADING = "### A requirement for the mismatch filter, which is not built yet"


def _recorded_requirement() -> str:
    """That section of `infra/README.md`, and only that section.

    Sliced rather than read whole because the file legitimately discusses role-name
    wildcards elsewhere, and a substring check against the whole thing would report the
    requirement as widened when it had not been -- or, worse, pass because some unrelated
    paragraph happened to contain the role name.

    IN `infra/README.md` AND NOT IN `config/organization.yaml`, WHERE THE JOIN TABLE GOES.
    The whole `config/` directory is copied into the admission Lambda zip and both released
    zips are byte-identical by construction, so any edit under `config/` -- including a
    comment -- moves both digests and reddens
    ``test_the_released_zip_is_the_one_this_tree_builds`` until somebody rebuilds and
    releases from a laptop. Recording a note there would have required an AWS deploy.
    """
    text = README_PATH.read_text(encoding="utf-8")
    heading = text.find(REQUIREMENT_HEADING)
    assert heading != -1, (
        "infra/README.md no longer records the mismatch-filter requirement. It is the only "
        "record that the preview role has to be excluded by name, and the filter it "
        "describes is not built yet, so nothing else would catch its loss."
    )
    return text[heading:]


def _statement_for(service: str) -> dict[str, Any]:
    matching = [
        statement
        for statement in _statements()
        if all(action.startswith(f"{service}:") for action in statement_actions(statement))
    ]
    assert len(matching) == 1, f"one statement grants {service}, and the tests read that one"
    return matching[0]


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
    condition = _environment_condition()

    assert condition["StringEquals"][SUB] == PREVIEW_SUBJECT
    assert SUB not in condition.get("StringLike", {}), (
        "the environment subject moved into StringLike, which accepts environments nobody "
        "created"
    )
    # And no statement anywhere in this template names an environment loosely. The ref
    # statement is allowed a wild subject and is pinned by the test below; what neither is
    # allowed is a pattern that would match `:environment:` followed by anything.
    for statement in _trust_statements():
        for pattern in statement["Condition"].get("StringLike", {}).values():
            assert ":environment:" not in pattern, pattern


def test_the_branch_statement_accepts_any_branch_except_main_by_name() -> None:
    """Mutation: drop the StringNotEquals, or widen `refs/heads/*` to `refs/*`.

    The resolve job declares no environment, so on a branch it is issued a ref subject and
    this statement is what accepts it. The subtraction of `main` is the load-bearing half.
    Without it a dispatch from `main` could pick this role up out of the job that is
    supposed to be holding the image resolver, and `main` is the one ref where that must be
    impossible, because on `main` the production path is the path -- the failure would not
    be a red job here, it would be a production submission silently scoped to one CPU queue.

    `refs/heads/*` rather than `refs/*` so a tag cannot present the claim either. A `*` in
    an IAM condition matches `/`, so the branch pattern already covers `feature/x`.
    """
    condition = _ref_condition()

    assert condition["StringLike"][SUB] == BRANCH_SUBJECT
    assert condition["StringNotEquals"][SUB] == MAIN_SUBJECT
    assert BRANCH_SUBJECT.startswith(f"{SUBJECT_PREFIX}:ref:refs/heads/")
    assert BRANCH_SUBJECT.count("*") == 1 and BRANCH_SUBJECT.endswith("*")
    # The subtraction has to name the same prefix it subtracts from, or it subtracts
    # nothing: a StringNotEquals against a string the StringLike could never match is a
    # condition that is always true.
    assert MAIN_SUBJECT == BRANCH_SUBJECT.removesuffix("*") + "main"


def test_the_workflow_file_is_pinned_and_only_the_ref_is_wild() -> None:
    """Mutation: drop `job_workflow_ref`, on the ground that the subject already gates this.

    It does not. The subject names an environment, and an environment is reachable from any
    workflow that declares it -- so without this condition a new workflow file could declare
    `run-approval-preview` and assume this role. The file is pinned with a `StringLike`
    whose only wild segment is after the `@`, which is the ref, which is the one thing this
    role exists to leave free.

    Asserted of both statements rather than of one. They are two ways into the same role,
    so a pin missing from either is a pin missing.
    """
    for condition in (_environment_condition(), _ref_condition()):
        workflow_ref = condition["StringLike"][WORKFLOW_REF]

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
    preview_subjects = {
        value
        for statement in _trust_statements()
        for value in walk_strings(statement["Condition"])
    }

    assert PREVIEW_ENVIRONMENT not in set(ApprovalEnvironment)
    assert PREVIEW_SUBJECT in preview_subjects
    assert PREVIEW_SUBJECT not in admission_subjects
    for gate in ApprovalEnvironment:
        accepted = f"{SUBJECT_PREFIX}:environment:{gate.value}"
        assert accepted in admission_subjects, gate
        assert accepted not in preview_subjects, gate
    # The branch statement is a pattern, so disjointness has to be checked against what it
    # matches rather than against what it says. An environment subject has `:environment:`
    # where this has `:ref:refs/heads/`, so no gate can satisfy it -- asserted rather than
    # asserted-by-inspection, because the two enumerations drifting is the whole worry.
    branch_prefix = BRANCH_SUBJECT.removesuffix("*")
    for gate in ApprovalEnvironment:
        assert not f"{SUBJECT_PREFIX}:environment:{gate.value}".startswith(branch_prefix), gate


def test_the_role_may_submit_a_job_and_read_an_image_and_do_nothing_else_at_all() -> None:
    """Mutation: add a third action, or a third statement.

    Asserted exactly rather than approximately, for the reason
    `infra/iam/image-resolver-role.yaml` gives about its own two reads: a role whose trust
    condition is looser than every other role here is affordable only while its grant is
    this small, so the grant is the thing that has to fail on a change.

    THREE ACTIONS RATHER THAN ONE, AND THE TWO THAT ARRIVED ARE READS. `submit-run.yml`
    dies in its resolve job on a branch without them -- the job asks ECR which image the
    declared commit published -- so a role that could submit but could not resolve was a
    preview of nothing.
    """
    statements = _statements()

    assert len(statements) == 2
    assert [statement["Effect"] for statement in statements] == ["Allow", "Allow"]
    assert statement_actions(_statement_for("batch")) == ["batch:SubmitJob"]
    assert statement_actions(_statement_for("ecr")) == [
        "ecr:DescribeImages",
        "ecr:DescribeImageScanFindings",
    ]


def test_the_two_reads_are_the_image_resolvers_two_reads_and_not_a_wider_pair() -> None:
    """Mutation: grant `ecr:*`, or point the resolve reads at every repository.

    Read off the production template rather than written twice, because the property is a
    relationship between the two: a branch dispatch exists to exercise what `main` will do,
    so a grant that differs here means a resolve that succeeds on a branch and fails on
    `main`, or the reverse -- and either way the preview stops predicting the thing it was
    built to predict.

    That makes this test cut both ways on purpose. It fails if this role widens, and it
    also fails if the image resolver narrows without this role following, which is the
    direction nobody would think to check.
    """
    resolver_role = next(iter(iam_roles(load_template(IMAGE_RESOLVER_TEMPLATE_PATH))))
    resolver_statements = resolver_role["Policies"][0]["PolicyDocument"]["Statement"]
    resolver_ecr = [
        statement
        for statement in resolver_statements
        if all(action.startswith("ecr:") for action in statement_actions(statement))
    ]
    assert len(resolver_ecr) == 1, "the image resolver's ECR grant is one statement"
    preview_ecr = _statement_for("ecr")

    assert statement_actions(preview_ecr) == statement_actions(resolver_ecr[0])
    assert statement_resources(preview_ecr) == statement_resources(resolver_ecr[0])
    # Neither of them may pull. Describing an image is reading a manifest; the two absent
    # actions are what turn that into bytes on a disk.
    for action in statement_actions(preview_ecr):
        assert action.startswith("ecr:Describe"), action


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
    reachable = statement_resources(_statement_for("batch"))

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


def test_the_mismatch_exclusion_is_recorded_against_this_role_by_name_and_not_a_pattern() -> None:
    """The filter does not exist yet, so what is pinned here is the requirement for it.

    A mismatch is a launch by a roster principal with no lineage record, and every job this
    role places is exactly that by construction -- so the list would fill with entries that
    are all correct behavior, which is how a monitoring surface becomes one nobody reads.
    Nothing computes that list today: it is described in the system overview, the twenty-row
    join table it needs is not in `config/organization.yaml`, and no tool reads CloudTrail
    against lineage. So the requirement is recorded in `infra/README.md` instead, for the
    reason ``_recorded_requirement`` gives.

    WHAT THIS TEST IS FOR, GIVEN THERE IS NO FILTER TO TEST. It fails if the recorded
    requirement stops naming this role -- which is what a rename of the role would do
    silently, leaving a note that reads fine and excludes nothing. And it fails if the
    requirement is ever restated as a pattern, because a pattern would swallow the next role
    that happened to match it. When the filter is built, the test that pins its exclusion
    replaces this one rather than joining it.
    """
    note = _recorded_requirement()
    role_name = _role()["RoleName"]

    assert role_name == ROLE_NAME
    assert role_name in note, (
        "infra/README.md no longer records the mismatch exclusion against this role by "
        "name, so a filter built from it would not exclude anything"
    )
    # Named, never matched. Only the role name with a wildcard glued to it is checked, and
    # deliberately not a list of bad patterns: the requirement text names several as
    # prohibitions -- "not `*-run-preview`" -- so a scan for those cannot tell a rule from
    # a violation of it. This one is the realistic widening and appears nowhere in the prose.
    assert f"{role_name}*" not in note
    # The three constraints, each stated so that dropping one is visible here. The second is
    # the one most easily lost, because a silent exclusion still looks like a working filter.
    assert "never a pattern" in note.lower()
    assert "preview launches, excluded" in note
    assert "test that fails if the exclusion widens" in note.lower()
    # And the property the exclusion exists to protect, which decides the shape of the fix:
    # excluding the role rather than giving preview jobs a lineage record.
    assert "no lineage record" in note


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

    assert services == {"batch", "ecr"}
    for forbidden in ("states:", "s3:", "iam:", "secretsmanager:", "sts:", "logs:"):
        assert not [action for action in granted if action.startswith(forbidden)], forbidden
    # TerminateJob and CancelJob belong to the run canceller and its own principal. The two
    # ECR actions are reads and are enumerated here too, so that `ecr:` widening to a pull
    # or to a write fails this test as well as the pair of tests above it.
    assert granted == {
        "batch:SubmitJob",
        "ecr:DescribeImages",
        "ecr:DescribeImageScanFindings",
    }
    # The two ids in the trust policy are nine and ten digits; an account id is twelve.
    assert not [
        value for value in walk_strings(load_template(TEMPLATE_PATH)) if ACCOUNT_LITERAL.search(value)
    ]
