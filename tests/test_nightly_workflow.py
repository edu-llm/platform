"""The two nightly checks that read the account, and the role that lets them.

**Both exist because the failure they describe is silent.** A training run whose save folder
was left at OLMo-core's ``/tmp`` default trains, writes nothing anybody can reach, and exits
zero; a W&B key that W&B refuses costs nothing at run time either, because a training run
does not fail when its logging is declined. Neither shows up as a red anything, so the check
is the only thing that reports them, and a check whose finding does not fail the job is the
same silence with more steps. That is what most of this module is about: the tools are
invoked, and what they find is treated as a failure.

The step bodies are executed rather than pattern-matched. A test that asserted ``exit 1``
appears somewhere in the script would pass for a script that reaches it only when the report
is empty, which is the mistake worth catching -- so the scripts are run the way the runner
runs them, with the tool replaced by a stub that exits how the real one would.

The role is here too rather than in a file of its own, because its shape is the argument for
the workflow's shape. Two scheduled jobs can assume it, it can read three named things, and
it holds no write anywhere in the account.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from workflow_support import (
    WORKFLOWS_ROOT,
    command_tokens,
    load_workflow,
    run_step_script,
    step,
    write_stub,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_PATH = WORKFLOWS_ROOT / "nightly.yml"
ROLE_PATH = PROJECT_ROOT / "infra" / "iam" / "nightly-reader-role.yaml"

RECONCILE_JOB = "checkpoint-reconciliation"
WANDB_JOB = "wandb-credential"

RECONCILE_TOOL = "tools/find_runs_that_saved_nothing.py"
WANDB_TOOL = "tools/verify_wandb_credential.py"

#: What the repository variable is called. Spelled here as a literal because the workflow
#: reads it by name and a rename on one side alone resolves to the empty string, which is
#: the failure the guard step in each job exists to name.
ROLE_VARIABLE = "AWS_NIGHTLY_READER_ROLE_ARN"


@pytest.fixture(scope="module")
def workflow() -> dict[str, Any]:
    return load_workflow(WORKFLOW_PATH)


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
        value for value in template["Resources"].values() if value["Type"] == "AWS::IAM::Role"
    )
    return resource["Properties"]


def statements(role: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        statement
        for policy in role["Policies"]
        for statement in policy["PolicyDocument"]["Statement"]
    ]


def scripts(workflow: dict[str, Any], job_id: str) -> str:
    return "\n".join(item["run"] for item in workflow["jobs"][job_id]["steps"] if "run" in item)


# ----------------------------------------------------------------------------------------
# The checks are invoked at all
# ----------------------------------------------------------------------------------------


def test_the_nightly_runs_the_checkpoint_reconciliation(workflow: dict[str, Any]) -> None:
    """Mutation: keep the job and drop the step, or point it at a different tool.

    The whole of the checkpoint signal is one command. A job that installs the tooling,
    takes a credential and then runs nothing is green every night and says nothing, which is
    indistinguishable from the state before it existed.
    """
    body = scripts(workflow, RECONCILE_JOB)

    assert RECONCILE_TOOL in body
    assert "--lineage-root" in body, "the report reads the intent tree and is handed a path"
    assert "--output" in body, "the report is captured so it can be read after the failure"


def test_the_nightly_runs_the_wandb_credential_check(workflow: dict[str, Any]) -> None:
    """Mutation: check the shape only, by passing --offline.

    The fault this catches was the right length and very nearly the right shape: a good key
    with the literal word `api` glued to the front, which is what pasting the netrc line as
    one token produces. Only W&B's own answer settles whether a key would be accepted, so an
    offline run of this check would have passed the value that was already broken.
    """
    body = scripts(workflow, WANDB_JOB)

    assert WANDB_TOOL in body
    assert "--offline" not in body


def test_the_reconciliation_fetches_only_the_intent_records(workflow: dict[str, Any]) -> None:
    """Mutation: sync the whole lineage bucket, which is one path segment away.

    The role's listing grant is conditioned on the `intent/` prefix, so a wider sync fails
    at 05:00 as an access denial rather than here. The narrower reason is the better one: the
    result records would say how each run ended, and this check deliberately does not ask,
    because its entire point is that a run recorded as a success can have saved nothing.
    """
    tokens = command_tokens(scripts(workflow, RECONCILE_JOB), "s3", "sync")

    assert tokens[3] == "s3://${LINEAGE_BUCKET}/intent/"


# ----------------------------------------------------------------------------------------
# What they find is treated as a failure
# ----------------------------------------------------------------------------------------


def reconciliation_step(workflow: dict[str, Any]) -> str:
    return step(workflow["jobs"][RECONCILE_JOB], "Reconcile what those runs actually wrote")["run"]


def wandb_step(workflow: dict[str, Any]) -> str:
    return step(workflow["jobs"][WANDB_JOB], "Ask W&B whether it would accept the stored key")[
        "run"
    ]


def stub_tool(tmp_path: Path, *, exit_code: int, report: str = "") -> Path:
    """A stand-in for the tool, honouring --output the way the real one does.

    Writing wherever the script says rather than to a fixed name, so a step that stopped
    passing `--output` is a test failure here rather than an empty step summary at 05:00.
    """
    return write_stub(
        tmp_path / "bin",
        "uv",
        f"""
