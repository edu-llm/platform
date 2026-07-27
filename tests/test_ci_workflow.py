"""What the CI workflow has to keep doing for the acceptance gates to stay honest.

Each gate reads committed evidence that expires. Running it only by hand means the
expiry lands as nothing at all: the bundle keeps reading as proven until somebody
happens to type the command. These tests pin the two properties that make CI the thing
that notices instead — the gates run on every change, and they run without credentials.

This module names the gate commands, so the textual marker that finds gate-invoking test
modules matches it and it is listed in ``REENTRANT_TEST_MODULES``. It never starts a
gate; the listing only means no criterion may cite it, which is a citation nobody wants.
"""

import re
from typing import Any

from workflow_support import WORKFLOWS_ROOT, load_workflow, unreal_context_references

WORKFLOW_PATH = WORKFLOWS_ROOT / "ci.yml"
GATE_JOBS = {
    "gate": "tools/validate_phase0.py",
    "phase1-gate": "tools/validate_phase1.py",
}


def _load_workflow() -> dict[str, Any]:
    return load_workflow(WORKFLOW_PATH)


def _job_comment(job_id: str) -> str:
    """The comment lines written inside one job block, in file order."""
    lines = WORKFLOW_PATH.read_text(encoding="utf-8").splitlines()
    opening = [index for index, line in enumerate(lines) if line == f"  {job_id}:"]
    assert len(opening) == 1, f"expected exactly one job named {job_id}"

    comments = []
    for line in lines[opening[0] + 1 :]:
        if re.fullmatch(r"  [A-Za-z0-9_-]+:", line):
            break
        stripped = line.strip()
        if stripped.startswith("#"):
            comments.append(stripped.lstrip("#").strip())
    return " ".join(comments)


def test_every_expression_names_something_that_actually_exists() -> None:
    assert unreal_context_references(WORKFLOW_PATH) == []


def test_both_acceptance_gates_run_on_every_change() -> None:
    # An expiry date nobody is watching is only a red job if the gate runs unasked. Both
    # triggers matter: the pull request catches the change, the push to main catches the
    # ordinary morning on which nothing changed but the evidence went stale.
    workflow = _load_workflow()

    assert workflow["on"]["push"] == {"branches": ["main"]}
    assert "pull_request" in workflow["on"]
    assert GATE_JOBS.keys() <= workflow["jobs"].keys()

    for job_id, gate_script in GATE_JOBS.items():
        job = workflow["jobs"][job_id]
        commands = [step["run"] for step in job["steps"] if "run" in step]
        assert [command for command in commands if gate_script in command] == [
            f"uv run --frozen python {gate_script}"
        ]
        assert "needs" not in job, f"{job_id} must not be skipped by a failure elsewhere"


def test_both_acceptance_gates_are_informational_and_say_why() -> None:
    # Informational is a deliberate tradeoff, not an oversight: a gate reads evidence that
    # legitimately expires, so it can go red for reasons that have nothing to do with the
    # change under review, and a required check that does that gets routed around. The
    # reasoning lives in the file so that nobody later "fixes" it into a required check.
    workflow = _load_workflow()

    for job_id in GATE_JOBS:
        job = workflow["jobs"][job_id]
        comment = _job_comment(job_id).lower()

        assert job["continue-on-error"] is True, f"{job_id} must not block a merge"
        assert "informational" in job["name"]
        assert "not a required check" in comment
        assert "expire" in comment
        assert "test suite" in comment, f"{job_id} must say what does block a merge"


def test_neither_gate_can_reach_aws_or_a_secret() -> None:
    # The gates read committed records, so they need no credentials. Saying so here means
    # a later attempt to make one of them re-capture live evidence has to change a test.
    workflow = _load_workflow()
    workflow_text = WORKFLOW_PATH.read_text(encoding="utf-8")

    assert workflow["permissions"] == {"contents": "read"}
    for job_id in GATE_JOBS:
        assert "permissions" not in workflow["jobs"][job_id]

    assert not re.search(r"\$\{\{[^}]*secrets\.", workflow_text)
    assert "aws-actions/" not in workflow_text
    assert "id-token" not in workflow_text
    assert not re.search(r"(?<!\d)\d{12}(?!\d)", workflow_text)
