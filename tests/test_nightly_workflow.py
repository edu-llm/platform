"""The scheduled job that goes and looks for the failure nothing reports.

**The most expensive mistake on this platform is silent, and until this job existed nothing
ran the tool that finds it.** A training run that took OLMo-core's ``/tmp`` default writes
its checkpoints to local disk on a machine that stops existing, exits zero, and is recorded
as a success. ``tools/find_runs_that_saved_nothing.py`` asks the question the lineage record
cannot; ``tests/test_find_runs_that_saved_nothing.py`` holds the tool. This module holds the
two things that decide whether anybody ever sees its answer.

The first is that the answer is allowed to be a failure. ``nightly.yml`` has no
``continue-on-error`` anywhere on purpose -- there is no alerting infrastructure here, so a
red scheduled run is the whole signal -- and a report piped into the step summary is one
missing ``pipefail`` away from a job that prints six silently broken runs and goes green.
That one is proved by running the step rather than by reading it.

The second is the credential. ``nightly.yml`` held no AWS access at all, and every GitHub
role in ``infra/iam/`` pins ``job_workflow_ref`` to some other workflow file with a
``StringEquals``, so this job needed a role of its own. A role added for a scheduled job
nobody is watching is worth bounding tightly, which is why the action set is asserted
exactly rather than checked for the absence of anything alarming.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from infrastructure_support import (
    ACCOUNT_LITERAL,
    BOUNDARY,
    IAM_ROOT,
    OIDC_PROVIDER,
    iam_roles,
    load_template,
    statement_actions,
)
from workflow_support import (
    WORKFLOWS_ROOT,
    command_tokens,
    load_workflow,
    run_step_script,
    write_stub,
)

from edullm_platform.contracts.results import OUTPUTS_BUCKET

WORKFLOW_PATH = WORKFLOWS_ROOT / "nightly.yml"
ROLE_PATH = IAM_ROOT / "nightly-reader-role.yaml"

#: The job, the tool it runs, and the variable that names the identity it runs as. Spelled
#: out because all three are load-bearing strings that nothing else in this repository
#: would notice the renaming of: the job id is what a red cross says at 05:00, the tool
#: path is the only thing that runs the report, and the variable is set by hand in the
#: repository settings and so cannot be found by a grep of the tree.
JOB_ID = "runs-that-saved-nothing"
REPORT_TOOL = "tools/find_runs_that_saved_nothing.py"
ROLE_VARIABLE = "AWS_NIGHTLY_READER_ROLE_ARN"
ROLE_NAME = "sbsandbox-intern-edullm-nightly-reader"
LINEAGE_BUCKET = "sbsandbox-intern-edullm-lineage"
WORKFLOW_REF = "edu-llm/platform/.github/workflows/nightly.yml@refs/heads/main"

#: A plausible ARN for the guard to accept, on the documentation account id every other
#: test here uses. A run of twelve digits that is not that one reads as a real account to
#: `tests/test_evidence.py`, which scans the tracked tree and does not care that this file
#: is a test.
SOME_ROLE_ARN = "arn:aws:iam::123456789012:role/sbsandbox-intern-edullm-nightly-reader"

#: Every verb that changes something, matched against the action set as a substring. The
#: exact-set assertion below is the primary check; this is the one that keeps reading true
#: when somebody argues a new action into the set.
MUTATING_ACTION_FRAGMENTS = (
    "Abort",
    "Create",
    "Delete",
    "Put",
    "Replicate",
    "Restore",
    "Tag",
    "Update",
    "Write",
)


@pytest.fixture(scope="module")
def workflow() -> dict[str, Any]:
    return load_workflow(WORKFLOW_PATH)


@pytest.fixture(scope="module")
def job(workflow: dict[str, Any]) -> dict[str, Any]:
    found: dict[str, Any] = workflow["jobs"][JOB_ID]
    return found


@pytest.fixture(scope="module")
def role() -> dict[str, Any]:
    roles = list(iam_roles(load_template(ROLE_PATH)))
    assert len(roles) == 1, "one template, one role, so there is one thing to reason about"
    return roles[0]


def named_step(job: dict[str, Any], fragment: str) -> dict[str, Any]:
    matching = [step for step in job["steps"] if fragment in step.get("name", "")]
    assert len(matching) == 1, f"expected exactly one step named for {fragment!r}"
    return matching[0]


def step_index(job: dict[str, Any], fragment: str) -> int:
    return next(
        index
        for index, step in enumerate(job["steps"])
        if fragment in step.get("name", "")
    )


def granted_actions(role: dict[str, Any]) -> set[str]:
    return {
        action
        for policy in role["Policies"]
        for statement in policy["PolicyDocument"]["Statement"]
        for action in statement_actions(statement)
    }


def test_something_actually_runs_the_report(job: dict[str, Any]) -> None:
    """Mutation: keep the job and drop the step, or rename the tool out from under it.

    The tool has had tests since the day it was written and nothing has ever run it, which
    is the state this change exists to leave. A job that installs the tooling, takes a
    credential and then reports nothing would look busy on the schedule page and answer the
    question it was added for on no morning at all.
    """
    invocations = [step["run"] for step in job["steps"] if REPORT_TOOL in step.get("run", "")]

    assert len(invocations) == 1
    assert "uv run --frozen python" in invocations[0], (
        "run through the locked environment, like every other job in this file"
    )


def test_a_report_naming_a_run_fails_the_job_and_still_reaches_the_summary(
    job: dict[str, Any],
    tmp_path: Path,
) -> None:
    """THE ONE THAT MATTERS. Mutation: drop `pipefail`, or append `|| true`.

    The tool exits 1 when it finds a run that promised a checkpoint and wrote none. That
    exit code is the entire signal: there is no alerting infrastructure here, so a job that
    prints the finding and returns zero is a job that reports six silently broken runs onto
    a page nobody opens.

    Piping into `tee` is what makes that one character away from happening. Without
    `pipefail` a pipeline reports the exit code of its last command, and `tee` always
    succeeds -- so the report would be written to the step summary and the run would be
    green. Proved by running the step against a stub that fails the way the tool fails,
    because reading the script for the word `pipefail` is not the same as watching the
    status come back.
    """
    summary = tmp_path / "summary.md"
    stub_bin = tmp_path / "bin"
    write_stub(stub_bin, "uv", 'echo "1 of them wrote nothing."\nexit 1\n')

    answer = run_step_script(
        named_step(job, "Report the runs that saved nothing")["run"],
        cwd=tmp_path,
        env={"RUNNER_TEMP": str(tmp_path), "GITHUB_STEP_SUMMARY": str(summary)},
        stub_bin=stub_bin,
    )

    assert answer.returncode != 0, (
        "the report found something and the step passed, so the exit code was swallowed "
        "somewhere between the tool and the runner"
    )
    assert "wrote nothing" in summary.read_text(encoding="utf-8"), (
        "a red run whose step summary is empty tells the reader to open the log, which is "
        "the half of the report that costs them the morning"
    )


def test_a_report_naming_nobody_leaves_the_job_green(
    job: dict[str, Any],
    tmp_path: Path,
) -> None:
    """The other side of the same branch, so the test above cannot pass by always failing.

    A scheduled check that is red on every run is one people learn to ignore, which is the
    same outcome as no check at all reached by a longer route.
    """
    summary = tmp_path / "summary.md"
    stub_bin = tmp_path / "bin"
    write_stub(stub_bin, "uv", 'echo "nothing here to be wrong"\n')

    answer = run_step_script(
        named_step(job, "Report the runs that saved nothing")["run"],
        cwd=tmp_path,
        env={"RUNNER_TEMP": str(tmp_path), "GITHUB_STEP_SUMMARY": str(summary)},
        stub_bin=stub_bin,
    )

    assert answer.returncode == 0, answer.stderr


def test_nothing_in_the_job_is_allowed_to_be_informational(
    workflow: dict[str, Any],
    job: dict[str, Any],
) -> None:
    """Mutation: `continue-on-error: true` on the job or on the reporting step.

    It is the obvious response to a job that goes red on the first morning, and it turns
    the job into one that cannot say anything. The header of this file argues the point for
    the three checks that were already here, and this is the fourth.
    """
    assert "continue-on-error" not in job
    assert "needs" not in job, "a failure elsewhere in this file must not skip the report"
    for step in job["steps"]:
        assert "continue-on-error" not in step, step.get("name")

    body = named_step(job, "Report the runs that saved nothing")["run"]
    assert "|| true" not in body
    assert "exit 0" not in body


def test_the_job_takes_a_token_and_assumes_the_reader_role(job: dict[str, Any]) -> None:
    """Mutation: keep `id-token: write` at the top of the file instead of on this job.

    The other three jobs read committed records and reach no AWS API, and they should stay
    unable to. Declaring the token on the job rather than on the workflow is what keeps the
    widening to the one job that argued for it.
    """
    assert job["permissions"] == {"contents": "read", "id-token": "write"}

    credentials = named_step(job, "Configure AWS credentials")
    assert credentials["with"]["role-to-assume"] == f"${{{{ vars.{ROLE_VARIABLE} }}}}"


def test_an_unset_role_variable_is_refused_before_a_credential_is_taken(
    job: dict[str, Any],
    tmp_path: Path,
) -> None:
    """Mutation: drop the guard and let configure-aws-credentials fail on an empty role.

    The stack this job needs is applied from a laptop, because the deployer role holds no
    `iam:CreateRole`, so the variable is unset until a person with an administrative
    credential sets it. An empty `role-to-assume` produces "Credentials could not be
    loaded, please check your action inputs", which reads as a broken federation and sends
    the reader to the OIDC configuration. The cause is one stack that was never applied.

    Run rather than read, because a guard that exits zero on an empty variable looks
    identical to one that works.
    """
    guard = named_step(job, "nightly reader role was never deployed")
    assert guard["env"] == {"ROLE_ARN": f"${{{{ vars.{ROLE_VARIABLE} }}}}"}
    assert step_index(job, "nightly reader role was never deployed") < step_index(
        job, "Configure AWS credentials"
    ), "a guard after the credential step guards nothing"

    refused = run_step_script(guard["run"], cwd=tmp_path, env={"ROLE_ARN": ""})

    assert refused.returncode == 1
    assert "nightly_reader_role_not_deployed" in refused.stderr
    # A diagnosis with nowhere to go is half an answer, and the template name is the half
    # that turns this into something the reader can act on.
    assert ROLE_VARIABLE in refused.stderr
    assert "infra/iam/nightly-reader-role.yaml" in refused.stderr
    assert "infra/README.md" in refused.stderr

    allowed = run_step_script(guard["run"], cwd=tmp_path, env={"ROLE_ARN": SOME_ROLE_ARN})

    assert allowed.returncode == 0, allowed.stderr


def test_the_workflow_syncs_the_one_prefix_the_role_can_list(
    job: dict[str, Any],
    role: dict[str, Any],
) -> None:
    """Mutation: sync the whole lineage bucket, which reads as the same thing.

    It is not. The role may list `intent/` and nothing else, so a sync of the bucket root
    is an access denial at 05:00 on a job whose failure is supposed to mean something about
    the runs. Both sides are read here so that widening either one alone fails.
    """
    source = command_tokens(named_step(job, "Fetch the admission intents")["run"], "s3", "sync")

    assert source[3] == f"s3://{LINEAGE_BUCKET}/intent/"

    listable = [
        statement
        for policy in role["Policies"]
        for statement in policy["PolicyDocument"]["Statement"]
        if statement["Resource"] == {"Fn::Sub": f"arn:${{AWS::Partition}}:s3:::{LINEAGE_BUCKET}"}
    ]

    assert len(listable) == 1
    assert listable[0]["Condition"] == {"StringLike": {"s3:prefix": "intent/*"}}


def test_the_reader_role_can_list_and_read_and_do_nothing_else(role: dict[str, Any]) -> None:
    """Mutation: add `s3:PutObject`, or `secretsmanager:GetSecretValue` while passing.

    This role is assumable by every job in `nightly.yml`, including one added later, which
    is tolerable only for as long as it is read-only. Asserted as an exact set rather than
    as an absence list, so an action added later has to be argued for in this test instead
    of merely not being forbidden.
    """
    actions = granted_actions(role)

    assert actions == {"s3:ListBucket", "s3:GetObject"}
    for action in actions:
        assert not any(fragment in action for fragment in MUTATING_ACTION_FRAGMENTS), action
        assert action.startswith("s3:"), action
        assert "*" not in action, action


def test_the_reader_role_reaches_the_two_buckets_the_report_asks_about(
    role: dict[str, Any],
) -> None:
    """Mutation: grant `s3:GetObject` on the outputs bucket beside the list.

    The report counts objects under a checkpoint prefix and never opens one, so a read
    grant there would let a scheduled job nobody watches pull a team's training output. The
    outputs bucket name is read from the contract that builds the prefix rather than
    written here twice, because the grant and the prefix drifting apart is a denial nobody
    sees until a morning.
    """
    statements = [
        statement
        for policy in role["Policies"]
        for statement in policy["PolicyDocument"]["Statement"]
    ]
    reach = {
        str(statement["Resource"]["Fn::Sub"]): set(statement_actions(statement))
        for statement in statements
    }

    assert reach == {
        f"arn:${{AWS::Partition}}:s3:::{LINEAGE_BUCKET}": {"s3:ListBucket"},
        f"arn:${{AWS::Partition}}:s3:::{LINEAGE_BUCKET}/intent/*": {"s3:GetObject"},
        f"arn:${{AWS::Partition}}:s3:::{OUTPUTS_BUCKET}": {"s3:ListBucket"},
    }

    outputs = next(
        statement
        for statement in statements
        if statement["Resource"]["Fn::Sub"].endswith(OUTPUTS_BUCKET)
    )
    # The same prefix shape `output_prefix` builds, so a listing cannot walk out of
    # `teams/{team}/runs/` into whatever else the bucket grows.
    assert outputs["Condition"] == {"StringLike": {"s3:prefix": "teams/*/runs/*"}}


def test_the_role_trusts_the_scheduled_file_and_carries_the_boundary(
    role: dict[str, Any],
) -> None:
    """Mutation: point `job_workflow_ref` at the workflows directory, or drop the boundary.

    A trust that matched a directory would make this role assumable from `submit-run.yml`,
    which is the file an unapproved dispatch reaches. The boundary is denied-by-default in
    this account rather than advisory: `iam:CreateRole` refuses a request that does not
    carry it, so a template without it does not create a weaker role, it fails.
    """
    assert role["RoleName"] == ROLE_NAME
    assert role["PermissionsBoundary"] == BOUNDARY
    assert role["MaxSessionDuration"] <= 3600

    statements = role["AssumeRolePolicyDocument"]["Statement"]
    assert len(statements) == 1
    assert statements[0]["Principal"] == {"Federated": OIDC_PROVIDER}
    assert statements[0]["Action"] == "sts:AssumeRoleWithWebIdentity"

    equals = statements[0]["Condition"]["StringEquals"]
    assert set(statements[0]["Condition"]) == {"StringEquals"}
    assert equals["token.actions.githubusercontent.com:job_workflow_ref"] == WORKFLOW_REF
    assert equals["token.actions.githubusercontent.com:aud"] == "sts.amazonaws.com"
    assert equals["token.actions.githubusercontent.com:repository_owner_id"] == "306859726"
    assert equals["token.actions.githubusercontent.com:repository_id"] == "1311508598"
    assert equals["token.actions.githubusercontent.com:sub"] == (
        "repo:edu-llm@306859726/platform@1311508598:ref:refs/heads/main"
    )
    assert not ACCOUNT_LITERAL.search(ROLE_PATH.read_text(encoding="utf-8"))


def test_no_role_that_already_existed_could_have_been_assumed_from_this_file() -> None:
    """The reason a new template was written, asserted rather than left in a PR body.

    Every GitHub role under `infra/iam/` pins `job_workflow_ref` with a `StringEquals`
    naming one workflow file, so a token minted for `nightly.yml` matched none of them and
    adding a job to an existing file was not an option either. Read from the directory so
    that a second template trusting this file -- which would be a second way into the same
    schedule, reviewed somewhere else -- fails here.
    """
    trusting = {
        path.name
        for path in sorted(IAM_ROOT.glob("*.yaml"))
        for properties in iam_roles(load_template(path))
        for statement in properties["AssumeRolePolicyDocument"]["Statement"]
        if WORKFLOW_REF
        in str(
            statement.get("Condition", {})
            .get("StringEquals", {})
            .get("token.actions.githubusercontent.com:job_workflow_ref", "")
        )
    }

    assert trusting == {ROLE_PATH.name}
