"""What a submission does about the last W&B credential verdict the audit published.

The decision is a pure function of the published report and the clock, which is deliberate:
the surrounding tool talks to the GitHub API and nothing about how a stale acceptance is
treated should have to be established by mocking one.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from workflow_support import write_stub

from edullm_platform.wandb_preflight import (
    AUDIT_BRANCH,
    AUDIT_VERDICT_ARTIFACT,
    AUDIT_VERDICT_FILENAME,
    AUDIT_WORKFLOW,
    CHECKED_AT_FIELD,
    FRESHNESS,
    VERDICT_FIELD,
    Outcome,
    Verdict,
    decide,
    read_checked_at,
    read_verdict,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TOOL = PROJECT_ROOT / "tools" / "verify_wandb_preflight.py"

NOW = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)


def published(verdict: str, *, age: timedelta, **rest: Any) -> dict[str, Any]:
    return {
        VERDICT_FIELD: verdict,
        CHECKED_AT_FIELD: (NOW - age).isoformat(),
        "looks_wrong": [],
        "secret": "sbsandbox-intern-edullm-wandb-api-key",
        **rest,
    }


# ----------------------------------------------------------------------------------------
# The decision
# ----------------------------------------------------------------------------------------


def test_a_recent_acceptance_lets_the_submission_through() -> None:
    answer = decide(report=published("accepted", age=timedelta(hours=5)), now=NOW)

    assert answer.outcome is Outcome.PROCEED
    assert answer.verdict is Verdict.ACCEPTED
    assert not answer.refuses


def test_a_refusal_stops_the_submission() -> None:
    """THE ONE THAT MATTERS, and the one this whole change exists to make possible.

    Before it, a key W&B had already refused made the audit red and stopped nothing: the
    header of audit.yml says there is no alerting and a red scheduled run is the entire
    signal, so every dispatch went on allocating a GPU against a credential nobody could
    use, and eight of the nine failures that produced were filed as torch bugs.
    """
    answer = decide(report=published("refused", age=timedelta(hours=2)), now=NOW)

    assert answer.outcome is Outcome.REFUSE
    assert answer.refuses
    assert "would refuse" in answer.sentence


def test_a_refusal_is_honoured_however_old_it_is() -> None:
    """Mutation: age a refusal out the way an acceptance ages out.

    The asymmetry is the design rather than an oversight. A measured refusal does not stop
    being one because a schedule slipped, and the only thing that should clear it is a newer
    measurement -- which is one dispatch of the audit, and is what infra/README.md tells
    whoever repairs the key to do.
    """
    answer = decide(report=published("refused", age=FRESHNESS * 10), now=NOW)

    assert answer.outcome is Outcome.REFUSE


def test_an_acceptance_older_than_the_bound_establishes_nothing() -> None:
    """Mutation: keep trusting it, or refuse on it. Both are wrong in opposite directions.

    Trusting it forever means an audit that quietly stopped running leaves a check that
    reports a pass from an unbounded past. Refusing on it makes every submission depend on
    the health of a scheduled workflow with six other jobs in it, which is the coupling the
    exit-code separation exists to avoid.
    """
    answer = decide(report=published("accepted", age=FRESHNESS + timedelta(minutes=1)), now=NOW)

    assert answer.outcome is Outcome.NOT_ESTABLISHED
    assert answer.reason == "wandb_verdict_stale"
    assert not answer.refuses
    # The age is in the sentence, because the reader's next question is how far behind it is.
    assert "36h01m" in answer.sentence


def test_an_acceptance_exactly_at_the_bound_is_still_current() -> None:
    answer = decide(report=published("accepted", age=FRESHNESS), now=NOW)

    assert answer.outcome is Outcome.PROCEED


def test_an_outage_at_the_check_is_not_a_bad_key() -> None:
    """Mutation: fold `unreachable` into `refused`.

    Then a W&B outage overnight becomes a platform outage the following morning, and the
    refusal tells every submitter their key is broken when nobody has established anything.
    """
    answer = decide(report=published("unreachable", age=timedelta(hours=1)), now=NOW)

    assert answer.outcome is Outcome.NOT_ESTABLISHED
    assert answer.reason == "wandb_verdict_inconclusive"


def test_no_published_verdict_lets_the_submission_through() -> None:
    """THE STATE THIS SHIPS IN. Mutation: fail closed on a missing verdict.

    Nothing has published one until the first audit after this merges, so failing closed
    here would refuse every submission on the platform in order to add a check to them.
    """
    answer = decide(report=None, now=NOW)

    assert answer.outcome is Outcome.NOT_ESTABLISHED
    assert answer.reason == "wandb_verdict_not_published"
    assert AUDIT_WORKFLOW in answer.sentence


@pytest.mark.parametrize(
    "report",
    [
        pytest.param({CHECKED_AT_FIELD: NOW.isoformat()}, id="no verdict at all"),
        pytest.param(
            {VERDICT_FIELD: "probably-fine", CHECKED_AT_FIELD: NOW.isoformat()},
            id="a word this does not know",
        ),
        pytest.param({VERDICT_FIELD: "accepted"}, id="no timestamp"),
        pytest.param(
            {VERDICT_FIELD: "accepted", CHECKED_AT_FIELD: "2026-08-02T12:00:00"},
            id="a timestamp with no offset",
        ),
        pytest.param(
            {VERDICT_FIELD: "accepted", CHECKED_AT_FIELD: "the small hours"},
            id="not a timestamp",
        ),
    ],
)
def test_a_report_this_cannot_read_establishes_nothing(report: dict[str, Any]) -> None:
    """Mutation: default the missing half rather than declining to decide.

    A report written by a tool older than the verdict field, or by an ``--offline`` run, has
    no answer from W&B in it. Reading one as an acceptance would be the preflight reporting
    a pass it has no evidence for, and reading one as a refusal would stop the platform on
    the strength of a field somebody had not added yet.

    A timestamp carrying no offset is in the list for the same reason. An age computed
    across two machines' idea of local noon is not an age.
    """
    answer = decide(report=report, now=NOW)

    assert answer.outcome is Outcome.NOT_ESTABLISHED
    assert answer.reason == "wandb_verdict_unreadable"


def test_the_verdict_and_the_timestamp_are_read_rather_than_assumed() -> None:
    assert read_verdict({VERDICT_FIELD: "refused"}) is Verdict.REFUSED
    assert read_verdict({VERDICT_FIELD: 7}) is None
    assert read_verdict({}) is None
    assert read_checked_at({CHECKED_AT_FIELD: NOW.isoformat()}) == NOW
    assert read_checked_at({CHECKED_AT_FIELD: None}) is None


# ----------------------------------------------------------------------------------------
# The tool around it
# ----------------------------------------------------------------------------------------


def load() -> Any:
    specification = importlib.util.spec_from_file_location("verify_wandb_preflight", TOOL)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def stub_gh(tmp_path: Path, *, runs: list[int], reports: dict[int, str]) -> None:
    """A ``gh`` that answers the two calls this tool makes and refuses everything else."""
    cases = "\n".join(
        f"  {run_id}) printf %s {report!r} > "
        f'"${{destination}}/{AUDIT_VERDICT_FILENAME}"; exit 0;;'
        for run_id, report in reports.items()
    )
    listed = "\\n".join(str(run_id) for run_id in runs)
    write_stub(
        tmp_path / "bin",
        "gh",
        f"""
