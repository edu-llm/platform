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
is empty, which is the mistake worth catching, so the scripts are run the way the runner runs
them, with the tool replaced by a stub that exits how the real one would.

The role is here too rather than in a file of its own, because its shape is the argument for
the workflow's shape. Two scheduled jobs can assume it, it can read three named things, and
it holds no write anywhere in the account. The action set is asserted exactly rather than
checked for the absence of anything alarming, because a trust policy cannot distinguish jobs
within a workflow: every job in ``nightly.yml`` presents the same claims and any of them can
assume this role, including one added later.
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
    step,
    write_stub,
)

from edullm_platform.admission_denials import LINEAGE_BUCKET
from edullm_platform.contracts.results import OUTPUTS_BUCKET

PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = WORKFLOWS_ROOT / "nightly.yml"
ROLE_PATH = IAM_ROOT / "nightly-reader-role.yaml"

RECONCILE_JOB = "checkpoint-reconciliation"
WANDB_JOB = "wandb-credential"

RECONCILE_TOOL = "tools/find_runs_that_saved_nothing.py"
WANDB_TOOL = "tools/verify_wandb_credential.py"

RECONCILE_STEP = "Reconcile what those runs actually wrote"
WANDB_STEP = "Ask W&B whether it would accept the stored key"
GUARD_STEP = "Check the nightly reader role is deployed"

#: What the repository variable is called, and what the role it names is called. Spelled here
#: as literals because the workflow reads the variable by name and a rename on one side alone
#: resolves to the empty string, which is the failure the guard step in each job exists to
#: name, and because the variable is set by hand in the repository settings and so cannot be
#: found by a grep of the tree.
ROLE_VARIABLE = "AWS_NIGHTLY_READER_ROLE_ARN"
ROLE_NAME = "sbsandbox-intern-edullm-nightly-reader"
WORKFLOW_REF = "edu-llm/platform/.github/workflows/nightly.yml@refs/heads/main"

#: The secret name is spelled out because the grant is scoped to it by name, and a rename on
#: either side alone is an access denial at 05:00 rather than a test failure at review time.
WANDB_SECRET_NAME = "sbsandbox-intern-edullm-wandb-api-key"

#: A plausible ARN for the guard to accept, on the documentation account id every other test
#: here uses. A run of twelve digits that is not that one reads as a real account to
#: `tests/test_evidence.py`, which scans the tracked tree and does not care that this file is
#: a test.
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
def role() -> dict[str, Any]:
    roles = list(iam_roles(load_template(ROLE_PATH)))
    assert len(roles) == 1, "one template, one role, so there is one thing to reason about"
    return roles[0]


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
    assert "uv run --frozen python" in body, (
        "run through the locked environment, like every other job in this file"
    )


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
    assert "uv run --frozen python" in body


def test_the_reconciliation_fetches_only_the_prefix_the_role_can_list(
    workflow: dict[str, Any],
    role: dict[str, Any],
) -> None:
    """Mutation: sync the whole lineage bucket, which is one path segment away.

    The role's listing grant is conditioned on the `intent/` prefix, so a wider sync fails at
    05:00 as an access denial rather than here. The narrower reason is the better one: the
    result records would say how each run ended, and this check deliberately does not ask,
    because its entire point is that a run recorded as a success can have saved nothing.

    Both sides are read here so that widening either one alone fails.
    """
    reconciliation = workflow["jobs"][RECONCILE_JOB]
    fetch = step(reconciliation, "Fetch the intent records")

    assert fetch["env"]["LINEAGE_BUCKET"] == LINEAGE_BUCKET
    assert command_tokens(fetch["run"], "s3", "sync")[3] == "s3://${LINEAGE_BUCKET}/intent/"

    listable = [
        statement
        for statement in statements(role)
        if statement["Resource"] == {"Fn::Sub": f"arn:${{AWS::Partition}}:s3:::{LINEAGE_BUCKET}"}
    ]

    assert len(listable) == 1
    assert listable[0]["Condition"] == {"StringLike": {"s3:prefix": "intent/*"}}


