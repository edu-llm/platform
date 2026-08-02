"""The three nightly checks that read the account, and the role that lets them.

**All three exist because the failure they describe is silent.** A training run whose save
folder was left at OLMo-core's ``/tmp`` default trains, writes nothing anybody can reach, and
exits zero; a W&B key that W&B refuses costs nothing at run time either, because a training
run does not fail when its logging is declined; and a Lambda deployed out of band goes on
admitting submissions and writing lineage with nothing to distinguish it from the reviewed
code. None shows up as a red anything, so the check is the only thing that reports them, and
a check whose finding does not fail the job is the same silence with more steps. That is what
most of this module is about: the tools are invoked, and what they find is treated as a
failure.

The step bodies are executed rather than pattern-matched. A test that asserted ``exit 1``
appears somewhere in the script would pass for a script that reaches it only when the report
is empty, which is the mistake worth catching, so the scripts are run the way the runner runs
them, with the tool replaced by a stub that exits how the real one would.

The third job is the exception and is deliberately shorter. Its tool already prints a
machine-readable reason and already separates a disagreement from an unanswered question in
its own exit code, so the step runs it and nothing else; what is asserted for it is that the
translation is genuinely absent rather than quietly turned into a pass.

The role is here too rather than in a file of its own, because its shape is the argument for
the workflow's shape. Three scheduled jobs can assume it, everything it reads is named, and
it holds no write anywhere in the account. The action set is asserted exactly rather than
checked for the absence of anything alarming, because a trust policy cannot distinguish jobs
within a workflow: every job in ``nightly.yml`` presents the same claims and any of them can
assume this role, including one added later.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest
from infrastructure_support import (
    ACCOUNT_LITERAL,
    BOUNDARY,
    IAM_ROOT,
    INFRA_ROOT,
    OIDC_PROVIDER,
    iam_roles,
    load_template,
    resource_of_type,
    statement_actions,
    statement_resources,
)
from workflow_support import (
    WORKFLOWS_ROOT,
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
RELEASE_JOB = "deployed-lambda-release"
STACKS_JOB = "deployed-stack-templates"
BOARD_JOB = "visibility-board"

RECONCILE_TOOL = "tools/find_runs_that_saved_nothing.py"
WANDB_TOOL = "tools/verify_wandb_credential.py"
RELEASE_TOOL = "tools/verify_deployed_lambdas.py"
STACKS_TOOL = "tools/verify_deployed_stacks.py"
BOARD_TOOL = "tools/visibility_board.py"

RECONCILE_STEP = "Reconcile what those runs actually wrote"
WANDB_STEP = "Ask W&B whether it would accept the stored key"
RELEASE_STEP = "Compare what AWS is running against what was released"
STACKS_STEP = "Compare each deployed stack against the template main declares"
BOARD_STEP = "Join what W&B, the account and the outputs bucket each say"
GUARD_STEP = "Check the nightly reader role is deployed"

#: Every job here that takes a credential. Each one is held to the same guard, the same
#: role and the same refusal to be informational, so the list is what a sixth such job has
#: to join rather than a set of tests it has to remember to be added to.
CREDENTIALED_JOBS = (RECONCILE_JOB, WANDB_JOB, RELEASE_JOB, STACKS_JOB, BOARD_JOB)

#: The two functions the release check reads, and the templates that name them to
#: CloudFormation. Read from the templates rather than spelled here, because the IAM grant
#: is written against these names and a rename that missed one of the three places would be
#: an access denial at 05:00 rather than a failure at review.
LAMBDA_TEMPLATES = ("admission-state-machine.yaml", "batch-events.yaml")

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


def test_the_reconciliation_fetches_only_the_prefixes_the_role_can_list(
    workflow: dict[str, Any],
    role: dict[str, Any],
) -> None:
    """Mutation: sync the whole lineage bucket, which is one path segment away.

    The role's listing grant is conditioned, so a wider sync fails at 05:00 as an access
    denial rather than here. Both sides are read so that widening either one alone fails.

    THIS USED TO ASSERT `intent/` ALONE, AND SAID THE RESULT RECORDS WERE DELIBERATELY NOT
    ASKED FOR, because a run recorded as a success can have saved nothing. That reason was
    right about the answer and wrong about the question. Reading the outcome to decide
    *whether to ask* is not the same as believing it: a run recorded as a success is still
    read out of the bucket exactly as before. What the old scope bought was a report that
    could not tell a run which finished and saved nothing from one that died at
    `wandb.init()`, and it spent fourteen of those against the one that mattered.
    """
    reconciliation = workflow["jobs"][RECONCILE_JOB]
    fetch = step(reconciliation, "Fetch the intent records")

    assert fetch["env"]["LINEAGE_BUCKET"] == LINEAGE_BUCKET

    synced = {
        line.split("s3://${LINEAGE_BUCKET}/")[1].split()[0].strip('"').rstrip("/")
        for line in fetch["run"].splitlines()
        if "s3://${LINEAGE_BUCKET}/" in line
    }

    assert synced == {"intent", "result"}

    listable = [
        statement
        for statement in statements(role)
        if statement["Resource"] == {"Fn::Sub": f"arn:${{AWS::Partition}}:s3:::{LINEAGE_BUCKET}"}
    ]

    assert len(listable) == 1
    assert listable[0]["Condition"] == {"StringLike": {"s3:prefix": ["intent/*", "result/*"]}}


def test_a_result_sync_that_is_refused_leaves_no_half_read_tree(
    workflow: dict[str, Any],
) -> None:
    """Mutation: drop the `rm -rf`, or soften the sync to `|| true`.

    The nightly reader role does not hold `result/` until the stack is applied from a laptop,
    so a denial is the expected answer for now and the job has to survive it. What it must
    not do is carry on with a partial tree. A run whose result did not sync reads as one that
    never finished, and a report that stopped asking about the runs that did would be the
    silent failure this whole check exists to find, pointed at itself.
    """
    fetch = step(workflow["jobs"][RECONCILE_JOB], "Fetch the intent records")["run"]

    assert "rm -rf lineage/result" in fetch, (
        "a refused result sync leaves a partial tree behind, which reads as runs that never "
        "finished and quietly narrows what the report asks about"
    )
    assert "|| true" not in fetch


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


def release_step(workflow: dict[str, Any]) -> str:
    return step(workflow["jobs"][RELEASE_JOB], RELEASE_STEP)["run"]


def test_the_nightly_compares_the_deployed_functions_against_the_records(
    workflow: dict[str, Any],
) -> None:
    """Mutation: keep the job and drop the step, or point it at a builder instead.

    The two release tripwires compare a record against a zip built from the tree and run on
    every pull request. This job is the only thing anywhere that reads the account, so a job
    that installs the tooling, takes a credential and runs nothing leaves the chain exactly
    as open as it was before the job existed.
    """
    body = scripts(workflow, RELEASE_JOB)

    assert RELEASE_TOOL in body
    assert "uv run --frozen python" in body


def test_a_deployed_function_that_is_not_the_released_one_fails_the_nightly(
    workflow: dict[str, Any], tmp_path: Path
) -> None:
    """THE ONE THAT MATTERS. Mutation: report the difference and exit zero.

    The tool exits 1 when the digest AWS reports is not the digest the release record
    carries, and there is no alerting on this platform, so the red run is the whole signal.
    """
    stub = write_stub(tmp_path / "bin", "uv", "exit 1")

    finished = run_step_script(release_step(workflow), cwd=tmp_path, env={}, stub_bin=stub.parent)

    assert finished.returncode != 0


def test_a_release_check_that_could_not_look_also_fails_the_nightly(
    workflow: dict[str, Any], tmp_path: Path
) -> None:
    """Mutation: pass on exit 2, on the ground that it found nothing wrong.

    It found nothing at all. A denied `lambda:GetFunctionConfiguration` is the likeliest way
    this ever exits 2, and treating that as a clean run would retire the check on the
    morning the grant lapsed without anybody deciding to.
    """
    stub = write_stub(tmp_path / "bin", "uv", "exit 2")

    finished = run_step_script(release_step(workflow), cwd=tmp_path, env={}, stub_bin=stub.parent)

    assert finished.returncode != 0


def test_a_deployment_that_matches_its_record_passes(
    workflow: dict[str, Any], tmp_path: Path
) -> None:
    """The other side, so the step cannot pass this file by failing unconditionally."""
    stub = write_stub(tmp_path / "bin", "uv", "exit 0")

    finished = run_step_script(release_step(workflow), cwd=tmp_path, env={}, stub_bin=stub.parent)

    assert finished.returncode == 0, finished.stderr


def test_the_release_step_restates_nothing_the_tool_already_says(
    workflow: dict[str, Any],
) -> None:
    """Mutation: wrap it in the `status=$?` shape the two jobs above use, to be consistent.

    Those two wrap tools that report a finding without a machine-readable reason, so the
    step supplies one. This tool prints its own reason, prints a sentence naming what to do,
    and separates a disagreement from an unanswered question in its exit code. A translation
    on top would be a second spelling of all three, and the two would drift.
    """
    body = release_step(workflow)

    assert body.strip() == f"uv run --frozen python {RELEASE_TOOL}"


def test_the_release_check_never_prints_an_account_id(workflow: dict[str, Any]) -> None:
    """Mutation: print the CLI's stderr when the call is refused.

    An AccessDenied from Lambda names the calling role's ARN and the resource ARN, and both
    carry the account id. This job writes to a scheduled log and a step summary in a public
    repository, and every committed capture here masks that number. Asserted beside the job
    as well as in the tool's own tests, for the same reason the W&B one is: the log this job
    writes is what makes it matter.
    """
    assert RELEASE_TOOL in scripts(workflow, RELEASE_JOB)
    source = (PROJECT_ROOT / RELEASE_TOOL).read_text(encoding="utf-8")

    assert "carries the account id" in source
    assert not ACCOUNT_LITERAL.search(source)


def stacks_step(workflow: dict[str, Any]) -> str:
    return step(workflow["jobs"][STACKS_JOB], STACKS_STEP)["run"]


def test_the_nightly_compares_each_deployed_stack_against_its_template(
    workflow: dict[str, Any],
) -> None:
    """Mutation: keep the job and drop the step, or point it at a template validator.

    Validating the templates is what CI already does, and it is the half that was never in
    question. The account is the half nothing read: fourteen of these stacks create roles and
    every one of them is applied by hand, so a job that installs the tooling, takes a
    credential and runs nothing leaves the account exactly as unwatched as before.
    """
    body = scripts(workflow, STACKS_JOB)

    assert STACKS_TOOL in body
    assert "uv run --frozen python" in body


def test_a_stack_that_is_not_the_template_on_main_fails_the_nightly(
    workflow: dict[str, Any], tmp_path: Path
) -> None:
    """THE ONE THAT MATTERS. Mutation: report the difference and exit zero.

    The tool exits 1 when a deployed template is not the one main declares, when a stack it
    declares is not deployed, and when the account holds a stack nothing declares. There is
    no alerting on this platform, so the red run is the whole signal.
    """
    stub = write_stub(tmp_path / "bin", "uv", "exit 1")

    finished = run_step_script(stacks_step(workflow), cwd=tmp_path, env={}, stub_bin=stub.parent)

    assert finished.returncode != 0


def test_a_stack_check_that_could_not_look_also_fails_the_nightly(
    workflow: dict[str, Any], tmp_path: Path
) -> None:
    """Mutation: pass on exit 2, on the ground that it found nothing wrong.

    It found nothing at all. A denied cloudformation:ListStacks is the likeliest way this
    ever exits 2, and it is the denial that matters most: the listing is what stops the check
    being confined to the stacks it already knows about, so treating it as a clean run would
    retire the whole property without anybody deciding to.
    """
    stub = write_stub(tmp_path / "bin", "uv", "exit 2")

    finished = run_step_script(stacks_step(workflow), cwd=tmp_path, env={}, stub_bin=stub.parent)

    assert finished.returncode != 0


def test_an_account_that_matches_main_passes(workflow: dict[str, Any], tmp_path: Path) -> None:
    """The other side, so the step cannot pass this file by failing unconditionally."""
    stub = write_stub(tmp_path / "bin", "uv", "exit 0")

    finished = run_step_script(stacks_step(workflow), cwd=tmp_path, env={}, stub_bin=stub.parent)

    assert finished.returncode == 0, finished.stderr


def test_the_stack_step_restates_nothing_the_tool_already_says(
    workflow: dict[str, Any],
) -> None:
    """Same argument as the release step, and the same shape.

    The tool prints a machine-readable reason, a sentence naming what to do, and the paths
    inside the template that differ. A translation on top would be a second spelling of all
    of it, and the two would drift.
    """
    assert stacks_step(workflow).strip() == f"uv run --frozen python {STACKS_TOOL}"


def test_the_stack_check_never_prints_an_account_id(workflow: dict[str, Any]) -> None:
    """Mutation: print the CLI's stderr when a call is refused.

    A CloudFormation denial names the calling role's ARN and the stack ARN, and both carry
    the account id. This job also prints template content, which is the account's rather than
    this repository's, so the tool masks what it renders as well.
    """
    assert STACKS_TOOL in scripts(workflow, STACKS_JOB)
    source = (PROJECT_ROOT / STACKS_TOOL).read_text(encoding="utf-8")

    assert "carries the account id" in source
    assert not ACCOUNT_LITERAL.search(source)


def board_step(workflow: dict[str, Any]) -> str:
    return step(workflow["jobs"][BOARD_JOB], BOARD_STEP)["run"]


def test_the_nightly_joins_the_three_records_of_a_run(workflow: dict[str, Any]) -> None:
    """Mutation: keep the job and drop the step, or point it at one of the sources alone.

    Each of the three systems already has something that reads it. What nothing did was join
    them, and the join is the whole check: a run present in W&B and absent from the bucket is
    invisible to every tool that reads one system, because each of them is looking at a
    complete and correct account of its own third.
    """
    body = scripts(workflow, BOARD_JOB)

    assert BOARD_TOOL in body
    assert "--output" in body, "the board is captured so it can be read after the failure"
    assert "uv run --frozen python" in body


def run_board(workflow: dict[str, Any], tmp_path: Path, *, exit_code: int) -> Any:
    summary = tmp_path / "summary.md"
    summary.touch()
    stub = stub_tool(tmp_path, exit_code=exit_code, report="# The visibility board")
    return run_step_script(
        board_step(workflow),
        cwd=tmp_path,
        env={"GITHUB_STEP_SUMMARY": str(summary)},
        stub_bin=stub.parent,
    )


def test_a_run_only_one_of_the_three_sources_knows_about_fails_the_nightly(
    workflow: dict[str, Any], tmp_path: Path
) -> None:
    """THE ONE THAT MATTERS. Mutation: print the board and exit zero.

    There is no alerting on this platform, so the red cross is the entire signal, and a job
    that writes three tables into the step summary and then succeeds is a job nobody opens.
    The board exits 1 when a run is in one source and not the others, and this asserts the
    step carries that through.
    """
    finished = run_board(workflow, tmp_path, exit_code=1)

    assert finished.returncode != 0, finished.stdout
    assert "the_three_records_of_a_run_disagree" in finished.stderr


def test_a_board_that_could_not_read_a_source_is_not_read_as_a_clean_one(
    workflow: dict[str, Any], tmp_path: Path
) -> None:
    """Mutation: treat any non-zero exit as the same finding.

    The board exits 2 when a source was not read, which is the state it is in today: the
    reader role holds no tagging action, so the account side is a named gap rather than an
    answer. Somebody who reads that as a disagreement goes looking for a submitter who did
    nothing, on a morning whose only finding is an IAM stack nobody has applied.
    """
    finished = run_board(workflow, tmp_path, exit_code=2)

    assert finished.returncode != 0
    assert "visibility_board_incomplete" in finished.stderr
    assert "the_three_records_of_a_run_disagree" not in finished.stderr


def test_three_sources_that_agree_pass(workflow: dict[str, Any], tmp_path: Path) -> None:
    """The other side, so the step cannot pass this file by failing unconditionally."""
    finished = run_board(workflow, tmp_path, exit_code=0)

    assert finished.returncode == 0, finished.stderr


def test_the_board_reaches_the_log_and_not_only_the_step_summary(
    workflow: dict[str, Any], tmp_path: Path
) -> None:
    """Mutation: write the tables to the step summary alone.

    Same argument the reconciliation is held to. The summary is a second page to open, and
    the person triaging a red cross is already looking at the log.
    """
    summary = tmp_path / "summary.md"
    summary.touch()
    stub = stub_tool(tmp_path, exit_code=1, report="run_019fbe0c logged nowhere")

    finished = run_step_script(
        board_step(workflow),
        cwd=tmp_path,
        env={"GITHUB_STEP_SUMMARY": str(summary)},
        stub_bin=stub.parent,
    )

    assert "run_019fbe0c logged nowhere" in finished.stdout
    assert "run_019fbe0c logged nowhere" in summary.read_text(encoding="utf-8")


def test_the_board_never_prints_an_account_id(workflow: dict[str, Any]) -> None:
    """Mutation: render the resource ARN the tagging API hands back.

    Every ARN carries the account id, and so does the ARN in any denial the CLI reports. This
    job writes to a scheduled log and a step summary in a public repository, and every
    committed capture here masks that number. Asserted beside the job as well as in the tool's
    own tests, for the reason the two sibling jobs are: the log this job writes is what makes
    it matter.
    """
    assert BOARD_TOOL in scripts(workflow, BOARD_JOB)
    source = (PROJECT_ROOT / BOARD_TOOL).read_text(encoding="utf-8")

    assert "carries the account id" in source
    assert not ACCOUNT_LITERAL.search(source)


@pytest.mark.parametrize(
    ("job_id", "step_name"),
    [
        (RECONCILE_JOB, RECONCILE_STEP),
        (WANDB_JOB, WANDB_STEP),
        (RELEASE_JOB, RELEASE_STEP),
        (STACKS_JOB, STACKS_STEP),
        (BOARD_JOB, BOARD_STEP),
    ],
)
def test_nothing_in_any_of_these_jobs_is_allowed_to_be_informational(
    workflow: dict[str, Any], job_id: str, step_name: str
) -> None:
    """Mutation: `continue-on-error: true` on the job or on the reporting step.

    It is the obvious response to a job that goes red on the first morning, and it turns the
    job into one that cannot say anything. The header of the workflow argues the point for
    the three checks that were already there, and these are the fourth, fifth and sixth.
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


