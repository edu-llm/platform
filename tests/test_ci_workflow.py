"""Where each automated check lives, and what stops it drifting back.

There are two workflows and the split between them is the point. ``ci.yml`` runs on every
change and everything in it can fail a build. ``nightly.yml`` runs on a schedule and holds
the checks that answer a question about the repository or about the account rather than
about the change: the two acceptance gates, which read evidence that expires; the
reproduction of the recorded suite results, which runs the suite inside the suite; and two
that read the account, where the thing being described was true before the change existed.

What those last two need and the first three do not is an AWS identity, so the permission
to mint one is declared per job here rather than for the file. ``tests/test_nightly_workflow.py``
holds the two checks themselves; this module holds the split.

Both gates were pull-request jobs and both were ``continue-on-error``, because a gate that
reads expiring evidence can go red for reasons unrelated to the change under review. The
file argued that running them per pull request was what made an expiry surface on an
ordinary morning. It would not have. Both pass today and the Phase 1 evidence does not
lapse until 2026-08-25, so the arrangement has never been tested; when it is, the job will
report a red cross that merges anyway, which is a thing a reviewer learns to scroll past.
A scheduled run that can actually fail does the job that comment wanted done, so these
tests now pin the opposite arrangement — nothing in ``ci.yml`` may be informational, and
nothing in ``nightly.yml`` may be either.

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
NIGHTLY_PATH = WORKFLOWS_ROOT / "nightly.yml"

#: Each acceptance gate, by the job that runs it and the script it runs. The job ids say
#: what the gate checks; they used to say which phase built it, which told a reader
#: looking at a red cross nothing they needed. The script filenames are cited by the
#: committed proof bundles as the command that reproduces a verdict, and by
#: ``tools/validate_phase1.py``'s appearance here, so those do not get to change with the
#: job names.
GATE_JOBS = {
    "contract-and-manifest-gate": "tools/validate_phase0.py",
    "branch-to-image-gate": "tools/validate_phase1.py",
}

#: The scheduled jobs that read the account rather than the tree, and so the only ones that
#: may mint an OIDC token. Named here rather than derived, because the point of the list is
#: that a job joining it is a review of this line.
CREDENTIALED_NIGHTLY_JOBS = frozenset({"checkpoint-reconciliation", "wandb-credential"})

#: The contexts pinned in branch protection on ``main``. They are job names, so renaming
#: either one silently stops the protection matching anything.
REQUIRED_CHECK_NAMES = {"checks (python 3.12)", "checks (python 3.13)"}

#: What asks the suite for the expensive half. Duplicated from ``tests/proof_support.py``
#: on purpose: this is the workflow's side of the contract, and a test that read the same
#: constant from the same place would pass while the two had drifted apart.
REPRODUCE_ENV = "EDULLM_REPRODUCE_PROOFS"


def _load_workflow(path: Path = WORKFLOW_PATH) -> dict[str, Any]:
    return load_workflow(path)


def test_every_expression_names_something_that_actually_exists() -> None:
    assert unreal_context_references(WORKFLOW_PATH) == []
    assert unreal_context_references(NIGHTLY_PATH) == []


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
    assert set(checks["strategy"]["matrix"]["python-version"]) == {"3.12", "3.13"}
    rendered = {
        checks["name"].replace("${{ matrix.python-version }}", version)
        for version in checks["strategy"]["matrix"]["python-version"]
    }
    assert rendered == REQUIRED_CHECK_NAMES


def test_nothing_on_the_pull_request_path_is_unable_to_fail() -> None:
    # continue-on-error on a pull-request job means the job reports a red cross that
    # merges anyway, which reads to everybody as a check and to GitHub as nothing. Both
    # jobs that had it are now in nightly.yml, where they can fail.
    workflow = _load_workflow()

    assert workflow["on"]["push"] == {"branches": ["main"]}
    assert "pull_request" in workflow["on"]
    for job_id, job in workflow["jobs"].items():
        assert "continue-on-error" not in job, f"{job_id} cannot fail a build"


def test_neither_acceptance_gate_still_runs_on_every_change() -> None:
    # Stated as its own test rather than folded into the one above, because the two say
    # different things: nothing in ci.yml may be informational, and these two in
    # particular may not come back as informational jobs under new names.
    text = WORKFLOW_PATH.read_text(encoding="utf-8")

    for gate_script in GATE_JOBS.values():
        assert gate_script not in text, (
            f"{gate_script} is back on the pull-request path. It reads evidence that "
            "expires, so it can only live there by being unable to fail, which is the "
            "arrangement nightly.yml replaced."
        )


def test_the_nightly_run_is_scheduled_and_can_be_started_by_hand() -> None:
    # The schedule is what makes an expiry surface without anybody asking. The manual
    # trigger is what makes the job usable the morning it goes red, when the question is
    # whether a fix worked and waiting a day is not an answer.
    workflow = _load_workflow(NIGHTLY_PATH)

    assert "workflow_dispatch" in workflow["on"]
    crons = [entry["cron"] for entry in workflow["on"]["schedule"]]
    assert len(crons) == 1
    assert re.fullmatch(r"\d+ \d+ \* \* \*", crons[0]), "the schedule must be daily"


def test_both_acceptance_gates_run_nightly_and_can_fail_the_run() -> None:
    # The whole reason for moving them. A gate that reads expiring evidence cannot be a
    # required check without blocking unrelated work the day the evidence lapses, and it
    # is worth nothing as a job that cannot fail. Scheduled and failing is the third
    # option, and it is the one that makes the expiry visible.
    workflow = _load_workflow(NIGHTLY_PATH)

    assert GATE_JOBS.keys() <= workflow["jobs"].keys()
    for job_id, gate_script in GATE_JOBS.items():
        job = workflow["jobs"][job_id]
        commands = [step["run"] for step in job["steps"] if "run" in step]

        assert [command for command in commands if gate_script in command] == [
            f"uv run --frozen python {gate_script}"
        ]
        assert "continue-on-error" not in job, f"{job_id} must be able to fail the run"
        assert "needs" not in job, f"{job_id} must not be skipped by a failure elsewhere"


def test_each_gate_job_is_named_for_what_it_checks() -> None:
    # The jobs were called "phase 0 gate" and "phase 1 gate", which named when the thing
    # was built rather than what it checks. Somebody reading a red cross at 09:00 needs
    # the second. The scripts keep their phase-numbered filenames because committed proof
    # bundles cite them as the command that reproduces a verdict.
    workflow = _load_workflow(NIGHTLY_PATH)

    names = {job_id: workflow["jobs"][job_id]["name"] for job_id in GATE_JOBS}

    assert names == {
        "contract-and-manifest-gate": "contract and manifest compilation gate",
        "branch-to-image-gate": "branch to image gate",
    }
    for job_id, name in names.items():
        assert "phase" not in name, f"{job_id} is named for when it was built"
        assert "informational" not in name, f"{job_id} is no longer informational"


def test_the_nightly_run_reproduces_what_the_pull_request_path_skips() -> None:
    # The pull-request suite skips the nested runs, so something has to perform them or
    # the generators stop being exercised at all. This is that something, it runs the
    # whole suite rather than a selection, and it asks for the expensive half by the same
    # variable tests/proof_support.py reads.
    workflow = _load_workflow(NIGHTLY_PATH)
    job = workflow["jobs"]["proof-bundles-reproduce"]
    steps = [step for step in job["steps"] if "run" in step and " pytest" in step["run"]]

    assert len(steps) == 1
    assert steps[0]["run"] == "uv run --frozen pytest -q"
    assert steps[0]["env"] == {REPRODUCE_ENV: "1"}
    assert "continue-on-error" not in job


def test_only_the_jobs_that_read_the_account_can_reach_it() -> None:
    # This said no scheduled job could reach AWS at all, which was true while every check
    # here read committed records. Two now ask the account a question no committed file
    # answers: whether the runs that promised a checkpoint have one, and whether the stored
    # W&B key is one W&B accepts.
    #
    # The claim worth keeping is the narrower one, and it is stronger than a file-wide
    # string search was. id-token is declared per job rather than for the file, so a gate
    # that started re-capturing live evidence -- which is exactly what somebody would be
    # tempted to put in a scheduled job -- cannot mint a token without adding a permissions
    # block, and adding one fails here.
    workflow = _load_workflow(NIGHTLY_PATH)

    assert workflow["permissions"] == {"contents": "read"}
    for job_id, job in workflow["jobs"].items():
        if job_id in CREDENTIALED_NIGHTLY_JOBS:
            assert job["permissions"] == {"contents": "read", "id-token": "write"}, job_id
        else:
            assert "permissions" not in job, f"{job_id} reads committed records and needs none"


def test_no_scheduled_job_takes_a_secret_or_names_the_account() -> None:
    # Unchanged in force by the two credentialed jobs. Both assume a role by OIDC and read
    # its ARN from a repository variable, so there is still no long-lived credential in
    # this file and still no account id to read off it.
    workflow_text = NIGHTLY_PATH.read_text(encoding="utf-8")

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


def test_the_nightly_file_says_why_each_check_is_not_on_the_pull_request_path() -> None:
    # The reasoning lives in the file because the arrangement looks like an oversight
    # from the outside: five checks nobody has to pass in order to merge. The next
    # person to notice should find the argument rather than reconstruct it.
    #
    # Read off the workflow rather than from a list here, so a job added later has to be
    # argued for in the header instead of merely not being on a list somebody forgot.
    header = NIGHTLY_PATH.read_text(encoding="utf-8").split("\non:", 1)[0].lower()

    assert "continue-on-error" in header, "say why nothing here is informational"
    assert "expire" in header, "say why the gates cannot sit on the pull-request path"
    assert "required" in header, "say what does block a merge"
    for job_id in _load_workflow(NIGHTLY_PATH)["jobs"]:
        assert job_id in header, f"{job_id} is not accounted for in the header"