# ----------------------------------------------------------------------------------------
# What they find is treated as a failure
# ----------------------------------------------------------------------------------------


def reconciliation_step(workflow: dict[str, Any]) -> str:
    return step(workflow["jobs"][RECONCILE_JOB], RECONCILE_STEP)["run"]


def wandb_step(workflow: dict[str, Any]) -> str:
    return step(workflow["jobs"][WANDB_JOB], WANDB_STEP)["run"]


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

    There is no alerting on this platform, so a red scheduled run is the entire signal, and a
    job that writes a table into the step summary and then succeeds is a job nobody reads.
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

    finished = run_step_script(wandb_step(workflow), cwd=tmp_path, env={}, stub_bin=stub.parent)

    assert finished.returncode != 0
    assert "wandb_credential_would_be_refused" in finished.stderr


def test_a_key_wandb_accepts_passes(workflow: dict[str, Any], tmp_path: Path) -> None:
    stub = stub_tool(tmp_path, exit_code=0, report='{"looks_wrong": []}')

    finished = run_step_script(wandb_step(workflow), cwd=tmp_path, env={}, stub_bin=stub.parent)

    assert finished.returncode == 0, finished.stderr


@pytest.mark.parametrize(
    ("job_id", "step_name"), [(RECONCILE_JOB, RECONCILE_STEP), (WANDB_JOB, WANDB_STEP)]
)
def test_nothing_in_either_job_is_allowed_to_be_informational(
    workflow: dict[str, Any], job_id: str, step_name: str
) -> None:
    """Mutation: `continue-on-error: true` on the job or on the reporting step.

    It is the obvious response to a job that goes red on the first morning, and it turns the
    job into one that cannot say anything. The header of the workflow argues the point for
    the three checks that were already there, and these are the fourth and fifth.
    """
    job = workflow["jobs"][job_id]

    assert "continue-on-error" not in job
    assert "needs" not in job, "a failure elsewhere in this file must not skip this"
    for item in job["steps"]:
        assert "continue-on-error" not in item, item.get("name")

    body = step(job, step_name)["run"]
    assert "|| true" not in body
    assert "exit 0" not in body


# ----------------------------------------------------------------------------------------
# The identity both jobs run as
# ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("job_id", [RECONCILE_JOB, WANDB_JOB])
def test_a_missing_role_is_named_rather_than_reported_as_no_credentials(
    workflow: dict[str, Any], job_id: str, tmp_path: Path
) -> None:
    """Mutation: drop the guard and let configure-aws-credentials fail on an empty role.

    That is what happened to the cancel path. An empty role-to-assume produces "Credentials
    could not be loaded, please check your action inputs", which reads as a broken secret or
    expired federation and sends the reader to the OIDC configuration. The cause is one IAM
    stack that was never applied, and nothing in that message says so.

    Run rather than read, because a guard that exits zero on an empty variable looks
    identical to one that works.
    """
    steps = workflow["jobs"][job_id]["steps"]
    names = [item.get("name", "") for item in steps]

    guard = next(index for index, name in enumerate(names) if "reader role is deployed" in name)
    credentialed = next(index for index, name in enumerate(names) if "AWS credentials" in name)

    assert guard < credentialed, "a guard after the credential step guards nothing"
    assert steps[guard]["env"] == {"ROLE_ARN": f"${{{{ vars.{ROLE_VARIABLE} }}}}"}

    refused = run_step_script(steps[guard]["run"], cwd=tmp_path, env={"ROLE_ARN": ""})

    assert refused.returncode == 1
    assert "nightly_reader_role_not_deployed" in refused.stderr
    # A diagnosis with nowhere to go is half an answer, and the template name is the half
    # that turns this into something the reader can act on.
    assert ROLE_VARIABLE in refused.stderr
    assert "nightly-reader-role.yaml" in refused.stderr
    assert "infra/README.md" in refused.stderr

    allowed = run_step_script(steps[guard]["run"], cwd=tmp_path, env={"ROLE_ARN": SOME_ROLE_ARN})

    assert allowed.returncode == 0, allowed.stderr


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
    assert workflow["jobs"][job_id]["permissions"] == {"contents": "read", "id-token": "write"}