@pytest.mark.parametrize("job_id", CREDENTIALED_JOBS)
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


@pytest.mark.parametrize("job_id", CREDENTIALED_JOBS)
def test_every_job_that_reads_the_account_assumes_the_reader_role_and_no_other(
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
        "lambda:GetFunctionConfiguration",
        "cloudformation:GetTemplate",
        "cloudformation:ListStacks",
    }
    for action in granted:
        assert not any(fragment in action for fragment in MUTATING_ACTION_FRAGMENTS), action
        assert action.startswith(("s3:", "secretsmanager:", "lambda:", "cloudformation:")), action
        assert "*" not in action, action

    # The sharpest instance of the rule this test is for. A release check that could deploy
    # could answer its own finding by making the account match the record, which is the
    # wrong direction: the record is the reviewed artifact and the deployment is the thing
    # under suspicion.
    assert "lambda:UpdateFunctionCode" not in granted
    # GetFunction answers with a presigned URL to the deployed artifact. A digest is a
    # description of the code and a link is a copy of it, and only one of those is a read
    # this job needs.
    assert "lambda:GetFunction" not in granted
    assert "lambda:ListFunctions" not in granted

    # ListSecrets has no resource type, so a grant of it could not be scoped to one secret
    # and would let a scheduled job enumerate every secret in the account.
    assert "secretsmanager:ListSecrets" not in granted

    # The same rule again, on the widest grant in this policy. A drift check that could
    # apply a template could answer its own finding by reconciling the account, and
    # reconciling is exactly the operation that took the delete grant back on 2026-08-01.
    # Fourteen of these stacks create roles, so it would also be a role-creation path behind
    # a scheduled workflow, which the first section of infra/README.md is entirely about.
    for forbidden in (
        "cloudformation:CreateStack",
        "cloudformation:UpdateStack",
        "cloudformation:DeleteStack",
        "cloudformation:CreateChangeSet",
        "cloudformation:ExecuteChangeSet",
    ):
        assert forbidden not in granted

    assert all(statement["Effect"] == "Allow" for statement in statements(role))


