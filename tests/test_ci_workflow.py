"""Where each automated check lives, and what stops it drifting back.

There are two workflows and the split between them is the point. ``ci.yml`` runs on every
change and everything in it can fail a build. ``audit.yml`` runs on a schedule and holds
the checks that answer a question about the repository or about the account rather than
about the change: the two acceptance gates, which read evidence that expires; the
reproduction of the recorded suite results, which runs the suite inside the suite; and two
that read the account, where the thing being described was true before the change existed.

What those last two need and the first three do not is an AWS identity, so the permission
to mint one is declared per job here rather than for the file. ``tests/test_audit_workflow.py``
holds the two checks themselves; this module holds the split.

Both gates were pull-request jobs and both were ``continue-on-error``, because a gate that
reads expiring evidence can go red for reasons unrelated to the change under review. The
file argued that running them per pull request was what made an expiry surface on an
ordinary morning. It would not have. Both pass today and the Phase 1 evidence does not
lapse until 2026-08-25, so the arrangement has never been tested; when it is, the job will
report a red cross that merges anyway, which is a thing a reviewer learns to scroll past.
A scheduled run that can actually fail does the job that comment wanted done, so these
tests now pin the opposite arrangement — nothing in ``ci.yml`` may be informational, and
nothing in ``audit.yml`` may be either.

This module names the gate commands, so the textual marker that finds gate-invoking test
modules matches it and it is listed in ``REENTRANT_TEST_MODULES``. It never starts a gate;
the listing only means no criterion may cite it, which is a citation nobody wants.
"""

import re
import tomllib
from pathlib import Path
from typing import Any

from workflow_support import WORKFLOWS_ROOT, load_workflow, unreal_context_references

WORKFLOW_PATH = WORKFLOWS_ROOT / "ci.yml"
AUDIT_PATH = WORKFLOWS_ROOT / "audit.yml"

#: The scheduled jobs that read the account rather than the tree, and so the only ones that
#: may mint an OIDC token. Named here rather than derived, because the point of the list is
#: that a job joining it is a review of this line.
CREDENTIALED_AUDIT_JOBS = frozenset(
    {
        "checkpoint-reconciliation",
        "wandb-credential",
        "deployed-lambda-release",
        "deployed-stack-templates",
        "visibility-board",
        "placement-verdicts",
        "substrate-capture",
        "roster-against-the-account",
    }
)

#: The scheduled jobs that write to this repository, which is a shorter list than the one
#: above and must never overlap it. A job holding both a credential into the account and a
#: write into this repository could publish a reading it invented, which is the same argument
#: ``infra/iam/audit-reader-role.yaml`` makes for the reader role holding no write in the
#: account: a check able to change what it is checking can produce its own all-clear.
PUBLISHING_AUDIT_JOBS = frozenset({"substrate-history"})

#: The scheduled jobs that read this repository's own issues, which is a third thing a job
#: here can be and the only one that reaches neither the account nor a write. Declared as a
#: category rather than waved through, because ``issues: read`` is still a widening of the
#: file-level ``contents: read`` and the reason it is safe is specific: an issue in this
#: repository is a thing somebody typed here, so a reader of them can invent no evidence about
#: the account and can change nothing at all.
#:
#: STATED EXPLICITLY RATHER THAN RELIED ON. The endpoint is readable on a public repository
#: without it today, which is an argument for leaving it off and a bad one -- the permission
#: the endpoint documents is the permission to ask for, and a repository that goes private
#: later should not take a scheduled job down with it.
ISSUE_READING_AUDIT_JOBS = frozenset({"open-asks"})

#: The contexts pinned in branch protection on ``main``. They are job names, so renaming
#: either one silently stops the protection matching anything.
#:
#: A SUBSET OF THE MATRIX RATHER THAN THE WHOLE OF IT, WHICH IT USED TO BE. The matrix also
#: runs 3.14, because ``requires-python`` lets somebody install on it and ``uv tool
#: install`` fetches whatever is newest, so it is a version researchers are on whether or
#: not anybody chose it. Pinning it in branch protection is a decision for whoever
#: administers the repository and is not one a test can make, so what this can hold is that
#: the two that are pinned still exist under the names the protection spells.
REQUIRED_CHECK_NAMES = {"checks (python 3.12)", "checks (python 3.13)"}

#: The floor ``pyproject.toml`` declares, and the ceiling ``requires-python`` leaves open.
#: Read here rather than derived from the matrix, because a matrix that lost a version
#: would otherwise agree with itself.
TESTED_PYTHON_VERSIONS = {"3.12", "3.13", "3.14"}


def _load_workflow(path: Path = WORKFLOW_PATH) -> dict[str, Any]:
    return load_workflow(path)


def test_every_expression_names_something_that_actually_exists() -> None:
    assert unreal_context_references(WORKFLOW_PATH) == []
    assert unreal_context_references(AUDIT_PATH) == []