def test_the_role_can_read_and_cannot_write(role: dict[str, Any]) -> None:
    """THE PROPERTY THE WHOLE TEMPLATE IS FOR. Mutation: add one write, for convenience.

    A check that can change what it is checking can produce its own all-clear, and the two
    tempting writes are the two worst: s3:PutObject would let the reconciliation create the
    checkpoint it is looking for, and secretsmanager:PutSecretValue would let the credential
    check repair the value instead of reporting it.

    Asserted as an exact set rather than an absence list, so an action added later has to be
    argued for here rather than merely not forbidden. The substring pass under it is what
    keeps reading true once somebody has argued one in.
    """
    granted = {
        action for statement in statements(role) for action in statement_actions(statement)
    }

    assert granted == {
        "s3:GetObject",
        "s3:ListBucket",
        "s3:GetBucketLocation",
        "secretsmanager:GetSecretValue",
    }
    for action in granted:
        assert not any(fragment in action for fragment in MUTATING_ACTION_FRAGMENTS), action
        assert action.startswith(("s3:", "secretsmanager:")), action
        assert "*" not in action, action

    # ListSecrets has no resource type, so a grant of it could not be scoped to one secret
    # and would let a scheduled job enumerate every secret in the account.
    assert "secretsmanager:ListSecrets" not in granted
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
        str(statement["Resource"]["Fn::Sub"])
        for statement in statements(role)
        if "s3:GetObject" in str(statement["Action"])
    ]
    assert all(reachable.count("/") >= 1 for reachable in objects), (
        "an object grant whose ARN stops at the bucket name reaches every key in it"
    )


def test_the_role_reads_the_two_buckets_the_reconciliation_asks_about(
    role: dict[str, Any],
) -> None:
    """Mutation: drop the outputs read, on the ground that the report only counts objects.

    It did until `inspect_checkpoint` replaced the count. Deciding whether a checkpoint would
    load means reading it: the `_SUCCESS` marker is fetched with GetObject and each member of
    a step directory is headed, which S3 authorises as GetObject too. Without this grant the
    report exits 2 with an access denial every night, which is not a statement about the runs.

    The bucket names are read from the contracts that build the prefixes rather than written
    here twice, because the grant and the prefix drifting apart is a denial nobody sees until
    a morning.
    """
    reach = {
        str(statement["Resource"]["Fn::Sub"]): set(statement_actions(statement))
        for statement in statements(role)
        if ":s3:::" in str(statement["Resource"]["Fn::Sub"])
    }

    assert reach == {
        f"arn:${{AWS::Partition}}:s3:::{LINEAGE_BUCKET}": {
            "s3:ListBucket",
            "s3:GetBucketLocation",
        },
        f"arn:${{AWS::Partition}}:s3:::{LINEAGE_BUCKET}/intent/*": {"s3:GetObject"},
        f"arn:${{AWS::Partition}}:s3:::{OUTPUTS_BUCKET}": {
            "s3:ListBucket",
            "s3:GetBucketLocation",
        },
        f"arn:${{AWS::Partition}}:s3:::{OUTPUTS_BUCKET}/teams/*/runs/*": {"s3:GetObject"},
    }