if [[ "$1" == "api" ]]; then
  printf '{listed}\\n'
  exit 0
fi
[[ "$1" == "run" && "$2" == "download" ]] || exit 64
run_id="$3"
destination=""
while [[ $# -gt 0 ]]; do
  if [[ "$1" == "--dir" ]]; then destination="$2"; fi
  shift
done
case "${{run_id}}" in
{cases}
  *) exit 1;;
esac
""",
    )


def run_tool(tmp_path: Path, *, runs: list[int], reports: dict[int, str]) -> Any:
    stub_gh(tmp_path, runs=runs, reports=reports)
    return subprocess.run(
        [sys.executable, str(TOOL), "--repository", "edu-llm/platform"],
        cwd=tmp_path,
        env={
            "PATH": f"{tmp_path / 'bin'}{os.pathsep}{os.defpath}",
            "PYTHONPATH": str(PROJECT_ROOT / "src"),
        },
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


def test_the_tool_separates_a_finding_from_an_unanswered_question() -> None:
    """Three exit codes, because there are three answers and two must not read alike."""
    module = load()

    assert (module.EXIT_PROCEED, module.EXIT_REFUSE, module.EXIT_NOT_ESTABLISHED) == (0, 1, 2)


def test_the_tool_reads_the_newest_run_that_actually_published_one(tmp_path: Path) -> None:
    """Mutation: read only the newest run, or the newest artifact of any branch.

    An audit that failed before its check ran publishes nothing, and the answer from the
    night before is then genuinely the newest one anybody has. Falling back to it is right;
    the freshness bound is what stops the fallback going on for ever.
    """
    finished = run_tool(
        tmp_path,
        runs=[300, 200, 100],
        reports={200: json.dumps(published("refused", age=timedelta(hours=1)))},
    )

    assert finished.returncode == 1, finished.stderr
    answer = json.loads(finished.stdout)
    assert answer["audit_run_id"] == 200
    assert answer["reason"] == "wandb_credential_would_be_refused"


def test_a_run_nobody_published_a_verdict_from_is_not_a_verdict(tmp_path: Path) -> None:
    finished = run_tool(tmp_path, runs=[300, 200], reports={})

    assert finished.returncode == 2, finished.stderr
    assert json.loads(finished.stdout)["reason"] == "wandb_verdict_not_published"


def test_the_tool_asks_only_for_runs_on_the_branch_the_role_is_pinned_to() -> None:
    """Mutation: drop the branch filter, which looks like a tidiness detail and is not.

    ``sbsandbox-intern-edullm-audit-reader`` pins its subject to ``ref:refs/heads/main``,
    so a dispatch of the audit from a branch cannot assume it and cannot read the secret
    at all. A verdict from one would be a statement produced by a tool somebody edited on
    that branch, about a value it never saw.
    """
    source = TOOL.read_text(encoding="utf-8")

    assert "branch={branch}" in source
    assert load().build_parser().get_default("branch") == AUDIT_BRANCH
    assert AUDIT_BRANCH == "main"


def test_the_tool_makes_no_aws_call_and_names_no_aws_identity() -> None:
    """THE DECISION, HELD TO A TEST. Mutation: read the secret here after all.

    Three principals hold ``secretsmanager:GetSecretValue`` on the W&B secret and none of
    them is reachable from submit-run.yml, deliberately. This tool exists so the submit path
    can act on that check without holding it, and the moment it reaches AWS the whole
    argument in infra/iam/admission-role.yaml stops being true.
    """
    source = TOOL.read_text(encoding="utf-8")

    assert "boto3" not in source
    assert "secretsmanager" not in source.replace("secretsmanager:GetSecretValue", "")
    assert '"aws"' not in source
    assert AUDIT_VERDICT_ARTIFACT in source or "AUDIT_VERDICT_ARTIFACT" in source