def test_the_test_job_runs_every_test_and_groups_the_workers() -> None:
    # Two properties, and the second is the one that is easy to get wrong. The default
    # per-test distribution would put one module's tests on several workers and have each
    # of them build its own copy of that module's session fixtures. loadgroup, with the
    # groups tests/conftest.py assigns, keeps each module whole and keeps the four
    # generators that share a collection together. The first property is the plainer one:
    # parallelism may make the suite finish sooner, never make it a subset.
    workflow = _load_workflow()
    commands = [step["run"] for step in workflow["jobs"]["checks"]["steps"] if "run" in step]
    tests = [command for command in commands if " pytest" in command]

    assert tests == ["uv run --frozen pytest -q -n4 --dist loadgroup"]
    assert "-m" not in tests[0].split(), "CI must not run a subset of the suite"


def test_the_distribution_flags_are_not_a_configured_default() -> None:
    # A local run stays serial, because a parallel one interleaves output and reorders
    # failures, and the person reading it is usually reading it because something broke.
    settings = tomllib.loads(
        (WORKFLOW_PATH.parents[2] / "pyproject.toml").read_text(encoding="utf-8")
    )
    assert "addopts" not in settings["tool"]["pytest"]["ini_options"]


def test_the_required_checks_are_still_called_what_protection_calls_them() -> None:
    # Branch protection pins job names, not job ids, and matches them as strings. A
    # rename here does not fail anything: it makes the required check stop existing, and
    # a pull request merges without it having run.
    workflow = _load_workflow()
    checks = workflow["jobs"]["checks"]

    assert checks["name"] == "checks (python ${{ matrix.python-version }})"
    assert set(checks["strategy"]["matrix"]["python-version"]) == TESTED_PYTHON_VERSIONS
    rendered = {
        checks["name"].replace("${{ matrix.python-version }}", version)
        for version in checks["strategy"]["matrix"]["python-version"]
    }
    assert REQUIRED_CHECK_NAMES <= rendered


def test_nothing_on_the_pull_request_path_is_unable_to_fail() -> None:
    # continue-on-error on a pull-request job means the job reports a red cross that
    # merges anyway, which reads to everybody as a check and to GitHub as nothing. Both
    # jobs that had it are now in audit.yml, where they can fail.
    workflow = _load_workflow()

    assert workflow["on"]["push"] == {"branches": ["main"]}
    assert "pull_request" in workflow["on"]
    for job_id, job in workflow["jobs"].items():
        assert "continue-on-error" not in job, f"{job_id} cannot fail a build"


def test_no_workflow_runs_a_phase_acceptance_gate() -> None:
    """Mutation: bring one of the six gate scripts back under a new job name.

    The phase model the gates reported against was replaced by the slice plans, and their
    evidence was void: twelve of the thirteen run ids the bundles cited named container
    images an ECR lifecycle rule had already deleted. A gate reintroduced here would be a
    green cross over a claim nothing can check.
    """
    for path in (WORKFLOW_PATH, AUDIT_PATH):
        text = path.read_text(encoding="utf-8")
        for phase in range(6):
            assert f"tools/validate_phase{phase}.py" not in text
            assert f"tools/build_phase{phase}_proof.py" not in text


def test_the_audit_is_scheduled_and_can_be_started_by_hand() -> None:
    # The schedule is what makes an expiry surface without anybody asking. The manual
    # trigger is what makes the job usable the morning it goes red, when the question is
    # whether a fix worked and waiting a day is not an answer.
    workflow = _load_workflow(AUDIT_PATH)

    assert "workflow_dispatch" in workflow["on"]
    crons = [entry["cron"] for entry in workflow["on"]["schedule"]]
    assert len(crons) == 1
    assert re.fullmatch(r"\d+ \d+ \* \* \*", crons[0]), "the schedule must be daily"