def test_listing_is_confined_to_the_prefixes_each_check_reads(role: dict[str, Any]) -> None:
    """Mutation: drop the prefix condition, because listing is bucket-level anyway.

    That is the point: s3:ListBucket cannot be scoped by an object ARN, so without the
    condition it enumerates the whole bucket. For the lineage store that is every record of
    every run, and this check reads one prefix of it. The outputs condition is the same shape
    `output_prefix` builds, so a listing cannot walk out of `teams/{team}/runs/` into
    whatever else the bucket grows.
    """
    conditions = {
        statement["Resource"]["Fn::Sub"].rsplit(":::", 1)[1]: statement["Condition"]["StringLike"][
            "s3:prefix"
        ]
        for statement in statements(role)
        if "s3:ListBucket" in str(statement["Action"])
    }

    assert conditions == {LINEAGE_BUCKET: "intent/*", OUTPUTS_BUCKET: "teams/*/runs/*"}


def test_the_secret_grant_names_one_secret_rather_than_the_account(
    role: dict[str, Any],
) -> None:
    """Mutation: widen the resource to `secret:*`, which still reads as scoped.

    It is not. `secret:*` is every secret in the account, and this role is assumable by any
    job in a scheduled workflow. The trailing `-*` on the name is Secrets Manager's own
    six-character suffix, chosen at creation and not stable across a delete and recreate, so
    the name cannot be matched without it.
    """
    secrets = [
        statement
        for statement in statements(role)
        if ":secretsmanager:" in str(statement["Resource"]["Fn::Sub"])
    ]

    assert len(secrets) == 1
    resource = str(secrets[0]["Resource"]["Fn::Sub"])
    assert resource.endswith(f":secret:{WANDB_SECRET_NAME}-*"), resource
    assert not resource.endswith(":secret:*")
    assert statement_actions(secrets[0]) == ["secretsmanager:GetSecretValue"]


def test_the_role_trusts_the_scheduled_file_and_carries_the_boundary(
    role: dict[str, Any],
) -> None:
    """Mutation: point `job_workflow_ref` at the workflows directory, or drop the boundary.

    A trust that matched a directory would make this role assumable from `submit-run.yml`,
    which is the file an unapproved dispatch reaches. The ref matters as much as the file: a
    schedule runs on the default branch, so pinning refs/heads/main stops a branch borrowing
    the role by adding a job to nightly.yml and dispatching it from the branch.

    The boundary is denied-by-default in this account rather than advisory: `iam:CreateRole`
    refuses a request that does not carry it, so a template without it does not create a
    weaker role, it fails.
    """
    assert role["RoleName"] == ROLE_NAME
    assert role["PermissionsBoundary"] == BOUNDARY
    assert role["MaxSessionDuration"] <= 3600

    trust = role["AssumeRolePolicyDocument"]["Statement"]
    assert len(trust) == 1
    assert trust[0]["Principal"] == {"Federated": OIDC_PROVIDER}
    assert trust[0]["Action"] == "sts:AssumeRoleWithWebIdentity"
    assert set(trust[0]["Condition"]) == {"StringEquals"}

    equals = trust[0]["Condition"]["StringEquals"]
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

    Every GitHub role under `infra/iam/` pins `job_workflow_ref` with a `StringEquals` naming
    one workflow file, so a token minted for `nightly.yml` matched none of them and adding a
    job to an existing file was not an option either. Read from the directory so that a
    second template trusting this file, which would be a second way into the same schedule
    reviewed somewhere else, fails here.
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


def test_the_verifier_the_job_runs_is_the_one_that_never_prints_the_key() -> None:
    """Mutation: point the job at a script that prints what it read.

    This job's output goes to a scheduled log and a step summary in a public repository, so
    the property that makes it safe to run there is that the tool reports a length, a
    four-character prefix and a truncated digest rather than the value. Asserted here as well
    as in the verifier's own tests, because the reason it matters is the log this job writes.
    """
    source = (PROJECT_ROOT / WANDB_TOOL).read_text(encoding="utf-8")

    assert "Never prints the key" in source
    assert "fingerprint" in source