def test_no_grant_reaches_a_whole_bucket_or_every_secret(role: dict[str, Any]) -> None:
    """Mutation: widen a resource to the bucket, or to `*`, to stop chasing an access denial.

    This is a shared sandbox account with sixteen other teams in it. A read grant is not
    harmless here: the lineage store holds every run's records and Secrets Manager holds
    everybody's credentials, so an unscoped read is an exfiltration path with a schedule.

    ONE STATEMENT IS EXEMPT AND IT IS NAMED RATHER THAN PATTERN-MATCHED, so a second one
    cannot arrive by widening a resource to `*` and calling it precedent.
    `cloudformation:ListStacks` has no resource type -- the request names no stack, so a
    policy naming one denies the call -- and it is the action that stops the drift check
    being confined to the stacks it already knows about. What it discloses is stack names,
    statuses and timestamps, and no template, parameter or output.
    """
    unscopable = {
        statement["Sid"]
        for statement in statements(role)
        if statement["Action"] == "cloudformation:ListStacks"
    }
    assert unscopable == {"FindStacksNothingInTheRepositoryAccountsFor"}

    resources = [
        rendered
        for statement in statements(role)
        if statement["Sid"] not in unscopable
        for rendered in statement_resources(statement)
    ]

    assert resources, "the policy grants nothing at all"
    for rendered in resources:
        assert rendered != "*", "a wildcard resource is not a scoped grant"
        assert "sbsandbox-intern-edullm-" in rendered, rendered

    objects = [
        reachable
        for statement in statements(role)
        for reachable in statement_resources(statement)
        if "s3:GetObject" in str(statement["Action"])
    ]
    assert all(reachable.count("/") >= 1 for reachable in objects), (
        "an object grant whose ARN stops at the bucket name reaches every key in it"
    )