def test_only_the_jobs_that_read_the_account_can_reach_it() -> None:
    # This said no scheduled job could reach AWS at all, which was true while every check
    # here read committed records. Five now ask the account a question no committed file
    # answers: whether the runs that promised a checkpoint have one, whether the stored W&B
    # key is one W&B accepts, whether the two admission functions are running the code their
    # release records describe, whether each deployed stack is the template main declares, and
    # whether what the account is running, what W&B logged and what the outputs bucket holds
    # describe the same set of runs.
    #
    # The claim worth keeping is the narrower one, and it is stronger than a file-wide
    # string search was. id-token is declared per job rather than for the file, so a gate
    # that started re-capturing live evidence -- which is exactly what somebody would be
    # tempted to put in a scheduled job -- cannot mint a token without adding a permissions
    # block, and adding one fails here.
    workflow = _load_workflow(AUDIT_PATH)

    assert workflow["permissions"] == {"contents": "read"}
    for job_id, job in workflow["jobs"].items():
        if job_id in CREDENTIALED_AUDIT_JOBS:
            assert job["permissions"] == {"contents": "read", "id-token": "write"}, job_id
            continue
        if job_id in PUBLISHING_AUDIT_JOBS:
            # No id-token at all, so this job cannot obtain an AWS identity however its
            # steps are written, and the separation is a property of the token rather than
            # of anybody remembering not to add a credential step.
            assert job["permissions"] == {"contents": "write"}, job_id
            reaching = [step for step in job["steps"] if "aws-actions/" in step.get("uses", "")]
            assert reaching == [], f"{job_id} writes to this repository and must not read AWS"
            continue
        if job_id in ISSUE_READING_AUDIT_JOBS:
            # Read on this repository and nothing else. No id-token, so no AWS identity is
            # obtainable however the steps are written, and no write of any kind, so the job
            # cannot alter what it is counting.
            assert job["permissions"] == {"contents": "read", "issues": "read"}, job_id
            reaching = [step for step in job["steps"] if "aws-actions/" in step.get("uses", "")]
            assert reaching == [], f"{job_id} reads issues and must not read AWS"
            continue
        assert "permissions" not in job, f"{job_id} reads committed records and needs none"
        # The permission and the step are separate mutations. A gate that gained a
        # configure-aws-credentials without a permissions block fails at 05:00 rather than
        # here, and a reader of that failure has no reason to look at this list.
        reaching = [step for step in job["steps"] if "aws-actions/" in step.get("uses", "")]
        assert reaching == [], f"{job_id} reads committed records and needs no credential"


def test_no_scheduled_job_both_reads_the_account_and_writes_to_this_repository() -> None:
    """Mutation: give substrate-capture `contents: write` and drop the second job.

    It is the obvious simplification -- one job, three fewer steps, no artifact hand-off --
    and it puts a repository write on the only job here holding a credential into the
    account. A reader that could commit could publish a reading it invented, and the reading
    is the artifact everything downstream believes precisely because nobody can go back and
    check it against sources that have since forgotten.

    Asserted as a disjointness rather than as a property of the two names, so a third job of
    either kind is held to it without being added to a list.
    """
    assert not CREDENTIALED_AUDIT_JOBS & PUBLISHING_AUDIT_JOBS

    workflow = _load_workflow(AUDIT_PATH)
    for job_id, job in workflow["jobs"].items():
        permissions = job.get("permissions", {})
        writes = permissions.get("contents") == "write"
        mints = permissions.get("id-token") == "write"
        assert not (writes and mints), f"{job_id} can reach the account and rewrite the record"
        assert (job_id in PUBLISHING_AUDIT_JOBS) == writes, job_id
        assert (job_id in CREDENTIALED_AUDIT_JOBS) == mints, job_id


def test_no_scheduled_job_takes_a_secret_or_names_the_account() -> None:
    # Unchanged in force by the two credentialed jobs. Both assume a role by OIDC and read
    # its ARN from a repository variable, so there is still no long-lived credential in
    # this file and still no account id to read off it.
    workflow_text = AUDIT_PATH.read_text(encoding="utf-8")

    assert not re.search(r"\$\{\{[^}]*secrets\.", workflow_text)
    assert not re.search(r"(?<!\d)\d{12}(?!\d)", workflow_text)


def test_the_pull_request_path_cannot_reach_aws_or_a_secret_either() -> None:
    workflow = _load_workflow()
    workflow_text = WORKFLOW_PATH.read_text(encoding="utf-8")

    assert workflow["permissions"] == {"contents": "read"}
    for job_id in workflow["jobs"]:
        assert "permissions" not in workflow["jobs"][job_id]

    assert not re.search(r"\$\{\{[^}]*secrets\.", workflow_text)
    assert "aws-actions/" not in workflow_text
    assert "id-token" not in workflow_text
    assert not re.search(r"(?<!\d)\d{12}(?!\d)", workflow_text)


def test_the_audit_says_why_each_check_is_not_on_the_pull_request_path() -> None:
    # The reasoning lives in the file because the arrangement looks like an oversight
    # from the outside: five checks nobody has to pass in order to merge. The next
    # person to notice should find the argument rather than reconstruct it.
    #
    # Read off the workflow rather than from a list here, so a job added later has to be
    # argued for in the header instead of merely not being on a list somebody forgot.
    header = AUDIT_PATH.read_text(encoding="utf-8").split("\non:", 1)[0].lower()

    assert "continue-on-error" in header, "say why nothing here is informational"
    assert "account" in header, "say why these cannot sit on the pull-request path"
    assert "required" in header, "say what does block a merge"
    for job_id in _load_workflow(AUDIT_PATH)["jobs"]:
        assert job_id in header, f"{job_id} is not accounted for in the header"