destination=""
while [[ $# -gt 0 ]]; do
  if [[ "$1" == "--output" ]]; then destination="$2"; fi
  shift
done
if [[ -n "${{destination}}" ]]; then
  printf '%s\\n' {report!r} > "${{destination}}"
else
  printf '%s\\n' {report!r}
fi
exit {exit_code}
""",
    )


def run_reconciliation(workflow: dict[str, Any], tmp_path: Path, *, exit_code: int) -> Any:
    summary = tmp_path / "summary.md"
    summary.touch()
    stub = stub_tool(tmp_path, exit_code=exit_code, report="# Runs that promised a checkpoint")
    return run_step_script(
        reconciliation_step(workflow),
        cwd=tmp_path,
        env={"GITHUB_STEP_SUMMARY": str(summary)},
        stub_bin=stub.parent,
    )


def test_a_run_with_no_loadable_checkpoint_fails_the_nightly(
    workflow: dict[str, Any], tmp_path: Path
) -> None:
    """THE ONE THAT MATTERS. Mutation: report the finding and exit zero.

    There is no alerting on this platform, so a red scheduled run is the entire signal, and
    a job that writes a table into the step summary and then succeeds is a job nobody reads.
    The report exits 1 when it finds a run that promised a checkpoint and has nothing
    loadable, and this asserts the step carries that through rather than swallowing it.
    """
    finished = run_reconciliation(workflow, tmp_path, exit_code=1)

    assert finished.returncode != 0, finished.stdout
    assert "runs_promised_a_checkpoint_and_have_none" in finished.stderr


def test_a_report_that_could_not_be_produced_is_not_read_as_a_clean_one(
    workflow: dict[str, Any], tmp_path: Path
) -> None:
    """Mutation: treat any non-zero exit as the same finding.

    The report exits 2 when it could not read the records or the bucket, which is not a
    statement about the runs at all. Both fail the job, and they have to fail it saying
    different things: somebody who reads the second as the first goes looking for broken runs
    on the morning a credential lapsed.
    """
    finished = run_reconciliation(workflow, tmp_path, exit_code=2)

    assert finished.returncode != 0
    assert "checkpoint_reconciliation_unusable" in finished.stderr
    assert "runs_promised_a_checkpoint_and_have_none" not in finished.stderr


def test_a_clean_reconciliation_passes(workflow: dict[str, Any], tmp_path: Path) -> None:
    """The other side, so the step cannot pass this file by failing unconditionally.

    A check that is red every night whatever it finds is a check that gets muted, and then
    the eight runs it was added for go back to being invisible.
    """
    finished = run_reconciliation(workflow, tmp_path, exit_code=0)

    assert finished.returncode == 0, finished.stderr


def test_the_report_reaches_the_log_and_not_only_the_step_summary(
    workflow: dict[str, Any], tmp_path: Path
) -> None:
    """Mutation: write the table to the step summary alone.

    Same argument the queue view was moved for. The summary is a second page to open, and the
    person triaging a red cross is already looking at the log.
    """
    summary = tmp_path / "summary.md"
    summary.touch()
    stub = stub_tool(tmp_path, exit_code=1, report="run_019fbbfb wrote a fragment")

    finished = run_step_script(
        reconciliation_step(workflow),
        cwd=tmp_path,
        env={"GITHUB_STEP_SUMMARY": str(summary)},
        stub_bin=stub.parent,
    )

    assert "run_019fbbfb wrote a fragment" in finished.stdout
    assert "run_019fbbfb wrote a fragment" in summary.read_text(encoding="utf-8")


def test_a_key_wandb_would_refuse_fails_the_nightly(
    workflow: dict[str, Any], tmp_path: Path
) -> None:
    """Mutation: report the faults and exit zero.

    The check exists because nothing else goes red when W&B declines a key. A job that prints
    `looks_wrong` and succeeds reproduces exactly the condition it was written to end.
    """
    stub = stub_tool(tmp_path, exit_code=1, report='{"looks_wrong": ["prefixed with api"]}')

    finished = run_step_script(
        wandb_step(workflow), cwd=tmp_path, env={}, stub_bin=stub.parent
    )

    assert finished.returncode != 0
    assert "wandb_credential_would_be_refused" in finished.stderr


def test_a_key_wandb_accepts_passes(workflow: dict[str, Any], tmp_path: Path) -> None:
    stub = stub_tool(tmp_path, exit_code=0, report='{"looks_wrong": []}')

    finished = run_step_script(
        wandb_step(workflow), cwd=tmp_path, env={}, stub_bin=stub.parent
    )

    assert finished.returncode == 0, finished.stderr


# ----------------------------------------------------------------------------------------
# The identity both jobs run as
# ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("job_id", [RECONCILE_JOB, WANDB_JOB])
def test_a_missing_role_is_named_rather_than_reported_as_no_credentials(
    workflow: dict[str, Any], job_id: str
) -> None:
    """Mutation: drop the guard and let configure-aws-credentials fail on an empty role.

    That is what happened to the cancel path. An empty role-to-assume produces "Credentials
    could not be loaded, please check your action inputs", which reads as a broken secret or
    expired federation and sends the reader to the OIDC configuration. The cause is one IAM
    stack that was never applied, and nothing in that message says so.
    """
    steps = workflow["jobs"][job_id]["steps"]
    names = [item.get("name", "") for item in steps]

    guard = next(index for index, name in enumerate(names) if "reader role is deployed" in name)
    credentialed = next(index for index, name in enumerate(names) if "AWS credentials" in name)

    assert guard < credentialed, "a guard after the credential step guards nothing"

    body = steps[guard]["run"]
    assert "nightly_reader_role_not_deployed" in body
    # The reader needs somewhere to go, not only a diagnosis.
    assert "infra/README.md" in body
    assert "nightly-reader-role.yaml" in body


@pytest.mark.parametrize("job_id", [RECONCILE_JOB, WANDB_JOB])
def test_both_jobs_assume_the_reader_role_and_no_other(
    workflow: dict[str, Any], job_id: str
) -> None:
    """Mutation: point one of them at the deployer or the admission role.

    Either would work, and either would put a role that can create infrastructure or admit a
    run behind a scheduled workflow nobody watches dispatch.
    """
    assumed = [
        item["with"]["role-to-assume"]
        for item in workflow["jobs"][job_id]["steps"]
        if "aws-actions/configure-aws-credentials" in str(item.get("uses", ""))
    ]

    assert assumed == [f"${{{{ vars.{ROLE_VARIABLE} }}}}"]


def test_the_role_can_read_and_cannot_write(role: dict[str, Any]) -> None:
    """THE PROPERTY THE WHOLE TEMPLATE IS FOR. Mutation: add one write, for convenience.

    A check that can change what it is checking can produce its own all-clear, and the two
    tempting writes are the two worst: s3:PutObject would let the reconciliation create the
    checkpoint it is looking for, and secretsmanager:PutSecretValue would let the credential
    check repair the value instead of reporting it.

    Asserted as an exact set rather than an absence list, so an action added later has to be
    argued for here rather than merely not forbidden.
    """
    granted = {
        action
        for statement in statements(role)
        for action in (
            statement["Action"]
            if isinstance(statement["Action"], list)
            else [statement["Action"]]
        )
    }

    assert granted == {
        "s3:GetObject",
        "s3:ListBucket",
        "s3:GetBucketLocation",
        "secretsmanager:GetSecretValue",
    }
    assert all(statement["Effect"] == "Allow" for statement in statements(role))


def test_no_grant_reaches_a_whole_bucket_or_every_secret(role: dict[str, Any]) -> None:
    """Mutation: widen a resource to the bucket, or to `*`, to stop chasing an access denial.

    This is a shared sandbox account with sixteen other teams in it. A read grant is not
    harmless here: the lineage store holds every run's records and Secrets Manager holds
    everybody's credentials, so an unscoped read is an exfiltration path with a schedule.
    """
    resources = [
        rendered
        for statement in statements(role)
        for rendered in (
            statement["Resource"]
            if isinstance(statement["Resource"], list)
            else [statement["Resource"]]
        )
    ]

    assert resources, "the policy grants nothing at all"
    for resource in resources:
        rendered = resource["Fn::Sub"] if isinstance(resource, dict) else resource
        assert rendered != "*", "a wildcard resource is not a scoped grant"
        assert "sbsandbox-intern-edullm-" in rendered, rendered

    objects = [
        resource["Fn::Sub"]
        for statement in statements(role)
        if "s3:GetObject" in str(statement["Action"])
        for resource in [statement["Resource"]]
    ]
    assert all(reachable.count("/") >= 1 for reachable in objects), (
        "an object grant whose ARN stops at the bucket name reaches every key in it"
    )


def test_listing_is_confined_to_the_prefixes_each_check_reads(role: dict[str, Any]) -> None:
    """Mutation: drop the prefix condition, because listing is bucket-level anyway.

    That is the point: s3:ListBucket cannot be scoped by an object ARN, so without the
    condition it enumerates the whole bucket. For the lineage store that is every record of
    every run, and this check reads one prefix of it.
    """
    conditions = {
        statement["Resource"]["Fn::Sub"].rsplit(":::", 1)[1]: statement["Condition"]["StringLike"][
            "s3:prefix"
        ]
        for statement in statements(role)
        if "s3:ListBucket" in str(statement["Action"])
    }

    assert conditions == {
        "sbsandbox-intern-edullm-lineage": "intent/*",
        "sbsandbox-intern-edullm-outputs": "teams/*/runs/*",
    }


def test_the_role_trusts_only_the_nightly_file(role: dict[str, Any]) -> None:
    """Mutation: list a second workflow file, or widen the ref.

    Every OIDC role here pins one file, which is what makes each role's reach readable off
    the workflow that can assume it. This role's own reason for existing is that pinning: a
    token minted for nightly.yml matched none of the five that already existed, and the
    alternative on the table was to widen one of theirs.

    The ref matters as much as the file. A schedule runs on the default branch, so pinning
    refs/heads/main costs nothing and stops a branch borrowing the role by adding a job to
    nightly.yml and dispatching it from the branch.
    """
    conditions = role["AssumeRolePolicyDocument"]["Statement"][0]["Condition"]["StringEquals"]

    assert conditions["token.actions.githubusercontent.com:job_workflow_ref"] == (
        "edu-llm/platform/.github/workflows/nightly.yml@refs/heads/main"
    )
    assert conditions["token.actions.githubusercontent.com:sub"].endswith(":ref:refs/heads/main")
    assert conditions["token.actions.githubusercontent.com:aud"] == "sts.amazonaws.com"


def test_the_role_carries_the_boundary_every_role_here_carries(role: dict[str, Any]) -> None:
    """Mutation: omit it, since the inline policy is narrow already.

    The boundary is what stops a role in this account growing past what the sandbox allows,
    and it is not implied by a narrow policy. Every other template here carries it, and a
    role created without one cannot be given one by amending the stack later without a
    replacement.
    """
    assert role["PermissionsBoundary"]["Fn::Sub"].endswith("policy/InternSandboxBoundary")