def test_the_unscopable_listing_is_confined_to_the_region_this_platform_deploys_in(
    role: dict[str, Any],
) -> None:
    """Mutation: drop the condition, since the action cannot be scoped anyway.

    It is the only narrowing the action admits and it is a small one, which is a reason to
    write down what it buys rather than a reason to leave it off. The region lock permits
    us-east-1 and us-east-2 and everything this platform has is in the first, so a listing of
    the second is a read nothing here asks for.
    """
    listing = [
        statement
        for statement in statements(role)
        if statement["Action"] == "cloudformation:ListStacks"
    ]

    assert len(listing) == 1
    assert listing[0]["Condition"] == {
        "StringEquals": {"aws:RequestedRegion": {"Fn::Sub": "${AWS::Region}"}}
    }


def test_the_template_grant_names_the_stacks_the_drift_check_compares() -> None:
    """Mutation: widen the resource to `stack/sbsandbox-intern-edullm-*`, which reads scoped.

    A template is the whole configuration of a stack, so this is the widest read in the
    policy. A prefix would extend it silently to whatever a later phase deploys under a
    matching name, and the point of the check is that a new stack is noticed rather than
    absorbed -- an unnamed stack is reported as unaccounted for, and a prefix grant would
    quietly make it comparable instead.

    The expected names come out of the tool's own table rather than being written here, so a
    stack added to one and not the other fails at review instead of as a denial at 05:00.
    """
    tool = PROJECT_ROOT / "tools" / "verify_deployed_stacks.py"
    specification = importlib.util.spec_from_file_location("verify_deployed_stacks", tool)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)

    role_properties = next(iter(iam_roles(load_template(ROLE_PATH))))
    granted = {
        reachable.rsplit(":stack/", 1)[1].removesuffix("/*")
        for statement in statements(role_properties)
        if statement["Action"] == "cloudformation:GetTemplate"
        for reachable in statement_resources(statement)
    }

    assert granted == set(module.STACKS)


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
        reachable: set(statement_actions(statement))
        for statement in statements(role)
        for reachable in statement_resources(statement)
        if ":s3:::" in reachable
    }

    assert reach == {
        f"arn:${{AWS::Partition}}:s3:::{LINEAGE_BUCKET}": {
            "s3:ListBucket",
            "s3:GetBucketLocation",
        },
        f"arn:${{AWS::Partition}}:s3:::{LINEAGE_BUCKET}/intent/*": {"s3:GetObject"},
        f"arn:${{AWS::Partition}}:s3:::{LINEAGE_BUCKET}/result/*": {"s3:GetObject"},
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
        statement_resources(statement)[0].rsplit(":::", 1)[1]: statement["Condition"][
            "StringLike"
        ]["s3:prefix"]
        for statement in statements(role)
        if "s3:ListBucket" in str(statement["Action"])
    }

    assert conditions == {
        LINEAGE_BUCKET: ["intent/*", "result/*"],
        OUTPUTS_BUCKET: "teams/*/runs/*",
    }


