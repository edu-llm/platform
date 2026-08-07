"""The audit's checks that read the account, and the role that lets them.

**Each one exists because the failure it describes is silent.** A training run whose save
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
within a workflow: every job in ``audit.yml`` presents the same claims and any of them can
assume this role, including one added later.
"""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from datetime import UTC, datetime
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
from edullm_platform.wandb_preflight import (
    AUDIT_VERDICT_ARTIFACT,
    AUDIT_VERDICT_FILENAME,
    VERDICT_FIELD,
    Verdict,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = WORKFLOWS_ROOT / "audit.yml"
ROLE_PATH = IAM_ROOT / "audit-reader-role.yaml"

RECONCILE_JOB = "checkpoint-reconciliation"
WANDB_JOB = "wandb-credential"
RELEASE_JOB = "deployed-lambda-release"
STACKS_JOB = "deployed-stack-templates"
BOARD_JOB = "visibility-board"
PLACEMENT_JOB = "placement-verdicts"
CAPTURE_JOB = "substrate-capture"
HISTORY_JOB = "substrate-history"
ACTIVITY_JOB = "activity-history"
ASKS_JOB = "open-asks"
ROSTER_JOB = "roster-against-the-account"
#: Found by the sweep below on the morning a tenth job was added, which is what the sweep is
#: for. It was the first job in the file until the gate check went in front of it.
DOCKERFILES_JOB = "registered-dockerfiles"
LEAD_GATE_JOB = "lead-gate"
#: The one job here whose subject is a GitHub setting rather than a file or the account. Its
#: neighbour above is the other half of the same question and the two do not overlap:
#: `lead-gate` asks who is behind the reviewer slot, from a capture, because that cannot be
#: read live; this one asks whether the slot is still there at all, and reads it live.
GATE_JOB = "the-gate-still-exists"

RECONCILE_TOOL = "tools/find_runs_that_saved_nothing.py"
WANDB_TOOL = "tools/verify_wandb_credential.py"
RELEASE_TOOL = "tools/verify_deployed_lambdas.py"
STACKS_TOOL = "tools/verify_deployed_stacks.py"
BOARD_TOOL = "tools/visibility_board.py"
PLACEMENT_TOOL = "tools/verify_placement_verdicts.py"
CAPTURE_TOOL = "tools/read_substrate.py"
ACTIVITY_TOOL = "tools/report_activity.py"
ASKS_TOOL = "tools/report_asks.py"
LEAD_GATE_TOOL = "tools/report_who_can_open_the_lead_gate.py"
GATE_TOOL = "tools/verify_the_gate.py"

RECONCILE_STEP = "Reconcile what those runs actually wrote"
WANDB_STEP = "Ask W&B whether it would accept the stored key"
WANDB_UPLOAD_STEP = "Publish what W&B said, for the submission preflight to read"
RELEASE_STEP = "Compare what AWS is running against what was released"
STACKS_STEP = "Compare each deployed stack against the template main declares"
BOARD_STEP = "Join what W&B, the account and the outputs bucket each say"
PLACEMENT_STEP = "Recompute each placement verdict from the sixteen queues"
CAPTURE_STEP = "Read the account and write today down"
CAPTURE_UPLOAD_STEP = "Publish the reading"
HISTORY_STEP = "Keep the reading on the machine/substrate branch"
ACTIVITY_STEP = "Write the day as a page"
ACTIVITY_UPLOAD_STEP = "Publish the page"
ACTIVITY_HISTORY_STEP = "Keep the page on the machine/activity branch"
ASKS_STEP = "Count the open asks"
DOCKERFILES_STEP = "Check every registration against the repository it names"
LEAD_GATE_STEP = "Compare the captured team against the roster, and say how old the capture is"
GATE_STEP = "Check the approval environments against what this repository declares"
GUARD_STEP = "Check the audit reader role is deployed"

#: Where a reading is kept, and what it is called there. Both are spelled here because the
#: workflow writes the branch by name and nothing at runtime would notice a rename: a push to
#: a branch nobody reads succeeds exactly as a push to the right one does.
HISTORY_BRANCH = "machine/substrate"
CAPTURE_ARTIFACT = "substrate"
ACTIVITY_BRANCH = "machine/activity"
ACTIVITY_ARTIFACT = "activity"

#: Every job here that takes a credential. Each one is held to the same guard, the same
#: role and the same refusal to be informational, so the list is what a seventh such job has
#: to join rather than a set of tests it has to remember to be added to.
CREDENTIALED_JOBS = (
    RECONCILE_JOB,
    WANDB_JOB,
    RELEASE_JOB,
    STACKS_JOB,
    BOARD_JOB,
    PLACEMENT_JOB,
    CAPTURE_JOB,
    ROSTER_JOB,
)

#: The functions the release check reads, and the templates that name them to
#: CloudFormation. Read from the templates rather than spelled here, because the IAM grant
#: is written against these names and a rename that missed one of the places would be an
#: access denial at 05:00 rather than a failure at review.
LAMBDA_TEMPLATES = (
    "admission-state-machine.yaml",
    "batch-events.yaml",
    "expiry-janitor.yaml",
    "notifications.yaml",
)

#: What the repository variable is called, and what the role it names is called. Spelled here
#: as literals because the workflow reads the variable by name and a rename on one side alone
#: resolves to the empty string, which is the failure the guard step in each job exists to
#: name, and because the variable is set by hand in the repository settings and so cannot be
#: found by a grep of the tree.
ROLE_VARIABLE = "AWS_AUDIT_READER_ROLE_ARN"
ROLE_NAME = "sbsandbox-intern-edullm-audit-reader"
WORKFLOW_REF = "edu-llm/platform/.github/workflows/audit.yml@refs/heads/main"

#: The secret name is spelled out because the grant is scoped to it by name, and a rename on
#: either side alone is an access denial at 05:00 rather than a test failure at review time.
WANDB_SECRET_NAME = "sbsandbox-intern-edullm-wandb-api-key"

#: A plausible ARN for the guard to accept, on the documentation account id every other test
#: here uses. A run of twelve digits that is not that one reads as a real account to
#: `tests/test_evidence.py`, which scans the tracked tree and does not care that this file is
#: a test.
SOME_ROLE_ARN = "arn:aws:iam::123456789012:role/sbsandbox-intern-edullm-audit-reader"

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


def load_tool(name: str) -> Any:
    """Import a module out of ``tools/``, which is not a package and not on the path.

    RETURNS THE MODULE ALREADY IMPORTED, IF THERE IS ONE, AND THAT IS THE WHOLE POINT.
    Loading a second time builds a second module object and binding it into ``sys.modules``
    leaves two live copies of the same file, which is silent until something patches one and
    calls the other. ``tests/test_visibility_board.py`` puts ``tools/`` on the path and does
    ``from visibility_board import read_tagged_resources`` at import, so its functions close
    over the first copy's globals; its tests then ``import visibility_board`` inside the test
    body, which reads ``sys.modules`` and hands back whichever copy was bound last. When this
    file happened to run first, ``monkeypatch.setattr(visibility_board, "aws", ...)`` patched
    a copy nothing called, the stub did not take, and five board tests shelled out to the
    real `aws` -- failing with `NoCredentials` on a runner and passing alone on a laptop.
    That is an ordering-dependent red that says nothing about the change under review.

    The entry is still registered when this function is the one doing the loading, because
    ``@dataclass`` reads ``sys.modules[cls.__module__]`` while the class body is executing and
    raises `AttributeError` on `None` without it.
    """
    cached = sys.modules.get(name)
    if cached is not None:
        return cached
    tool = PROJECT_ROOT / "tools" / f"{name}.py"
    specification = importlib.util.spec_from_file_location(name, tool)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


# ----------------------------------------------------------------------------------------
# Which lineage prefixes the scheduled tools read, and which ones the role can reach
#
# THE THREE FUNCTIONS BELOW EXIST BECAUSE THIS FACT WAS WRITTEN DOWN TWICE AND COMPARED
# NOWHERE. `tools/report_run_costs.py` declared LINEAGE_PREFIXES = ("intent", "attempt") and
# `infra/iam/audit-reader-role.yaml` granted intent/ and result/, and the visibility board
# -- the one caller of `sync_bucket` that runs on the schedule, under that role -- was
# refused on attempt/ every night from the moment it shipped. Deriving both sides is what
# makes the disagreement a red review instead of a line in a step summary nobody reads.
# ----------------------------------------------------------------------------------------


def reconciliation_prefixes(workflow: dict[str, Any]) -> set[str]:
    """The lineage prefixes the checkpoint reconciliation's own step syncs."""
    fetch = step(workflow["jobs"][RECONCILE_JOB], "Fetch the intent records")
    assert fetch["env"]["LINEAGE_BUCKET"] == LINEAGE_BUCKET
    return {
        line.split("s3://${LINEAGE_BUCKET}/")[1].split()[0].strip('"').rstrip("/")
        for line in fetch["run"].splitlines()
        if "s3://${LINEAGE_BUCKET}/" in line
    }


def synced_lineage_prefixes(workflow: dict[str, Any]) -> set[str]:
    """Every lineage prefix something running under this role must be able to read.

    The union rather than either one. The reconciliation joins intent to result and the
    board joins intent to attempt to result, so the grant has to cover three prefixes and
    neither reader on its own says which three.

    READ OFF THE BOARD RATHER THAN OFF THE COST REPORT, WHICH IS A WIDENING OF THIS CHECK
    AND NOT A RESTATEMENT OF IT. It read ``report_run_costs.LINEAGE_PREFIXES``, which is the
    set that report needs, and the board is the thing that actually runs under this role --
    so a prefix the board syncs and the cost report does not was invisible here. The board's
    own constant is the honest source, and it is the union of the cost report's two with
    whatever else the board cannot run without.
    """
    board = set(load_tool("visibility_board").REQUIRED_LINEAGE_PREFIXES)
    assert board, "the board's required prefix set is empty, so this check compares nothing"
    assert set(load_tool("report_run_costs").LINEAGE_PREFIXES) <= board, (
        "the board syncs the cost report's prefixes through the same call, so its required "
        "set has to contain them"
    )
    reconciliation = reconciliation_prefixes(workflow)
    assert reconciliation, "the reconciliation step syncs nothing, so this check compares nothing"
    return board | reconciliation


def degrading_lineage_prefixes() -> set[str]:
    """Prefixes a scheduled reader asks for and survives being refused.

    ``binding/`` is the second account-side source and the role does not hold it yet, so the
    board reports a narrower horizon and carries on. It is separated from the required set
    because the two need opposite assertions: a required prefix that is not granted is a
    denial at 05:00, and a degrading one that *is* granted is somebody having applied the
    statement the board prints, which must not fail a check written before they did.
    """
    degrading = set(load_tool("visibility_board").DEGRADING_LINEAGE_PREFIXES)
    assert degrading, "the board declares no degrading prefix, so this check compares nothing"
    return degrading


def fetchable_lineage_prefixes(role: dict[str, Any]) -> set[str]:
    """The prefixes the role holds ``s3:GetObject`` on, read off the object ARNs."""
    marker = f":s3:::{LINEAGE_BUCKET}/"
    return {
        reachable.split(marker, 1)[1].rsplit("/*", 1)[0]
        for statement in statements(role)
        if "s3:GetObject" in statement_actions(statement)
        for reachable in statement_resources(statement)
        if marker in reachable
    }


def listable_lineage_prefixes(role: dict[str, Any]) -> set[str]:
    """The prefixes the role may enumerate, read off the ``s3:prefix`` condition.

    A separate function from the one above rather than a second return value, because the
    whole hazard is that these two are written in different places and can disagree.
    """
    bucket = f"arn:${{AWS::Partition}}:s3:::{LINEAGE_BUCKET}"
    values: set[str] = set()
    for statement in statements(role):
        if "s3:ListBucket" not in statement_actions(statement):
            continue
        if statement_resources(statement) != [bucket]:
            continue
        condition = statement["Condition"]["StringLike"]["s3:prefix"]
        entries = condition if isinstance(condition, list) else [condition]
        values |= {entry.rsplit("/*", 1)[0] for entry in entries}
    return values


# ----------------------------------------------------------------------------------------
# The checks are invoked at all
# ----------------------------------------------------------------------------------------


def test_the_audit_runs_the_checkpoint_reconciliation(workflow: dict[str, Any]) -> None:
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


def test_the_audit_runs_the_wandb_credential_check(workflow: dict[str, Any]) -> None:
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

    THE LISTING SIDE IS NO LONGER PINNED TO THIS JOB'S TWO PREFIXES. It was, and that is how
    the board's `attempt/` grant stayed missing: an equality against `intent/*, result/*`
    reads as strict and silently forbids a second reader from being granted what it needs.
    What this job requires is that everything it syncs is listable, which is what is asserted
    here; that the grant covers every scheduled reader and nothing beyond them is
    `test_the_role_can_list_and_fetch_every_prefix_the_scheduled_tools_sync`.

    `attempt/` JOINED THE THREE BECAUSE `result/` COULD NOT ANSWER WHAT IT WAS BEING ASKED.
    One result record is written per run *key* and one terminal event arrives per *cell*, so
    every child of an array overwrites the same object and a fan-out's record is whichever
    cell landed last. Scoping the question on that meant scoping it by arrival order on
    nineteen runs. The attempt records are one per cell and complete, `result/` still answers
    for the runs Batch never placed, and `edullm_platform.cells.outcome_of_cells` is the one
    function this report and the substrate now put both through.
    """
    synced = reconciliation_prefixes(workflow)

    assert synced == {"intent", "attempt", "result"}

    listable = [
        statement
        for statement in statements(role)
        if statement["Resource"] == {"Fn::Sub": f"arn:${{AWS::Partition}}:s3:::{LINEAGE_BUCKET}"}
    ]

    assert len(listable) == 1
    assert synced <= listable_lineage_prefixes(role)


def test_a_result_sync_that_is_refused_leaves_no_half_read_tree(
    workflow: dict[str, Any],
) -> None:
    """Mutation: drop the `rm -rf`, or soften the sync to `|| true`.

    The audit reader role holds `result/` and has since the stack was applied, so a denial
    here is a lapsed credential or a drifted role rather than the ordinary case -- and the
    job still has to survive it, because a denial that has become unexpected is not a denial
    that has become impossible. What it must not do is carry on with a partial tree. A run
    whose result did not sync reads as one that never finished, and a report that stopped
    asking about the runs that did would be the silent failure this whole check exists to
    find, pointed at itself.
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


def load_wandb_tool() -> Any:
    """The verifier as a module, so its vocabulary can be compared rather than grepped."""
    specification = importlib.util.spec_from_file_location(
        "_audit_wandb_tool", PROJECT_ROOT / WANDB_TOOL
    )
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


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


def test_a_run_with_no_loadable_checkpoint_fails_the_audit(
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


def run_wandb_check(workflow: dict[str, Any], tmp_path: Path, *, exit_code: int, report: str) -> Any:
    stub = stub_tool(tmp_path, exit_code=exit_code, report=report)
    return run_step_script(
        wandb_step(workflow),
        cwd=tmp_path,
        env={"RUNNER_TEMP": str(tmp_path)},
        stub_bin=stub.parent,
    )


def test_a_key_wandb_would_refuse_fails_the_audit(
    workflow: dict[str, Any], tmp_path: Path
) -> None:
    """Mutation: report the faults and exit zero.

    The check exists because nothing else goes red when W&B declines a key. A job that prints
    `looks_wrong` and succeeds reproduces exactly the condition it was written to end.
    """
    finished = run_wandb_check(
        workflow,
        tmp_path,
        exit_code=1,
        report='{"looks_wrong": ["prefixed with api"], "verdict": "refused"}',
    )

    assert finished.returncode != 0
    assert "wandb_credential_would_be_refused" in finished.stderr
    # And it says what the refusal now costs, because it costs more than it used to. The
    # submission preflight reads this verdict, so a red run here refuses every dispatch
    # until a later run says otherwise -- and the person repairing the key has to know that
    # dispatching this workflow is what clears it.
    assert "dispatch this workflow again" in finished.stderr


def test_a_key_that_names_a_person_reddens_the_audit_and_claims_no_refusal(
    workflow: dict[str, Any], tmp_path: Path
) -> None:
    """Mutation: branch on the exit code alone, which is what this step used to do.

    Two faults exit 1 now and only one of them closes the submission gate. A key W&B
    accepts that belongs to a person logs every run under that person's name and stops
    nothing, so telling the reader "submissions are refused while this stands" would send
    them to repair a gate that is open, on the one night they are reading at 05:00.
    """
    finished = run_wandb_check(
        workflow,
        tmp_path,
        exit_code=1,
        report=(
            '{"looks_wrong": [], "verdict": "accepted", '
            '"attribution_looks_wrong": ["W&B resolves this key to the user \'philote\'"]}'
        ),
    )

    assert finished.returncode != 0
    assert "wandb_key_attributes_to_a_person" in finished.stderr
    assert "wandb_credential_would_be_refused" not in finished.stderr
    assert "Submissions are refused" not in finished.stderr
    # And it still says what to do, because the repair is the same kind of thing: a person
    # writing the secret from a laptop, then dispatching this workflow.
    assert "dispatch this workflow again" in finished.stderr


def test_a_key_wandb_accepts_passes(workflow: dict[str, Any], tmp_path: Path) -> None:
    finished = run_wandb_check(
        workflow, tmp_path, exit_code=0, report='{"looks_wrong": [], "verdict": "accepted"}'
    )

    assert finished.returncode == 0, finished.stderr


def test_the_verdict_is_written_where_the_submission_preflight_can_read_it(
    workflow: dict[str, Any], tmp_path: Path
) -> None:
    """Mutation: print the report and upload nothing, which is what this job used to do.

    The verdict has a second reader now. ``submit-run.yml`` refuses a submission on the
    strength of it, because no identity that workflow can obtain holds
    ``secretsmanager:GetSecretValue`` on this secret and
    ``infra/iam/admission-role.yaml`` argues that none should. So the answer has to leave
    this job as a document rather than only as log output: a step summary is exposed by no
    REST endpoint, and a job log is a zip somebody has to parse.

    The upload is ``if: always()`` and that is the whole point rather than caution. The step
    above exits 1 on exactly the answer worth publishing, so an upload conditioned on
    success would publish every verdict except the refusal.
    """
    upload = step(workflow["jobs"][WANDB_JOB], WANDB_UPLOAD_STEP)
    names = [candidate.get("name") for candidate in workflow["jobs"][WANDB_JOB]["steps"]]

    assert upload["uses"].startswith("actions/upload-artifact@")
    assert upload["if"] == "always()"
    assert upload["with"]["name"] == AUDIT_VERDICT_ARTIFACT
    assert upload["with"]["path"].endswith(AUDIT_VERDICT_FILENAME)
    assert upload["with"]["if-no-files-found"] == "error"
    assert names.index(WANDB_STEP) < names.index(WANDB_UPLOAD_STEP)

    # And the file it names is the one the check actually writes, run rather than read.
    finished = run_wandb_check(
        workflow, tmp_path, exit_code=0, report='{"looks_wrong": [], "verdict": "accepted"}'
    )

    assert finished.returncode == 0, finished.stderr
    published = tmp_path / AUDIT_VERDICT_FILENAME
    assert published.is_file(), "the upload would find nothing to publish"
    assert json.loads(published.read_text(encoding="utf-8"))["verdict"] == "accepted"
    # In the log as well, because the person triaging a red cross is already looking at it.
    assert "accepted" in finished.stdout


def test_the_verdict_vocabulary_is_the_one_the_preflight_decides_on() -> None:
    """Mutation: spell a verdict as a string in either the tool or the preflight.

    Two files decide with this vocabulary and nothing at runtime would notice them
    disagreeing: a verdict the preflight does not recognise reads as no verdict, which lets
    every submission through silently. So the tool imports the enumeration rather than
    restating it, and this is what says so.
    """
    module = load_wandb_tool()

    assert module.Verdict is Verdict
    assert module.VERDICT_FIELD == VERDICT_FIELD
    assert {member.value for member in Verdict} == {"accepted", "refused", "unreachable"}
    # An outage is not a bad key, and keeping them apart is the reason there are three.
    assert module.verdict_for({"error": f"{module.UNREACHABLE_PREFIX}: URLError"}, faults=["x"]) is (
        Verdict.UNREACHABLE
    )
    assert module.verdict_for({"error": "W&B does not recognise this key"}, faults=["x"]) is (
        Verdict.REFUSED
    )
    assert module.verdict_for({"entity": "eduLLM"}, faults=[]) is Verdict.ACCEPTED


def release_step(workflow: dict[str, Any]) -> str:
    return step(workflow["jobs"][RELEASE_JOB], RELEASE_STEP)["run"]


def test_the_audit_compares_the_deployed_functions_against_the_records(
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


def test_a_deployed_function_that_is_not_the_released_one_fails_the_audit(
    workflow: dict[str, Any], tmp_path: Path
) -> None:
    """THE ONE THAT MATTERS. Mutation: report the difference and exit zero.

    The tool exits 1 when the digest AWS reports is not the digest the release record
    carries, and there is no alerting on this platform, so the red run is the whole signal.
    """
    stub = write_stub(tmp_path / "bin", "uv", "exit 1")

    finished = run_step_script(release_step(workflow), cwd=tmp_path, env={}, stub_bin=stub.parent)

    assert finished.returncode != 0


def test_a_release_check_that_could_not_look_also_fails_the_audit(
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


def test_the_audit_compares_each_deployed_stack_against_its_template(
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


def test_a_stack_that_is_not_the_template_on_main_fails_the_audit(
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


def test_a_stack_check_that_could_not_look_also_fails_the_audit(
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


def test_the_audit_joins_the_three_records_of_a_run(workflow: dict[str, Any]) -> None:
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


def test_a_run_only_one_of_the_three_sources_knows_about_fails_the_audit(
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

    The board exits 2 when a source was not read, and 1 when the sources it read disagree.
    The two send a reader to different places: a disagreement is a run to go and open, and a
    gap is a grant or a credential to go and fix. Somebody who reads a gap as a disagreement
    goes looking for a submitter who did nothing, on a morning whose only finding is that
    nobody could look.
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


# ----------------------------------------------------------------------------------------
# The placement verdicts, against the sixteen queues
# ----------------------------------------------------------------------------------------


def placement_step(workflow: dict[str, Any]) -> str:
    return step(workflow["jobs"][PLACEMENT_JOB], PLACEMENT_STEP)["run"]


def run_placement(workflow: dict[str, Any], tmp_path: Path, *, exit_code: int) -> Any:
    summary = tmp_path / "summary.md"
    summary.touch()
    stub = stub_tool(
        tmp_path, exit_code=exit_code, report="| `gpu-8xl40s` | unreliably | queue |"
    )
    return run_step_script(
        placement_step(workflow),
        cwd=tmp_path,
        env={"GITHUB_STEP_SUMMARY": str(summary)},
        stub_bin=stub.parent,
    )


def test_the_audit_recomputes_each_placement_verdict(workflow: dict[str, Any]) -> None:
    """Mutation: keep the job and drop the step, or run it without capturing the report.

    The grants #227 added were argued for on this job existing, so a workflow that holds the
    permission and reads nothing with it is the state the header of this file spent two
    paragraphs explaining it was in.
    """
    body = placement_step(workflow)

    assert PLACEMENT_TOOL in body
    assert "--output" in body, "the report is captured so it can be read after the failure"
    assert "uv run --frozen python" in body


def test_a_verdict_the_queues_contradict_fails_the_audit(
    workflow: dict[str, Any], tmp_path: Path
) -> None:
    finished = run_placement(workflow, tmp_path, exit_code=1)

    assert finished.returncode != 0
    assert "placement_verdict_disagrees_with_the_account" in finished.stderr
    # It reports and never rewrites, and the sentence saying so is where the reader is.
    assert "Nothing was rewritten" in finished.stderr


def test_queues_that_could_not_be_read_are_not_read_as_agreement(
    workflow: dict[str, Any], tmp_path: Path
) -> None:
    """Mutation: treat any non-zero exit as a disagreement.

    Exit 2 says the queues were not read at all, which is never a statement that the file is
    right. Reporting it as a disagreement sends somebody to re-measure a pool on the morning
    the two Batch grants lapsed, and reporting it as a pass silently stops the check covering
    anything -- which is exactly the state config/capacity.yaml's header describes this job
    being in before the grants existed.
    """
    finished = run_placement(workflow, tmp_path, exit_code=2)

    assert finished.returncode != 0
    assert "placement_verdicts_unusable" in finished.stderr
    assert "placement_verdict_disagrees_with_the_account" not in finished.stderr
    assert "batch:ListJobs" in finished.stderr


def test_verdicts_the_queues_support_pass(workflow: dict[str, Any], tmp_path: Path) -> None:
    """The other side, so the step cannot pass this file by failing unconditionally."""
    finished = run_placement(workflow, tmp_path, exit_code=0)

    assert finished.returncode == 0, finished.stderr


def test_the_placement_table_reaches_the_log_and_not_only_the_step_summary(
    workflow: dict[str, Any], tmp_path: Path
) -> None:
    summary = tmp_path / "summary.md"
    summary.touch()
    stub = stub_tool(tmp_path, exit_code=1, report="gpu-8xl40s is recorded unreliably")

    finished = run_step_script(
        placement_step(workflow),
        cwd=tmp_path,
        env={"GITHUB_STEP_SUMMARY": str(summary)},
        stub_bin=stub.parent,
    )

    assert "gpu-8xl40s is recorded unreliably" in finished.stdout
    assert "gpu-8xl40s is recorded unreliably" in summary.read_text(encoding="utf-8")


def test_the_placement_check_never_prints_an_account_id(workflow: dict[str, Any]) -> None:
    """Every ARN in a Batch denial carries the account id, and this log is public."""
    assert PLACEMENT_TOOL in scripts(workflow, PLACEMENT_JOB)
    source = (PROJECT_ROOT / PLACEMENT_TOOL).read_text(encoding="utf-8")

    assert "carries the account id" in source
    assert not ACCOUNT_LITERAL.search(source)


def test_the_placement_check_reads_only_grants_the_role_holds(role: dict[str, Any]) -> None:
    """Mutation: reach for a read the role does not have, such as the scaling activities.

    The autoscaling history is where a shape's refusals actually live, and it is the obvious
    thing to want. It is not granted, and a tool that called for it would be red every night
    with an exit 2 rather than reporting anything -- which is the failure the visibility board
    spent its first weeks in and which this file's header exists to stop recurring.
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
    source = (PROJECT_ROOT / PLACEMENT_TOOL).read_text(encoding="utf-8")

    assert {"batch:ListJobs", "batch:DescribeJobs"} <= granted
    assert '"list-jobs"' in source and '"describe-jobs"' in source
    for ungranted in ("describe-job-queues", "describe-scaling-activities", "describe-instances"):
        assert ungranted not in source, (
            f"{ungranted} is not a read this role holds, so calling it would make the job "
            "unable to look rather than able to report"
        )


# ----------------------------------------------------------------------------------------
# The reading, and the two sources that forget
# ----------------------------------------------------------------------------------------


def capture_step(workflow: dict[str, Any]) -> str:
    return step(workflow["jobs"][CAPTURE_JOB], CAPTURE_STEP)["run"]


def capture_command(workflow: dict[str, Any]) -> str:
    """The one line that runs the collector, with its continuations joined.

    THE COMMAND RATHER THAN THE WHOLE STEP BODY, AND THE DIFFERENCE IS NOT PEDANTRY. This
    step prints a sentence naming `--write` when the file it expects is missing, so a
    substring search over the body passes for a step that dropped the flag from the command
    and kept it in the error message. That mutation was made and the body-wide search did not
    notice it.
    """
    joined = re.sub(r"\\\s*\n\s*", " ", capture_step(workflow))
    running = [line for line in joined.splitlines() if CAPTURE_TOOL in line and "uv run" in line]
    assert len(running) == 1, f"expected one invocation of {CAPTURE_TOOL}, found {running}"
    return running[0]


def run_capture(
    workflow: dict[str, Any], tmp_path: Path, *, exit_code: int, writes: bool = True
) -> Any:
    """The capture step, with the collector replaced by one that honours ``--write``.

    Honouring the flag rather than writing to a fixed name, so a step that stopped passing
    ``--write`` is a failure here rather than a branch that grows nothing at 05:00.
    """
    summary = tmp_path / "summary.md"
    summary.touch()
    stub = write_stub(
        tmp_path / "bin",
        "uv",
        f"""
destination=""
while [[ $# -gt 0 ]]; do
  if [[ "$1" == "--write" ]]; then destination="$2"; fi
  shift
done
printf '%s\\n' '1 run(s), 1 priced, not read launch event(s)'
if [[ -n "${{destination}}" && {"1" if writes else "0"} -eq 1 ]]; then
  mkdir -p "$(dirname "${{destination}}")"
  printf '%s\\n' '{{"format_version": 1}}' > "${{destination}}"
fi
exit {exit_code}
""",
    )
    return run_step_script(
        capture_step(workflow),
        cwd=tmp_path,
        env={"GITHUB_STEP_SUMMARY": str(summary)},
        stub_bin=stub.parent,
    )


def test_the_audit_takes_a_reading_and_writes_it_down(workflow: dict[str, Any]) -> None:
    """Mutation: run the collector without --write, which prints a summary and keeps nothing.

    This is the one job here whose product is a record rather than a verdict, and the reason
    is that two of the four sources forget: Batch drops a job about a week after it ends and
    CloudWatch keeps a run log for ninety days. A job that reads all four and writes none of
    it down leaves exactly the same hole as not running at all, one day later.
    """
    command = capture_command(workflow)

    assert "--write" in command, "a reading that is only printed is a reading nobody kept"
    assert "uv run --frozen python" in command


def test_the_reading_is_named_by_the_day_in_utc(workflow: dict[str, Any], tmp_path: Path) -> None:
    """Mutation: name the file with the local date, or with a fixed name.

    A fixed name overwrites yesterday, which is the whole of what this job is for. A local
    date produces two names for one morning as soon as anybody dispatches this by hand from a
    laptop, and the branch is keyed by day.
    """
    assert "date -u" in capture_step(workflow), "the runner is UTC and a laptop is not"

    finished = run_capture(workflow, tmp_path, exit_code=0)

    assert finished.returncode == 0, finished.stderr
    written = sorted(path.name for path in (tmp_path / "substrate").iterdir())
    assert len(written) == 1
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}\.json", written[0]), written


def test_a_morning_the_account_could_not_be_read_fails_the_audit(
    workflow: dict[str, Any], tmp_path: Path
) -> None:
    """THE ONE THAT MATTERS. Mutation: report the refusal and exit zero.

    A missing reading cannot be taken later. Every other job in this file compares two things
    that will both still be there tomorrow, so a red cross on one of them is a disagreement
    somebody can go and look at whenever they get to it. This one goes red about a question
    that stops being answerable, which is why it is red at all: the tool itself judges nothing
    and has no exit 1.
    """
    finished = run_capture(workflow, tmp_path, exit_code=2, writes=False)

    assert finished.returncode != 0
    assert "substrate_not_read" in finished.stderr


def test_a_reading_that_was_reported_and_never_written_fails_the_audit(
    workflow: dict[str, Any], tmp_path: Path
) -> None:
    """Mutation: trust the exit code and let the upload find nothing.

    An upload with `if-no-files-found: warn` is green on an empty directory, so a `--write`
    that silently stopped working would leave a green audit, a step summary full of figures
    and a branch that has not moved in a month. The exit code says the read happened; the
    file is what says it was kept.
    """
    finished = run_capture(workflow, tmp_path, exit_code=0, writes=False)

    assert finished.returncode != 0
    assert "substrate_reading_is_empty" in finished.stderr


def test_the_reading_reaches_the_log_and_not_only_the_step_summary(
    workflow: dict[str, Any], tmp_path: Path
) -> None:
    """Same argument the reconciliation and the board are held to."""
    finished = run_capture(workflow, tmp_path, exit_code=0)
    summary = (tmp_path / "summary.md").read_text(encoding="utf-8")

    assert "1 run(s), 1 priced" in finished.stdout
    assert "1 run(s), 1 priced" in summary


def test_the_reading_is_published_before_anything_can_lose_it(workflow: dict[str, Any]) -> None:
    """Mutation: upload only on success, or upload after the branch push.

    The capture goes red when a source was refused and still writes what it did read, so an
    upload conditioned on success would drop the reading on exactly the morning somebody
    wants it. The artifact is also the hand-off to the job that commits it, so a failure to
    push must cost the branch and not the reading.
    """
    steps = workflow["jobs"][CAPTURE_JOB]["steps"]
    names = [str(item.get("name", "")) for item in steps]
    upload = step(workflow["jobs"][CAPTURE_JOB], CAPTURE_UPLOAD_STEP)

    assert upload["uses"].startswith("actions/upload-artifact@")
    assert upload["if"] == "always()"
    assert upload["with"]["name"] == CAPTURE_ARTIFACT
    assert names.index(CAPTURE_STEP) < names.index(CAPTURE_UPLOAD_STEP)


def test_the_history_job_keeps_the_reading_where_it_can_be_diffed(
    workflow: dict[str, Any],
) -> None:
    """Mutation: append to main, or force-push the branch the way the run index will.

    main refuses this push and should, because branch protection wants two status checks
    this token cannot produce. It wanted an approving review and a code-owner review until
    2026-08-05 and the push was refused then too. The branch is appended to rather than
    force-pushed because with
    a source that forgets, last week is the only copy of last week -- which is the opposite of
    the run index, where a snapshot refreshed on state change is overwritten in place and its
    history is noise.
    """
    job = workflow["jobs"][HISTORY_JOB]
    body = step(job, HISTORY_STEP)["run"]

    assert job["needs"] == CAPTURE_JOB
    assert job["if"] == "always()"
    assert f"git push origin {HISTORY_BRANCH}" in body
    assert "--force" not in body.split("git push")[1], "the history is the product here"
    assert "git switch --orphan" in body, "the readings carry none of the tree they came from"


def test_each_writing_job_is_the_single_writer_of_its_own_branch(
    workflow: dict[str, Any],
) -> None:
    """Mutation: drop a group, share one between them, or cancel in progress.

    Two runs appending to one branch race, and the loser is a reading nobody notices is
    missing until somebody goes looking for that day. Cancelling is the same loss arriving
    differently, which is why the flag is asserted as well as the group.

    Sharing one group between them is the third mutation and the least obvious. They push
    different branches and are `needs: substrate-capture` siblings, so a shared group would
    serialise them inside a single run for a race that does not exist between them, and the
    page would be written a runner-minute after the reading it describes for no reason.
    """
    for job, group in ((HISTORY_JOB, HISTORY_BRANCH), (ACTIVITY_JOB, ACTIVITY_BRANCH)):
        concurrency = workflow["jobs"][job]["concurrency"]

        assert concurrency["cancel-in-progress"] is False, job
        assert group.removeprefix("machine/") in concurrency["group"], (
            f"{job} must key its group on the branch it is the single writer of"
        )

    assert (
        workflow["jobs"][HISTORY_JOB]["concurrency"]["group"]
        != workflow["jobs"][ACTIVITY_JOB]["concurrency"]["group"]
    )


def test_no_job_that_only_computes_a_verdict_takes_a_concurrency_group() -> None:
    """THE ONE THAT MATTERS. Mutation: put `concurrency: group: audit` back on the file.

    That is where it was, and it made this workflow undispatchable. checkpoint-reconciliation
    had been taking about forty minutes a run for at least eleven hours on 2026-08-06 while
    every other job finished inside four, and it held the group for all of them. Two people
    dispatched the audit at 07:32 and 07:35 to check something before a deploy; a scheduled
    run arriving at 07:44 discarded the second and the second had already discarded the first.
    Neither ever started a job.

    `cancel-in-progress: false` does not prevent that and cannot. The group holds one pending
    run, so a third arrival cancels the one waiting -- the flag chooses which dispatch is
    thrown away, not whether one is. The only fix is for the jobs that compute verdicts to be
    in no group at all: two runs computing the same verdict concurrently is a duplicate
    answer, not a race, and it costs a runner minute.

    Asserted on the file rather than on a job list, because the failure it guards is somebody
    adding a group back at the top, which no per-job assertion would see.
    """
    workflow = load_workflow(WORKFLOW_PATH)

    assert "concurrency" not in workflow, (
        "a group here is charged to every job in the file, including the one that takes "
        "forty minutes, and a dispatch is what pays it"
    )

    grouped = {name for name, job in workflow["jobs"].items() if "concurrency" in job}

    assert grouped == {HISTORY_JOB, ACTIVITY_JOB}, (
        "only the two jobs that push a branch have anything to serialise"
    )


def test_the_slowest_job_is_bounded_well_above_how_long_it_takes() -> None:
    """Mutation: drop the bound, or set it at what the job currently takes.

    Both directions are wrong and for opposite reasons. With no bound a call that never
    returns sits for GitHub's six-hour default, which is how long nobody could tell a wedged
    job from one reading a bucket. Set at the current runtime, an ordinary slow morning goes
    red for being slow, and this job's runtime is a function of how many runs the platform has
    ever accepted -- 38, 43, 45, 44 and 39 minutes across five consecutive runs while the
    intent tree grew under it. A read of a growing bucket is not a defect.

    So the bound is above the worst figure measured rather than near the expected one, and
    what makes that safe is that the tool now names each run before reading it: slow is
    visible in the log, and this only has to catch wedged.
    """
    #: The longest this job has been measured taking, on 2026-08-06.
    slowest_observed = 45
    #: What GitHub allows a job with no bound, which is what the old state was.
    github_default = 6 * 60
    bound = load_workflow(WORKFLOW_PATH)["jobs"][RECONCILE_JOB]["timeout-minutes"]

    assert bound >= slowest_observed, "an ordinary slow morning must not go red for being slow"
    assert bound < github_default, "a wedged call must not be allowed to sit for the default"


def test_the_history_job_commits_nothing_when_no_reading_was_published(
    workflow: dict[str, Any], tmp_path: Path
) -> None:
    """Mutation: commit unconditionally, which puts an empty day on the branch.

    A dated commit holding nothing reads as a morning the platform ran nothing, on a branch
    whose whole purpose is being believed later. The capture writes no file when the
    collection failed outright, so the absence is what has to travel.
    """
    stub = write_stub(tmp_path / "bin", "git", 'echo "git $*" >&2\nexit 1')
    (tmp_path / "substrate").mkdir()

    finished = run_step_script(
        step(workflow["jobs"][HISTORY_JOB], HISTORY_STEP)["run"],
        cwd=tmp_path,
        env={},
        stub_bin=stub.parent,
    )

    assert finished.returncode == 0, finished.stderr
    assert "git push" not in finished.stderr


# ----------------------------------------------------------------------------------------
# The page, which is a view over the reading and reads nothing else
# ----------------------------------------------------------------------------------------


def _seed_a_branch(root: Path, pages: dict[str, str]) -> Path:
    """A bare origin already carrying `machine/activity`, and the URL to reach it by.

    ``file://`` rather than a path, because the step fetches with ``--depth=1`` and git
    refuses a shallow fetch over the local transport. A path here would make the fetch fail,
    the `|| true` swallow it and the switch fall through to `--orphan`, which is the state
    the test below exists to prove does not happen.
    """
    origin = root / "origin.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(origin)], check=True,
                   capture_output=True)
    seed = root / "seed"
    (seed / "activity").mkdir(parents=True)
    for name, body in pages.items():
        (seed / "activity" / name).write_text(body, encoding="utf-8")
    for command in (
        ["git", "init", "-b", ACTIVITY_BRANCH],
        ["git", "config", "user.email", "seed@example.invalid"],
        ["git", "config", "user.name", "seed"],
        ["git", "add", "activity"],
        ["git", "commit", "-m", "seed"],
        ["git", "remote", "add", "origin", f"file://{origin}"],
        ["git", "push", "origin", ACTIVITY_BRANCH],
    ):
        subprocess.run(command, cwd=seed, check=True, capture_output=True)
    return origin


def _a_checkout_holding(root: Path, origin: Path, pages: dict[str, str]) -> Path:
    """What the runner has when the branch step starts: a tree, and an untracked page."""
    work = root / "work"
    (work / "activity").mkdir(parents=True)
    (work / "README.md").write_text("the tree the tool ran from\n", encoding="utf-8")
    for name, body in pages.items():
        (work / "activity" / name).write_text(body, encoding="utf-8")
    for command in (
        ["git", "init", "-b", "main"],
        ["git", "config", "user.email", "runner@example.invalid"],
        ["git", "config", "user.name", "runner"],
        ["git", "add", "README.md"],
        ["git", "commit", "-m", "tree"],
        ["git", "remote", "add", "origin", f"file://{origin}"],
    ):
        subprocess.run(command, cwd=work, check=True, capture_output=True)
    return work


def test_the_activity_job_reads_the_reading_and_never_the_account(
    workflow: dict[str, Any],
) -> None:
    """Mutation: drop --reading and let the tool collect, or add the credential preamble.

    The page and the reading beside it are one pipeline and two publications, and the whole
    value of that is that they cannot disagree about one run. A page that re-read the account
    would be a second ingestion, and the disagreement would surface as two dollar figures for
    the same thing. It is also what makes this job safe to give a repository write: it holds
    no AWS identity because it needs none.
    """
    job = workflow["jobs"][ACTIVITY_JOB]
    body = step(job, ACTIVITY_STEP)["run"]

    assert ACTIVITY_TOOL in body
    assert "--reading" in body
    assert job["permissions"] == {"contents": "write"}
    assert "id-token" not in job["permissions"]
    assert [item for item in job["steps"] if "aws-actions/" in item.get("uses", "")] == []


def test_the_page_is_named_by_the_day_in_utc(workflow: dict[str, Any], tmp_path: Path) -> None:
    """Mutation: name the page with the local date, or with a fixed name.

    A fixed name overwrites yesterday, and the history is the product: a page is how somebody
    sees that a source has been refused for a week, which is a diff rather than a value. A
    local date produces two names for one morning the first time anybody dispatches this from
    a laptop.
    """
    body = step(workflow["jobs"][ACTIVITY_JOB], ACTIVITY_STEP)["run"]
    assert "date -u" in body, "the runner is UTC and a laptop is not"

    stub = write_stub(
        tmp_path / "bin",
        "uv",
        'mkdir -p activity\nprintf "# a page\\n" > "activity/$(date -u +%F).md"',
    )
    (tmp_path / "substrate").mkdir()
    (tmp_path / f"substrate/{datetime.now(UTC):%Y-%m-%d}.json").write_text("{}", encoding="utf-8")
    summary = tmp_path / "summary.md"

    finished = run_step_script(
        body,
        cwd=tmp_path,
        env={"GITHUB_STEP_SUMMARY": str(summary)},
        stub_bin=stub.parent,
    )

    assert finished.returncode == 0, finished.stderr
    written = sorted(path.name for path in (tmp_path / "activity").iterdir())
    assert len(written) == 1
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}\.md", written[0]), written
    assert "a page" in summary.read_text(encoding="utf-8")


def test_a_morning_with_no_reading_writes_no_page(
    workflow: dict[str, Any], tmp_path: Path
) -> None:
    """Mutation: run the tool anyway and let it report a day on which nothing happened.

    A page saying nothing ran and a morning nobody could read the account are the same
    document and only one of them is true. The capture writes no file when the collection
    failed outright, so the absence is what has to travel, and this job's redness is what
    says the page is missing rather than empty.
    """
    stub = write_stub(tmp_path / "bin", "uv", 'echo "uv $*" >&2\nexit 0')
    (tmp_path / "substrate").mkdir()

    finished = run_step_script(
        step(workflow["jobs"][ACTIVITY_JOB], ACTIVITY_STEP)["run"],
        cwd=tmp_path,
        env={"GITHUB_STEP_SUMMARY": str(tmp_path / "summary.md")},
        stub_bin=stub.parent,
    )

    assert finished.returncode != 0
    assert "activity_has_no_reading_to_read" in finished.stderr
    assert not (tmp_path / "activity").exists()


def test_the_page_is_published_before_anything_can_lose_it(workflow: dict[str, Any]) -> None:
    """Mutation: upload after the branch push, or only on success.

    A push that is refused must cost the branch and not the page. The artifact is also the
    only copy on a morning the branch protection posture changes, which has happened here.
    """
    job = workflow["jobs"][ACTIVITY_JOB]
    names = [str(item.get("name", "")) for item in job["steps"]]
    upload = step(job, ACTIVITY_UPLOAD_STEP)

    assert upload["uses"].startswith("actions/upload-artifact@")
    assert upload["if"] == "always()"
    assert upload["with"]["name"] == ACTIVITY_ARTIFACT
    assert names.index(ACTIVITY_STEP) < names.index(ACTIVITY_UPLOAD_STEP)
    assert names.index(ACTIVITY_UPLOAD_STEP) < names.index(ACTIVITY_HISTORY_STEP)


def test_the_activity_branch_is_appended_to_rather_than_force_pushed(
    workflow: dict[str, Any],
) -> None:
    """Mutation: force-push the branch the way the run index does.

    The run index is overwritten in place because a snapshot refreshed on state change has a
    history that is noise. This is the opposite: the page for a day is the only page for that
    day, and yesterday's is what makes today's readable.
    """
    body = step(workflow["jobs"][ACTIVITY_JOB], ACTIVITY_HISTORY_STEP)["run"]

    assert f"git push origin {ACTIVITY_BRANCH}" in body
    assert "--force" not in body.split("git push")[1], "the history is the product here"
    assert "git switch --orphan" in body, "the pages carry none of the tree they came from"


def test_a_second_run_on_one_day_keeps_the_pages_the_branch_already_had(
    workflow: dict[str, Any], tmp_path: Path
) -> None:
    """Mutation: switch to the branch before moving the freshly written page aside.

    A page for a day the branch already carries is an untracked file git refuses to overwrite,
    so the switch fails, the `||` falls through to `--orphan`, and what gets pushed is a
    branch holding one day. The push is refused as non-fast-forward, so nothing is lost -- but
    the job is red for a re-run, which is a thing somebody does to a page far more often than
    to a reading. Run against real git rather than a stub, because the failure is git's
    behaviour and a stub would be asserting this test's own idea of it.
    """
    today = f"{datetime.now(UTC):%Y-%m-%d}.md"
    origin = _seed_a_branch(tmp_path, {"2026-08-04.md": "older\n", today: "the first attempt\n"})
    work = _a_checkout_holding(tmp_path, origin, {today: "the second attempt\n"})

    finished = run_step_script(
        step(workflow["jobs"][ACTIVITY_JOB], ACTIVITY_HISTORY_STEP)["run"],
        cwd=work,
        env={"HOME": str(tmp_path)},
        stub_bin=None,
    )

    assert finished.returncode == 0, finished.stderr
    listed = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", ACTIVITY_BRANCH],
        cwd=origin,
        check=True,
        capture_output=True,
        text=True,
    )
    assert sorted(listed.stdout.split()) == ["activity/2026-08-04.md", f"activity/{today}"]

    rewritten = subprocess.run(
        ["git", "show", f"{ACTIVITY_BRANCH}:activity/{today}"],
        cwd=origin,
        check=True,
        capture_output=True,
        text=True,
    )
    assert rewritten.stdout == "the second attempt\n"


# ----------------------------------------------------------------------------------------
# Counting the asks, which is the only job here that reads no AWS
# ----------------------------------------------------------------------------------------


def test_the_asks_job_assumes_nothing_and_needs_no_repository_variable(
    workflow: dict[str, Any],
) -> None:
    """Mutation: give it the credential preamble the neighbouring jobs carry.

    Every other job in this file can fail before it asks its question -- the role variable can
    be unset, the role can be undeployed, the assume can be refused -- and each carries a guard
    step saying so. This one reads this repository's own issues on the token the runner already
    holds, so it has no role to assume and nothing to be misconfigured. Copying the preamble in
    would give the cheapest job on the schedule three new ways to go red about something other
    than asks.
    """
    job = workflow["jobs"][ASKS_JOB]
    rendered = json.dumps(job)

    assert job["permissions"] == {"contents": "read", "issues": "read"}
    assert ROLE_VARIABLE not in rendered
    assert "aws-actions/configure-aws-credentials" not in rendered
    assert GUARD_STEP not in [item.get("name") for item in job["steps"]]


def test_the_asks_job_writes_its_answer_where_a_person_will_see_it(
    workflow: dict[str, Any],
) -> None:
    """Mutation: drop the redirect and let it print to the log.

    The output is a table somebody reads to decide what to build next, and a scheduled run's
    log is not somewhere anybody looks unless it is red. This job is never red on a finding by
    construction, so the step summary is the only place its answer is seen at all.
    """
    body = step(workflow["jobs"][ASKS_JOB], ASKS_STEP)["run"]

    assert ASKS_TOOL in body
    assert 'GITHUB_STEP_SUMMARY' in body


def test_the_lead_gate_job_takes_no_credential_of_any_kind(workflow: dict[str, Any]) -> None:
    """Mutation: give it the credential preamble its neighbours carry.

    It reads two committed files. A role, a repository variable or an id-token would each be a
    way for it to go red for a reason outside the question it was asked, which is the state
    the visibility board spent its first weeks in. There is nothing here for a credential to
    fetch: the one thing a credential could buy is reading the GitHub team live, and no token
    available on this schedule can do that at all.
    """
    job = workflow["jobs"][LEAD_GATE_JOB]

    assert "permissions" not in job
    assert [one for one in job["steps"] if "aws-actions/" in one.get("uses", "")] == []
    assert GUARD_STEP not in [one.get("name") for one in job["steps"]]


def test_the_lead_gate_job_writes_its_answer_where_a_person_will_see_it(
    workflow: dict[str, Any],
) -> None:
    """Mutation: drop the redirect and let it print to the log.

    Unlike the asks job this one is red on a finding, so the log is read on the mornings that
    matter. The summary is for the other mornings: the age it prints is the number that says
    how much the green tick is worth, and a green tick nobody can price is the thing this job
    was built to replace.
    """
    body = step(workflow["jobs"][LEAD_GATE_JOB], LEAD_GATE_STEP)["run"]

    assert LEAD_GATE_TOOL in body
    assert "GITHUB_STEP_SUMMARY" in body


def test_the_lead_gate_job_does_not_swallow_the_tool_exit_code(
    workflow: dict[str, Any],
) -> None:
    """Mutation: let the redirect's own status stand as the step's.

    The tool's answer is its exit code and the step wraps it in a pipeline that writes a
    summary. Losing the code between the two is the quietest way to build a check that cannot
    fail, and it looks exactly like a job that passes every morning.
    """
    body = step(workflow["jobs"][LEAD_GATE_JOB], LEAD_GATE_STEP)["run"]

    assert "status=$?" in body
    assert 'exit "${status}"' in body


def test_the_audit_reads_the_approval_gate_out_of_github_every_morning(
    workflow: dict[str, Any],
) -> None:
    """Mutation: delete this job, which costs nothing anybody would notice.

    It is the only check anywhere that reads the control deciding whether a run waits for a
    person. Everything else in this repository holds a record against reality; the approval
    environments are a browser setting, and between the thirty-day capture under
    ``fixtures/evidence/phase-2/github/`` and its expiry nothing said what they were. Deleted,
    the suite stays green, every submission keeps working, and the morning somebody converts
    this repository to private every waiting job proceeds with nobody asked.

    ``lead-gate`` beside it is not a substitute and neither is a substitute for it. That job
    asks who stands behind the reviewer slot and can only do it from a capture; this one asks
    whether the slot is there, and can only do that live. The failure each is blind to is the
    other's subject.

    Also mutation: give it ``id-token: write`` and a role, on the reasoning that a check about
    the gate belongs with the other account checks. It reads GitHub rather than AWS, both
    endpoints answer on a public repository with no session at all, and a credential here
    would be three new ways for the first job in the file to go red about something other than
    the gate.
    """
    job = workflow["jobs"][GATE_JOB]
    rendered = json.dumps(job)
    body = step(job, GATE_STEP)["run"]

    assert GATE_TOOL in body
    assert "permissions" not in job
    assert ROLE_VARIABLE not in rendered
    assert "aws-actions/configure-aws-credentials" not in rendered
    assert GUARD_STEP not in [item.get("name") for item in job["steps"]]

    # The repository comes from the context rather than from the tool's default. The default
    # is `edu-llm/platform` and it is right, which is exactly why it must not be what runs
    # here: a fork or a rename would have this job reporting confidently on somebody else's
    # settings.
    assert "${{ github.repository }}" in body


@pytest.mark.parametrize(
    ("job_id", "step_name"),
    [
        (GATE_JOB, GATE_STEP),
        (RECONCILE_JOB, RECONCILE_STEP),
        (WANDB_JOB, WANDB_STEP),
        (RELEASE_JOB, RELEASE_STEP),
        (STACKS_JOB, STACKS_STEP),
        (BOARD_JOB, BOARD_STEP),
        (PLACEMENT_JOB, PLACEMENT_STEP),
        (CAPTURE_JOB, CAPTURE_STEP),
        (ASKS_JOB, ASKS_STEP),
        (DOCKERFILES_JOB, DOCKERFILES_STEP),
        (LEAD_GATE_JOB, LEAD_GATE_STEP),
    ],
)
def test_nothing_in_any_of_these_jobs_is_allowed_to_be_informational(
    workflow: dict[str, Any], job_id: str, step_name: str
) -> None:
    """Mutation: `continue-on-error: true` on the job or on the reporting step.

    It is the obvious response to a job that goes red on the first morning, and it turns the
    job into one that cannot say anything. The header of the workflow argues the point for
    the three checks that were already there, and these are the fourth onwards.
    """
    job = workflow["jobs"][job_id]

    assert "continue-on-error" not in job
    assert "needs" not in job, "a failure elsewhere in this file must not skip this"
    for item in job["steps"]:
        assert "continue-on-error" not in item, item.get("name")

    body = step(job, step_name)["run"]
    assert "|| true" not in body
    assert "exit 0" not in body


#: The two jobs above excused from the per-job check, and why. Both publish what the capture
#: read, so both carry `needs: substrate-capture` by design and the "a failure elsewhere must
#: not skip this" assertion cannot hold for either. Named here rather than simply left out, so
#: that the sweep below can tell an excused job from a forgotten one.
NEEDS_ITS_PREDECESSOR = (HISTORY_JOB, ACTIVITY_JOB)


def test_every_job_in_this_file_is_either_checked_above_or_excused_by_name(
    workflow: dict[str, Any],
) -> None:
    """Mutation: add a job and do not add it to the list above.

    The list above is written out, and a written-out list of things to check gets weaker every
    time the file grows past it -- a job added and not added there is unchecked and looks
    exactly like a job that passed. This is what makes forgetting loud. An excused job goes in
    NEEDS_ITS_PREDECESSOR with the reason beside it, which is a decision rather than an
    omission.
    """
    checked = {
        RECONCILE_JOB,
        WANDB_JOB,
        RELEASE_JOB,
        STACKS_JOB,
        BOARD_JOB,
        PLACEMENT_JOB,
        CAPTURE_JOB,
        ASKS_JOB,
        DOCKERFILES_JOB,
        ROSTER_JOB,
        LEAD_GATE_JOB,
        GATE_JOB,
    }
    unaccounted = set(workflow["jobs"]) - checked - set(NEEDS_ITS_PREDECESSOR)

    assert not unaccounted, (
        f"{sorted(unaccounted)} run on the audit schedule and nothing checks that they are "
        "allowed to fail. Add each to the parametrized list above, or to "
        "NEEDS_ITS_PREDECESSOR with the reason it cannot be checked that way."
    )


def test_no_job_anywhere_in_this_file_is_allowed_to_continue_on_error(
    workflow: dict[str, Any],
) -> None:
    """Mutation: put `continue-on-error: true` on the one job the list above excuses.

    Held over every job including the excused one, because the reason substrate-history is
    excused is its `needs`, not its right to be informational. A reader that cannot fail
    reports nothing and looks exactly like a reader that found nothing.
    """
    for job_id, job in workflow["jobs"].items():
        assert "continue-on-error" not in job, job_id
        for item in job["steps"]:
            assert "continue-on-error" not in item, f"{job_id}: {item.get('name')}"


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
    assert "audit_reader_role_not_deployed" in refused.stderr
    # A diagnosis with nowhere to go is half an answer, and the template name is the half
    # that turns this into something the reader can act on.
    assert ROLE_VARIABLE in refused.stderr
    assert "audit-reader-role.yaml" in refused.stderr
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

    ``lambda:InvokeFunction`` IS THE ONE ACTION HERE THAT IS NEITHER A READ NOR A WRITE, AND
    IT IS ARGUED RATHER THAN WAVED THROUGH. It arrived from the 2026-08-06 notifier outage,
    where every grant in this policy answered correctly about a function that had raised on
    all 934 of its invocations: they read what bytes are deployed, and the bytes were right.
    The only question that would have caught it is whether the thing runs, and nothing can
    ask that without running it.

    What keeps it inside this test's promise is the resource, which is checked separately by
    ``test_the_lambda_grant_names_the_functions_the_templates_declare``: it reaches the
    notifier and nothing else, and the notifier invoked on the committed non-terminal fixture
    owes no message, so it loads its catalogs, reads its secret, projects the event and posts
    nothing. Invoking any of the other three would admit a submission, file lineage for a run
    that did not happen, or stop machines, and none of those is grantable here at any width.

    ``events:TestEventPattern`` IS THE SECOND ACTION HERE THAT IS NOT A READ OF ANYTHING, and
    it is a smaller thing than the invoke above. It touches no account state at all: it takes
    a pattern and an event as request parameters, runs EventBridge's matcher over them, and
    answers true or false. Nothing in the account is read, nothing is changed, and nothing is
    disclosed that the caller did not put in the request. It is here because the alternative
    is writing a matcher in this repository and believing it, and the wildcard platform #310
    turns on is exactly the sort that is easy to get almost right.
    The write half of that service stays absent and matters: a credential that could disable
    the lifecycle rule could stop every run's lineage being written and leave nothing red.
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
        "lambda:InvokeFunction",
        "cloudformation:GetTemplate",
        "cloudformation:ListStacks",
        "cloudwatch:GetMetricStatistics",
        "cloudwatch:DescribeAlarms",
        "sns:ListSubscriptionsByTopic",
        "tag:GetResources",
        "cloudtrail:LookupEvents",
        "batch:ListJobs",
        "batch:DescribeJobs",
        "iam:ListRoles",
        "events:DescribeRule",
        "events:TestEventPattern",
    }
    for action in granted:
        assert not any(fragment in action for fragment in MUTATING_ACTION_FRAGMENTS), action
        assert action.startswith(
            (
                "s3:",
                "secretsmanager:",
                "lambda:",
                "cloudformation:",
                "cloudwatch:",
                "sns:",
                "tag:",
                "cloudtrail:",
                "batch:",
                "iam:",
                "events:",
            )
        ), action
        assert "*" not in action, action

    # THE SHARPEST INSTANCE OF THIS RULE IN THE WHOLE POLICY, AND THE ONLY ONE WHERE THE
    # ADJACENT WRITE WOULD LET A CHECK MANUFACTURE THE THING IT REPORTS AS MISSING. The
    # roster comparison names people who hold a role and are not on the roster; a job holding
    # any of these three could close its own finding by creating the identity instead of
    # reporting it, and who may submit a run would then be decided by a cron line.
    for forbidden in ("iam:CreateRole", "iam:AttachRolePolicy", "iam:PutRolePolicy"):
        assert forbidden not in granted
    # GetRole is the adjacent read rather than an adjacent write, and it is absent for a
    # different reason: it is what would answer when a role was last assumed, one call per
    # role, and tools/report_roster_against_the_account.py argues at AccountRole why the
    # creation date is the column that dates the drift.
    assert "iam:GetRole" not in granted

    # The adjacent actions on the tagging grant, and the reason it is a read rather than a
    # way for the board to decide what appears on its own report. `tag:` is a service prefix
    # rather than the tagging action on another service, so these two are the whole pair.
    assert "tag:TagResources" not in granted
    assert "tag:UntagResources" not in granted

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

    # The adjacent actions on the queue grants, and the reason they need naming here rather
    # than being left to the substring pass: not one of Submit, Cancel or Terminate is a
    # fragment in MUTATING_ACTION_FRAGMENTS, so all three would read as harmless to it. Each
    # is a write to a queue this role exists to describe, and the placement check is the
    # sharpest case of the rule -- a check that can cancel a job can decide for itself that
    # the shape it is measuring does not place.
    for forbidden in ("batch:SubmitJob", "batch:CancelJob", "batch:TerminateJob"):
        assert forbidden not in granted

    assert all(statement["Effect"] == "Allow" for statement in statements(role))


def test_no_grant_reaches_a_whole_bucket_or_every_secret(role: dict[str, Any]) -> None:
    """Mutation: widen a resource to the bucket, or to `*`, to stop chasing an access denial.

    This is a shared sandbox account with sixteen other teams in it. A read grant is not
    harmless here: the lineage store holds every run's records and Secrets Manager holds
    everybody's credentials, so an unscoped read is an exfiltration path with a schedule.

    FOUR STATEMENTS ARE EXEMPT AND ALL FOUR ARE NAMED RATHER THAN PATTERN-MATCHED, so a
    fifth cannot arrive by widening a resource to `*` and calling these four precedent. The
    assertion runs the other way round as well -- exactly these Sids hold a wildcard and no
    others -- because an exemption list that is only a filter grows silently.

    `cloudformation:ListStacks` has no resource type: the request names no stack, so a
    policy naming one denies the call, and it is the action that stops the drift check being
    confined to the stacks it already knows about. What it discloses is stack names,
    statuses and timestamps, and no template, parameter or output.

    `tag:GetResources` is the same shape and is the wider disclosure of the two, since it
    answers with the ARNs and tags of resources in this region. It is what makes the account
    side of `tools/visibility_board.py` readable at all, and there is no narrower substitute:
    `batch:ListJobs` is granted below and is not one, because it enumerates a named queue and
    so sees only what this platform submitted to the sixteen queues it created, which is the
    half of the comparison the board already has.

    `cloudtrail:LookupEvents` takes no resource either, and the region condition is the whole
    of its bound. It reads the ninety-day management-event history, which is the only feed
    the platform does not write itself: without it the mismatch arm of the instruments can
    only compare the platform's records with themselves, which is the thing a mismatch report
    exists to stop. It is a wider disclosure than the other two, since an event carries the
    caller and the parameters of the call, and it stays because the alternative is a report
    that cannot see a launch nobody recorded.

    `batch:ListJobs` and `batch:DescribeJobs` share one statement and neither has a resource
    type: the service authorization reference gives both an empty resource column, so the
    jobQueue ListJobs takes and the job ids DescribeJobs takes are request parameters IAM
    cannot match an ARN against. Confining this to the sixteen queues in
    `config/execution-targets.yaml` is not a narrowing somebody declined to write. What it
    discloses is the job records of this region, which is the price already paid for the
    tagging read above.

    `iam:ListRoles` is the fifth and is the same shape once more: listing is what the action
    is for, so the request names no role and IAM offers no condition key for the name of one
    being listed. What it discloses is the name, path, creation date and trust policy of
    every role in a shared sandbox account. It is what makes
    `tools/report_roster_against_the_account.py` able to say who holds AWS access that
    `config/organization.yaml` has never heard of, which nothing else in the account or the
    repository can answer.
    """
    unscopable = {
        "FindStacksNothingInTheRepositoryAccountsFor",
        "FindEveryResourceThisPlatformTagged",
        "LookUpLaunchEvents",
        "ReadTheQueuesThePlacementVerdictNeeds",
        "ListTheInternRolesTheRosterIsComparedAgainst",
        # The two CloudWatch reads, and the reason is the API rather than a narrowing
        # somebody declined to write. A metric has no ARN: GetMetricStatistics is scoped by
        # namespace and dimension inside the request, and DescribeAlarms by name prefix, and
        # IAM offers no condition key for either. Both carry the region condition, which is
        # the only bound available. What they disclose is published metric statistics and
        # alarm configuration for this region, which is counts, thresholds and states.
        "ReadWhetherTheDeployedFunctionsSucceed",
        "ReadWhetherTheAlarmsHaveSomewhereToFire",
        # And the matcher, which is the one grant in this policy that reads nothing. IAM has
        # no resource type for TestEventPattern because the call names none: the pattern and
        # the event are both request parameters, and the answer is a boolean over the two of
        # them. There is nothing behind it to confine. The region condition is on it anyway,
        # as it is on every unscopable grant here, so a policy read cannot find a statement
        # with neither a resource nor a bound.
        "AskEventBridgeWhetherAPatternMatches",
    }
    assert {
        statement["Sid"]
        for statement in statements(role)
        if "*" in statement_resources(statement)
    } == unscopable

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


#: The unscopable grants whose service is global, so ``aws:RequestedRegion`` is not a bound
#: they can carry. Named as a set rather than left out of the parametrisation below, because
#: an exemption that is only an absence is one nobody has to argue for.
#:
#: IAM is the whole of it and is likely to stay so. Its endpoint is a single global one, so
#: every call this role makes to it reports the same region whatever the runner asked for,
#: and a condition on that value narrows nothing at all. Writing it anyway would read to the
#: next reviewer as a narrowing that exists, which is worse than its absence with a sentence
#: beside it -- and it would be a condition that silently starts denying the call if AWS ever
#: changes which region a global endpoint reports.
GLOBAL_SERVICE_GRANTS = {"ListTheInternRolesTheRosterIsComparedAgainst"}


@pytest.mark.parametrize(
    "sid",
    [
        "FindStacksNothingInTheRepositoryAccountsFor",
        "FindEveryResourceThisPlatformTagged",
        "LookUpLaunchEvents",
        "ReadTheQueuesThePlacementVerdictNeeds",
        "ReadWhetherTheDeployedFunctionsSucceed",
        "ReadWhetherTheAlarmsHaveSomewhereToFire",
    ],
)
def test_each_unscopable_grant_is_confined_to_the_region_this_platform_deploys_in(
    role: dict[str, Any], sid: str
) -> None:
    """Mutation: drop the condition, since the action cannot be scoped anyway.

    It is the only narrowing any of these actions admits and it is a small one, which is a
    reason to write down what it buys rather than a reason to leave it off. The region lock
    permits us-east-1 and us-east-2 and everything this platform has is in the first, so a
    read of the second is one nothing here asks for.

    EVERY UNSCOPABLE GRANT RATHER THAN THE TWO THIS STARTED WITH, and parametrised by Sid
    rather than by action because one of them holds two actions in one statement and an
    equality test against a single action string silently matches nothing. The rule this
    encodes was already written here -- a condition that covers the older statement and not
    the newer is the ordinary way a rule like this stops applying -- and it had already
    stopped applying to `cloudtrail:LookupEvents`, which is the widest of these and was never
    on the list. The case below holds this list and the exemption test's list together, so a
    statement cannot be exempted from scoping without landing in one of the two.
    """
    found = [statement for statement in statements(role) if statement["Sid"] == sid]

    assert len(found) == 1
    assert found[0]["Condition"] == {
        "StringEquals": {"aws:RequestedRegion": {"Fn::Sub": "${AWS::Region}"}}
    }


def test_the_only_unscopable_grant_with_no_region_bound_is_the_global_one(
    role: dict[str, Any],
) -> None:
    """Mutation: add a sixth wildcard grant and quietly leave its region condition off.

    THIS IS THE CASE THAT STOPS THE ONE ABOVE FROM BEING A LIST SOMEBODY CAN DECLINE TO JOIN.
    That test is parametrised over four Sids written out by hand, so a fifth wildcard grant
    that carried no condition would not fail it -- it would simply not be in it, which is the
    exact shape the exemption test's own docstring warns about one paragraph earlier and the
    shape this repository has now found more than a dozen times.

    So the two lists are held against the whole policy from the other direction: every
    statement whose resource is `*` either carries the region condition or is named in
    :data:`GLOBAL_SERVICE_GRANTS`, and nothing is in both. Adding a wildcard grant now means
    either writing the condition or arguing here why the service cannot take one.
    """
    wildcards = {
        statement["Sid"]
        for statement in statements(role)
        if "*" in statement_resources(statement)
    }
    conditioned = {
        statement["Sid"]
        for statement in statements(role)
        if statement["Sid"] in wildcards and "Condition" in statement
    }

    assert wildcards, "the policy holds no wildcard grant, so this is asserting nothing"
    assert conditioned & GLOBAL_SERVICE_GRANTS == set(), (
        "a grant argued to be global carries a region condition, so one of the two is wrong"
    )
    assert wildcards - conditioned == GLOBAL_SERVICE_GRANTS, (
        "a wildcard grant carries no region condition and is not argued to be global: "
        f"{sorted(wildcards - conditioned - GLOBAL_SERVICE_GRANTS)}"
    )


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
    module = load_tool("verify_deployed_stacks")

    role_properties = next(iter(iam_roles(load_template(ROLE_PATH))))
    granted = {
        reachable.rsplit(":stack/", 1)[1].removesuffix("/*")
        for statement in statements(role_properties)
        if statement["Action"] == "cloudformation:GetTemplate"
        for reachable in statement_resources(statement)
    }

    assert granted == set(module.STACKS)


def test_the_role_reads_the_two_buckets_the_reconciliation_asks_about(
    workflow: dict[str, Any],
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

    expected = {
        f"arn:${{AWS::Partition}}:s3:::{LINEAGE_BUCKET}": {
            "s3:ListBucket",
            "s3:GetBucketLocation",
        },
        f"arn:${{AWS::Partition}}:s3:::{OUTPUTS_BUCKET}": {
            "s3:ListBucket",
            "s3:GetBucketLocation",
        },
        f"arn:${{AWS::Partition}}:s3:::{OUTPUTS_BUCKET}/teams/*/runs/*": {"s3:GetObject"},
    }
    # The lineage object ARNs are built from what the scheduled tools sync rather than
    # listed here. Listing them is what let the grant and the readers disagree: a literal
    # here would have had to be edited in step with the policy and would have gone on
    # agreeing with it while both disagreed with the tools.
    required = synced_lineage_prefixes(workflow)
    expected |= {
        f"arn:${{AWS::Partition}}:s3:::{LINEAGE_BUCKET}/{prefix}/*": {"s3:GetObject"}
        for prefix in required
    }
    # EXACT ON THE BUCKETS AND ON EVERY REQUIRED PREFIX, AND NOT ON A DEGRADING ONE. The
    # board asks for `binding/` and survives the refusal, so the account side is narrower
    # tonight and whole the morning after somebody applies the statement the board prints.
    # An equality over the whole set would have made that application fail this check, which
    # would mean a test written before the grant landed voting against it.
    allowed = expected | {
        f"arn:${{AWS::Partition}}:s3:::{LINEAGE_BUCKET}/{prefix}/*": {"s3:GetObject"}
        for prefix in degrading_lineage_prefixes()
    }

    assert set(expected) <= set(reach), (
        f"the role cannot fetch {sorted(set(expected) - set(reach))}, which a scheduled "
        "tool syncs and cannot run without"
    )
    assert set(reach) <= set(allowed), (
        f"the role can fetch {sorted(set(reach) - set(allowed))}, which nothing scheduled "
        "reads. This role describes the account and every grant on it is argued for beside "
        "the statement; a prefix nothing reads is a read nobody asked for."
    )
    assert all(reach[reachable] == allowed[reachable] for reachable in reach)


def test_listing_is_confined_to_the_prefixes_each_check_reads(
    workflow: dict[str, Any],
    role: dict[str, Any],
) -> None:
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

    assert set(conditions) == {LINEAGE_BUCKET, OUTPUTS_BUCKET}
    assert conditions[OUTPUTS_BUCKET] == "teams/*/runs/*"
    required = {f"{prefix}/*" for prefix in synced_lineage_prefixes(workflow)}
    permitted = required | {f"{prefix}/*" for prefix in degrading_lineage_prefixes()}
    # Bounded on both sides rather than compared for equality, for the reason the fetch
    # check above gives: the board asks for a prefix it survives being refused, so the
    # condition is allowed to grow to it and to nothing else.
    assert required <= set(conditions[LINEAGE_BUCKET]) <= permitted


def test_the_role_can_list_and_fetch_every_prefix_the_scheduled_tools_sync(
    workflow: dict[str, Any],
    role: dict[str, Any],
) -> None:
    """Mutation: grant a prefix a scheduled tool syncs on GetObject and not on ListBucket.

    THIS IS THE CHECK THAT DID NOT EXIST, AND ITS ABSENCE COST EVERY COST FIGURE ON THE
    BOARD. `tools/report_run_costs.py` declared LINEAGE_PREFIXES = ("intent", "attempt") and
    this role granted `intent/` and `result/`. Both files were internally consistent, both
    were pinned by tests, and nothing read one against the other, so
    `tools/visibility_board.py` -- the only caller of `sync_bucket` that runs on the
    schedule, under this role -- was refused on `attempt/` on every night it ran. It did not
    lose the attempt records alone: `sync_bucket` raises on a refused prefix rather than
    skipping it, so the whole cost mapping came back None and every run rendered as "not
    costed".

    BOTH HALVES, BECAUSE ONLY ONE OF THEM FAILS VISIBLY. `aws s3 sync` lists before it
    fetches, so a prefix granted on GetObject and missing from the `s3:prefix` condition
    reads as granted in the policy document and is refused at the first call, with no object
    fetched. Asserting the two halves are equal is what catches the half-grant; asserting
    each covers the tools is what catches the missing grant.

    NEITHER SIDE IS ALLOWED TO BE EMPTY. A check whose expected set is derived can pass by
    deriving nothing -- a renamed constant, a restructured statement, a bucket constant that
    stopped matching -- and this repository has found four of those today. Each helper
    asserts it read something, so a parse that silently stops working fails here rather than
    reporting agreement between two empty sets.
    """
    required = synced_lineage_prefixes(workflow)
    fetchable = fetchable_lineage_prefixes(role)
    listable = listable_lineage_prefixes(role)

    assert fetchable, "no s3:GetObject statement on the lineage bucket parsed out of the role"
    assert listable, "no s3:prefix condition on the lineage bucket parsed out of the role"

    assert required <= listable, (
        f"the scheduled tools sync {sorted(required - listable)} which the role cannot list. "
        "aws s3 sync lists first, so this is an access denial on the first call rather than "
        "a partial read. Add the prefix to ListLineageRecords in "
        "infra/iam/audit-reader-role.yaml and apply the stack from a laptop."
    )
    assert required <= fetchable, (
        f"the scheduled tools sync {sorted(required - fetchable)} which the role cannot "
        "fetch. Add a ReadRecords statement to infra/iam/audit-reader-role.yaml and apply "
        "the stack from a laptop."
    )
    assert fetchable == listable, (
        f"the role can fetch {sorted(fetchable)} and list {sorted(listable)}. A prefix in one "
        "and not the other is granted on paper and refused in practice, which is the shape "
        "that reads as fine in a policy review."
    )


def test_the_lambda_grant_names_the_functions_the_templates_declare(
    role: dict[str, Any],
) -> None:
    """Mutation: widen the resource to `function:*`, or to the project's own name prefix.

    Either still reads as scoped and neither is. This is a shared sandbox account with
    sixteen other teams in it, and a function's configuration carries its environment
    variable names, its role and its layers, so an unscoped describe is an audit
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

    # Accumulated rather than assigned, because one function is now named by two statements
    # and a dict comprehension would keep whichever was written last -- reporting the notifier
    # as holding only its invoke grant and asserting nothing about the read.
    reach: dict[str, set[str]] = {}
    for statement in statements(role):
        for reachable in statement_resources(statement):
            if ":lambda:" in reachable:
                named = reachable.rsplit(":function:", 1)[1]
                reach.setdefault(named, set()).update(statement_actions(statement))

    assert set(reach) == declared

    # EVERY FUNCTION IS READ AND EXACTLY ONE IS INVOKED. The invoke arrived from the
    # 2026-08-06 outage, where reading the deployed bytes of all four said nothing about a
    # notifier that raised on every invocation. It is confined to the notifier because that
    # is the only one of the four with a harmless payload: a non-terminal Batch event owes no
    # message, so the handler loads its catalogs, reads its secret, projects the event and
    # posts nothing. The other three admit submissions, write lineage, or stop machines.
    invocable = {name for name, actions in reach.items() if "lambda:InvokeFunction" in actions}
    assert invocable == {"sbsandbox-intern-edullm-notifier"}
    assert all("lambda:GetFunctionConfiguration" in actions for actions in reach.values())
    assert all(
        actions <= {"lambda:GetFunctionConfiguration", "lambda:InvokeFunction"}
        for actions in reach.values()
    )
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
    the role by adding a job to audit.yml and dispatching it from the branch.

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
    one workflow file, so a token minted for `audit.yml` matched none of them and adding a
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