def test_the_lambda_grant_names_the_functions_the_templates_declare(
    role: dict[str, Any],
) -> None:
    """Mutation: widen the resource to `function:*`, or to the project's own name prefix.

    Either still reads as scoped and neither is. This is a shared sandbox account with
    sixteen other teams in it, and a function's configuration carries its environment
    variable names, its role and its layers, so an unscoped describe is a nightly
    reconnaissance of everybody else's infrastructure to answer a question about two
    functions.

    The expected names are read out of the templates that declare them to CloudFormation
    rather than written here, because the name lives in three places once this grant exists
    -- the template, the policy, and whatever the account was left holding -- and a rename
    that updates the template alone is an access denial at 05:00 rather than a red review.
    """
    declared = set()
    for name in LAMBDA_TEMPLATES:
        template = load_template(INFRA_ROOT / name)
        _, function = resource_of_type(template, "AWS::Lambda::Function")
        declared.add(function["Properties"]["FunctionName"])

    reach = {
        reachable.rsplit(":function:", 1)[1]: set(statement_actions(statement))
        for statement in statements(role)
        for reachable in statement_resources(statement)
        if ":lambda:" in reachable
    }

    assert set(reach) == declared
    assert all(actions == {"lambda:GetFunctionConfiguration"} for actions in reach.values())
    # Unqualified, with no trailing `:*`. The check asks about $LATEST, which is what the
    # state machine and the events rule invoke; a version or alias suffix would be a grant
    # for something nothing here reads.
    assert all(not qualified.endswith("*") for qualified in reach)


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
        if any(":secretsmanager:" in reachable for reachable in statement_resources(statement))
    ]

    assert len(secrets) == 1
    resource = statement_resources(secrets[0])[0]
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
